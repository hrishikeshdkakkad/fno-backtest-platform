"""Offline threshold tuner — grid-searches the four regime thresholds
against the cached `spread_trades.csv` to find the combo that maximises
Sharpe (or any other metric).

How it works:
  1. Load the backtested trades (`results/nfo/spread_trades.csv`).
  2. For every entry_date, enrich the row with regime signals computed
     from cached daily parquets:
       - rv_30d           (realized vol from NIFTY closes)
       - iv_minus_rv      (entry_iv − rv_30d in vol-pts)
       - vix              (actual India-VIX close at entry_date)
       - vix_pct_3mo      (rank of entry-date VIX in the prior ~60 daily
                          VIX closes — matches regime_watch's live ECDF)
       - pullback_atr     (pullback off 10d high in ATR units)
  3. Call `nfo.calibrate.grid_search_thresholds` and print the result.
  4. `--write` persists the winning combo to
     `results/nfo/tuned_thresholds.json` for `regime_watch.py` to load.

This script is 100 % offline — no Parallel or Dhan calls. It requires a
cached VIX parquet under `data/nfo/index/VIX_*.parquet`. Generate it via
`scripts/nfo/refresh_vix_cache.py` before tuning, or the tuner will fall
back to the legacy rolling-RV proxy for `vix_pct_3mo` and warn loudly.

Usage:
    .venv/bin/python scripts/nfo/tune_thresholds.py
    .venv/bin/python scripts/nfo/tune_thresholds.py --metric sortino --write
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from nfo import calibrate, signals
from nfo.config import DATA_DIR, RESULTS_DIR

INDEX_CACHE_DIR = DATA_DIR / "index"
SPREAD_TRADES_CSV = RESULTS_DIR / "spread_trades.csv"


def _load_index_daily(underlying: str = "NIFTY") -> pd.DataFrame:
    """Concatenate every cached index parquet and return a deduped daily frame."""
    rows: list[pd.DataFrame] = []
    for p in INDEX_CACHE_DIR.glob(f"{underlying}_*.parquet"):
        rows.append(pd.read_parquet(p))
    if not rows:
        raise FileNotFoundError(f"No cached {underlying} bars under {INDEX_CACHE_DIR}")
    df = pd.concat(rows, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    return df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)


def _load_vix_daily() -> pd.DataFrame | None:
    """Concat every cached `VIX_*.parquet`, dedupe by date. None if absent.

    Callers should check for None and warn/fall back — the tuner treats this
    as an optional input because older checkouts may not have a VIX cache yet.
    """
    try:
        df = _load_index_daily("VIX")
    except FileNotFoundError:
        return None
    return df if not df.empty else None


def _vix_pct_from_history(vix_at_d: float, prior_history: np.ndarray) -> float:
    """Empirical ECDF rank — same semantics as `regime_watch._vix_percentile`.

    `prior_history` must be strictly before `d`; we do not self-include to
    match the "where does today rank in history" question the live watcher
    asks. Returns NaN if the history is empty or `vix_at_d` is missing.
    """
    if not np.isfinite(vix_at_d) or prior_history.size == 0:
        return float("nan")
    return float(np.mean(prior_history <= vix_at_d))


def _enrich_trades(
    trades: pd.DataFrame,
    index_daily: pd.DataFrame,
    vix_daily: pd.DataFrame | None,
) -> pd.DataFrame:
    """Attach rv_30d / iv_minus_rv / vix / vix_pct_3mo / pullback_atr to each trade.

    When `vix_daily` is provided, `vix` and `vix_pct_3mo` are computed from
    actual India-VIX history — matching the distribution `regime_watch.py`
    grades against. When `vix_daily` is None we fall back to the legacy
    rolling-RV proxy so this script still runs without the VIX cache; the
    caller warns in that case because tuned thresholds won't be strictly
    comparable to live grading.
    """
    idx = index_daily.set_index("date")
    vix_idx = vix_daily.set_index("date") if vix_daily is not None else None
    out = trades.copy()
    out["entry_date"] = pd.to_datetime(out["entry_date"])

    def _vix_proxy_from_rv(closes_arr: np.ndarray, iv: float) -> float:
        """Legacy fallback: rank `iv` against 60 rolling-30d realized vols.

        Critical: the numerator / denominator slices below must be aligned by
        POSITION, not by pandas' default label alignment — otherwise the two
        iloc slices get matched on their preserved row labels and the division
        collapses to 1.0, making the log-returns zero.
        """
        lookback_rv: list[float] = []
        for k in range(60):
            if len(closes_arr) <= k + 31:
                continue
            end = -k if k > 0 else None
            num = closes_arr[-(k + 30):end]
            den = closes_arr[-(k + 31):-(k + 1)]
            if num.size != den.size or num.size < 2:
                continue
            rets = np.log(num / den)
            rets = rets[np.isfinite(rets)]
            if rets.size < 2:
                continue
            lookback_rv.append(float(rets.std(ddof=1) * np.sqrt(252) * 100.0))
        if iv and np.isfinite(iv) and lookback_rv:
            return float(np.mean(np.asarray(lookback_rv) <= iv))
        return float("nan")

    def per_row(row: pd.Series) -> pd.Series:
        d = row["entry_date"]
        hist = idx.loc[:d]
        if len(hist) < 30:
            return pd.Series({"rv_30d": np.nan, "iv_minus_rv": np.nan,
                              "vix": np.nan, "vix_pct_3mo": np.nan,
                              "pullback_atr": np.nan})
        closes = hist["close"].astype(float)
        # Realized vol (30d), annualized.
        rets = np.log(closes / closes.shift(1)).dropna().iloc[-30:]
        rv = float(rets.std() * np.sqrt(252) * 100.0)   # vol-pts

        iv = float(row.get("entry_iv", np.nan))
        iv_rv = iv - rv if np.isfinite(iv) else np.nan

        # Pullback in ATR units off the last 10-day high.
        last10 = hist.iloc[-10:]
        atr_series = signals.atr(hist.iloc[-60:].reset_index(), 14)
        atr_val = float(atr_series.iloc[-1]) if not atr_series.empty else np.nan
        spot = float(closes.iloc[-1])
        hi10 = float(last10["high"].max())
        pb_atr = signals.pullback_atr_scaled(spot, hi10, atr_val)

        # VIX features: prefer actual VIX when cached, else fall back to the
        # legacy proxy so pre-cache checkouts still tune (with a warning).
        if vix_idx is not None:
            vix_hist = vix_idx.loc[:d]
            if len(vix_hist) >= 2:
                vix_at_d = float(vix_hist["close"].iloc[-1])
                # Prior-60 distribution excludes today so the rank is
                # "where does today fall in the recent past," matching
                # regime_watch._vix_percentile.
                prior = vix_hist["close"].astype(float).iloc[-61:-1].to_numpy()
                vix_pct = _vix_pct_from_history(vix_at_d, prior)
            else:
                vix_at_d = float("nan")
                vix_pct = float("nan")
        else:
            vix_at_d = iv
            vix_pct = _vix_proxy_from_rv(closes.to_numpy(dtype=float), iv)

        return pd.Series({
            "rv_30d": rv, "iv_minus_rv": iv_rv,
            "vix": vix_at_d, "vix_pct_3mo": vix_pct,
            "pullback_atr": pb_atr,
        })

    enriched = out.apply(per_row, axis=1)
    return pd.concat([out.reset_index(drop=True), enriched.reset_index(drop=True)], axis=1)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trades-csv", type=Path, default=SPREAD_TRADES_CSV)
    p.add_argument("--underlying", default="NIFTY")
    p.add_argument("--metric", default="sharpe",
                   choices=["sharpe", "sortino", "win_rate", "avg_pnl_contract"])
    p.add_argument("--min-trades", type=int, default=5)
    p.add_argument("--write", action="store_true",
                   help="Persist winner to results/nfo/tuned_thresholds.json")
    p.add_argument("--also-pop-table", action="store_true",
                   help="Also build empirical POP table at results/nfo/empirical_pop.parquet")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("tune_thresholds")

    if not args.trades_csv.exists():
        log.error("Missing trades CSV: %s", args.trades_csv)
        return 1

    trades = pd.read_csv(args.trades_csv)
    log.info("Loaded %d trades from %s", len(trades), args.trades_csv)

    index_daily = _load_index_daily(args.underlying)
    log.info("Loaded %d %s daily bars (%s to %s)",
             len(index_daily), args.underlying,
             index_daily["date"].min().date(), index_daily["date"].max().date())

    vix_daily = _load_vix_daily()
    if vix_daily is not None:
        log.info("Loaded %d VIX daily bars (%s to %s)",
                 len(vix_daily),
                 vix_daily["date"].min().date(), vix_daily["date"].max().date())
    else:
        log.warning(
            "No cached VIX parquet under %s — falling back to the legacy "
            "rolling-RV proxy for vix_pct_3mo. Thresholds tuned against this "
            "distribution are NOT apples-to-apples with regime_watch's live "
            "VIX-percentile grading. Run scripts/nfo/refresh_vix_cache.py "
            "before re-tuning.",
            INDEX_CACHE_DIR,
        )

    enriched = _enrich_trades(trades, index_daily, vix_daily)
    enriched = enriched.dropna(subset=["vix", "iv_minus_rv", "pullback_atr"])
    log.info("Enriched trades: %d (after dropping incomplete history)", len(enriched))

    if len(enriched) < args.min_trades:
        log.error("Not enough enriched trades for grid search (have %d, need %d)",
                  len(enriched), args.min_trades)
        return 1

    result = calibrate.grid_search_thresholds(
        enriched, metric=args.metric, min_trades=args.min_trades, persist=args.write,
    )

    log.info("")
    log.info("=== Baseline (no filter) ===")
    for k, v in result["baseline_unfiltered"].items():
        log.info("  %-24s %s", k, _fmt(v))

    log.info("")
    log.info("=== Best combo by %s ===", args.metric)
    for k, v in result["best"].items():
        log.info("  %-24s %s", k, _fmt(v))

    log.info("")
    log.info("Top-5 combos:")
    for i, combo in enumerate(result["top5"], 1):
        log.info("  %d. %s  |  sharpe=%.2f  win_rate=%.0f%%  worst=%.0f",
                 i,
                 " ".join(f"{k}={_fmt(v)}" for k, v in combo.items()
                          if k in ("vix_rich", "vix_pct_rich", "iv_rv_rich", "pullback_atr")),
                 combo["sharpe"], combo["win_rate"] * 100, combo["worst_cycle_pnl"])

    if args.write:
        log.info("")
        log.info("Wrote %s", calibrate.TUNED_THRESHOLDS_PATH)

    if args.also_pop_table:
        pop = calibrate.build_empirical_pop_table(enriched, persist=True)
        log.info("Wrote %s (%d buckets)", calibrate.EMPIRICAL_POP_PATH, len(pop))

    # Light warning if the edge is inside noise.
    lift = result["best"]["sharpe"] - result["baseline_unfiltered"]["sharpe"]
    if abs(lift) < 0.05 * max(abs(result["baseline_unfiltered"]["sharpe"]), 1e-6):
        log.warning("\nLift is inside noise (<5%%). Treat tuned thresholds as provisional.")

    return 0


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


if __name__ == "__main__":
    raise SystemExit(main())
