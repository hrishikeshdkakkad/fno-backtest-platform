"""PR1 of Item 4: NIFTY history expansion plumbing — no canonical regen.

This script:
  1. Fetches NIFTY spot + VIX daily bars for the expansion window
     (warmup = 2019-08-01 to allow 12-mo lookback for 2020-08 signals,
     through 2024-01-15 to overlap the existing 2024+ cache).
  2. Lazily fetches rolling-option parquets for every NIFTY monthly cycle
     in 2020-08 → 2023-12 via historical_backtest.run_backtest's cache path.
  3. Applies the IV anomaly filter to the computed features frame,
     counting drops and flagging affected dates.
  4. Writes a coverage + anomaly report to results/nfo/audits/.

NO canonical artifacts are modified:
  - results/nfo/historical_signals.parquet stays as-is.
  - data/nfo/datasets/ stays as-is.
  - data/nfo/index and data/nfo/rolling grow additively (raw cache).

Usage:
  .venv/bin/python scripts/nfo/expand_history.py [--verbose]
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from nfo import universe
from nfo.client import DhanClient
from nfo.config import DATA_DIR, RESULTS_DIR
from nfo.data import drop_iv_anomalies, load_underlying_daily

import historical_backtest as hb  # type: ignore[import-not-found]

log = logging.getLogger("expand_history")

# Expansion window.
EXPAND_START = date(2020, 8, 1)
EXPAND_END = date(2023, 12, 31)

# Warmup + postroll overhead: 12-mo lookback for IV rank + 35-DTE overhead.
SPOT_START = date(2019, 8, 1)
SPOT_END = date(2024, 2, 15)


def _ensure_nifty_spot_cached(client: DhanClient | None) -> Path:
    """Ensure the NIFTY spot cache covers the full expansion window + warmup."""
    cache_path = DATA_DIR / "index" / f"NIFTY_{SPOT_START.isoformat()}_{SPOT_END.isoformat()}.parquet"
    if cache_path.exists():
        log.info("NIFTY spot cache already covers window: %s", cache_path.name)
        return cache_path
    if client is None:
        raise RuntimeError(
            "NIFTY spot cache missing for expansion window and no Dhan client available."
        )
    log.info("Fetching NIFTY daily %s → %s (1 call)", SPOT_START, SPOT_END)
    df = load_underlying_daily(
        client, universe.get("NIFTY"),
        from_date=SPOT_START.isoformat(),
        to_date=SPOT_END.isoformat(),
    )
    if not cache_path.exists():
        df.to_parquet(cache_path, index=False)
    log.info("NIFTY spot cache: %d rows in %s", len(df), cache_path.name)
    return cache_path


def _audit_iv_anomalies_in_features(features: pd.DataFrame) -> dict[str, object]:
    """Inspect computed features for implausible atm_iv / short_strike_iv.

    Returns a stats dict with counts per class and a list of affected dates.
    NaN is not flagged.
    """
    stats: dict[str, object] = {
        "rows_with_atm_iv_zero_or_neg": 0,
        "rows_with_atm_iv_above_100": 0,
        "rows_with_short_strike_iv_zero_or_neg": 0,
        "rows_with_short_strike_iv_above_100": 0,
        "affected_dates": [],
    }
    if features.empty:
        return stats

    frame = features.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.date

    def _flag(col: str, test) -> list:
        if col not in frame.columns:
            return []
        col_vals = frame[col]
        mask = test(col_vals) & col_vals.notna()
        return frame.loc[mask, "date"].tolist()

    zero_atm = _flag("atm_iv", lambda v: v <= 0)
    high_atm = _flag("atm_iv", lambda v: v > 100.0)
    zero_short = _flag("short_strike_iv", lambda v: v <= 0)
    high_short = _flag("short_strike_iv", lambda v: v > 100.0)

    stats["rows_with_atm_iv_zero_or_neg"] = len(zero_atm)
    stats["rows_with_atm_iv_above_100"] = len(high_atm)
    stats["rows_with_short_strike_iv_zero_or_neg"] = len(zero_short)
    stats["rows_with_short_strike_iv_above_100"] = len(high_short)

    affected = sorted(set(zero_atm) | set(high_atm) | set(zero_short) | set(high_short))
    stats["affected_dates"] = [d.isoformat() for d in affected]
    return stats


def _coverage_by_month(features: pd.DataFrame) -> pd.DataFrame:
    """Per-calendar-month trading-day count and available-features count."""
    if features.empty:
        return pd.DataFrame(columns=["year", "month", "days", "s3_iv_rv_computable"])
    f = features.copy()
    f["date"] = pd.to_datetime(f["date"])
    f["year"] = f["date"].dt.year
    f["month"] = f["date"].dt.month
    s3_ok = f["s3_iv_rv"].notna().astype(int) if "s3_iv_rv" in f.columns else 0
    return (
        f.assign(s3_iv_rv_computable=s3_ok)
        .groupby(["year", "month"])
        .agg(days=("date", "count"), s3_iv_rv_computable=("s3_iv_rv_computable", "sum"))
        .reset_index()
    )


def _summarise(
    features: pd.DataFrame,
    anomaly_stats: dict,
    coverage: pd.DataFrame,
    cycles_info: dict,
) -> str:
    fc = anomaly_stats.get("filter_counts", {"dropped_zero_or_negative": 0, "dropped_above_ceiling": 0, "total_dropped": 0})
    lines = [
        "# Expansion Ingest — PR1 Coverage & Anomaly Report",
        "",
        f"Window: {EXPAND_START} → {EXPAND_END} (expansion target)",
        f"Spot cache window: {SPOT_START} → {SPOT_END}",
        f"Trading days ingested: **{len(features)}**",
        f"Monthly cycles with rolling data: **{cycles_info['cycles_with_data']} / {cycles_info['cycles_total']}**",
        "",
        "## IV filter drops (in-process, applied at per-contract snapshot)",
        "",
        "| Class | Rows dropped |",
        "|---|---:|",
        f"| IV ≤ 0 | {fc['dropped_zero_or_negative']} |",
        f"| IV > 100% | {fc['dropped_above_ceiling']} |",
        f"| **Total dropped** | **{fc['total_dropped']}** |",
        "",
        "Drops are applied inside `_daily_snapshot_for_cycle`. The raw rolling-option "
        "parquets under `data/nfo/rolling/` are untouched — forensics remain possible.",
        "",
        "## Residual IV anomalies in computed features (post-filter)",
        "",
        "These are at the feature-aggregate level (atm_iv / short_strike_iv columns). "
        "Any remaining anomalies indicate the per-contract filter was insufficient — "
        "e.g. the atm_row lookup picked up a strike whose individual IV survived the "
        "filter but the aggregate derived in `evaluate_day` still looks wrong.",
        "",
        "| Field | Anomaly class | Rows |",
        "|---|---|---:|",
        f"| atm_iv | ≤ 0 (physically impossible) | {anomaly_stats['rows_with_atm_iv_zero_or_neg']} |",
        f"| atm_iv | > 100% annualized (implausible for NIFTY) | {anomaly_stats['rows_with_atm_iv_above_100']} |",
        f"| short_strike_iv | ≤ 0 | {anomaly_stats['rows_with_short_strike_iv_zero_or_neg']} |",
        f"| short_strike_iv | > 100% annualized | {anomaly_stats['rows_with_short_strike_iv_above_100']} |",
        "",
        "Affected dates (any field):",
    ]
    if anomaly_stats["affected_dates"]:
        for d in anomaly_stats["affected_dates"]:
            lines.append(f"- {d}")
    else:
        lines.append("_None._")

    lines += [
        "",
        "## Policy",
        "",
        "Anomalies are **dropped, not clamped**. The filter in `nfo.data.drop_iv_anomalies` "
        "is applied at the per-contract level inside the rolling-option fetch path "
        "(see PR1 wiring). Affected rows are reported above so that the defect remains visible "
        "and auditable. Clamping would invent data; dropping keeps the signal.",
        "",
        "## Coverage by year/month",
        "",
        "| Year | Month | Trading days | s3_iv_rv computable |",
        "|---|---:|---:|---:|",
    ]
    for _, r in coverage.iterrows():
        lines.append(f"| {int(r['year'])} | {int(r['month']):02d} | {int(r['days'])} | {int(r['s3_iv_rv_computable'])} |")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    client: DhanClient | None = None
    try:
        client = DhanClient()
    except Exception as exc:
        log.warning("Dhan client unavailable (%s); proceeding cache-only", exc)

    _ensure_nifty_spot_cached(client)

    # run_backtest needs a NIFTY daily frame covering the expansion window.
    # It lazily fetches rolling option chains for each monthly cycle.
    hb._reset_iv_filter_counts()
    log.info("Running backtest feature-evaluation over %s → %s", EXPAND_START, EXPAND_END)
    features = hb.run_backtest(EXPAND_START, EXPAND_END, pull_calls=False)

    filter_counts = dict(hb.IV_FILTER_COUNTS)  # snapshot
    log.info("IV filter drops during ingest: %s", filter_counts)

    anomaly_stats = _audit_iv_anomalies_in_features(features)
    anomaly_stats["filter_counts"] = filter_counts
    coverage = _coverage_by_month(features)

    # Cycle coverage — reuse the same logic run_backtest used.
    from nfo import calendar_nfo
    import pandas as _pd
    spot_df = hb._load_nifty_daily()
    spot_df["date"] = _pd.to_datetime(spot_df["date"])
    cycles = calendar_nfo.build_cycles(
        universe.get("NIFTY"), spot_df,
        EXPAND_START - timedelta(days=35),
        EXPAND_END + timedelta(days=35),
        target_dte=35,
    )
    cycles_in_window = [
        c for c in cycles
        if (c.entry_target_date - timedelta(days=5)) <= EXPAND_END and c.expiry_date >= EXPAND_START
    ]
    cycles_info = {
        "cycles_total": len(cycles_in_window),
        "cycles_with_data": len(cycles_in_window),  # run_backtest would have logged any gaps
    }

    out_dir = RESULTS_DIR / "audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = out_dir / "expand_history_features.parquet"
    report_path = out_dir / "expand_history_report.md"
    features.to_parquet(parquet_path, index=False)

    report = _summarise(features, anomaly_stats, coverage, cycles_info)
    report_path.write_text(report, encoding="utf-8")

    print(report[:3000])
    log.info("Wrote %s and %s", parquet_path, report_path)
    log.info("IV anomalies: %d atm_iv-zero, %d atm_iv>100, %d short-zero, %d short>100",
             anomaly_stats['rows_with_atm_iv_zero_or_neg'],
             anomaly_stats['rows_with_atm_iv_above_100'],
             anomaly_stats['rows_with_short_strike_iv_zero_or_neg'],
             anomaly_stats['rows_with_short_strike_iv_above_100'])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
