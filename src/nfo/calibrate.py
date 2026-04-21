"""Empirical calibration of POP, thresholds, and edge — offline from cached trades.

Three outputs drive the Sharpe improvement:

  1. **Empirical-POP table** — historical win-rate per (|Δ| bucket × DTE bucket).
     Answers "did a 0.30Δ / 35-DTE spread actually win 73% of the time in this
     regime?" vs what the model-POP says. Fed into the TUI so the trader sees
     both numbers and the gap.

  2. **Threshold grid-search** — given a trades frame enriched with the four
     regime signals at entry, sweeps all combinations of the VIX / IV-RV /
     pullback thresholds and returns the combo maximising Sharpe (or any
     other metric). Produces `tuned_thresholds.json`.

  3. **Summary stats** — Sharpe, Sortino, max-loss rate, before/after
     comparison for the tier-1 report.

All functions operate on a pandas DataFrame of trades with the columns
`spread_trades.csv` already provides (entry_delta, dte_entry, pnl_per_share,
pnl_contract, outcome, entry_date, expiry_date, …). Regime enrichment
(iv_rank / vix_pct / iv_minus_rv / pullback_atr) is expected to come from
the caller — usually the `tune_thresholds.py` script, which enriches using
cached NIFTY bars + `nfo.signals`.
"""
from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping

import numpy as np
import pandas as pd

from .config import RESULTS_DIR
from nfo.engine.metrics import SummaryStats, summary_stats  # re-exported

EMPIRICAL_POP_PATH: Path = RESULTS_DIR / "empirical_pop.parquet"
TUNED_THRESHOLDS_PATH: Path = RESULTS_DIR / "tuned_thresholds.json"

DEFAULT_DELTA_BUCKETS: tuple[float, ...] = (0.15, 0.20, 0.25, 0.30, 0.35, 0.40)
DEFAULT_DTE_BUCKETS: tuple[int, ...] = (14, 25, 35, 50)


# ── Empirical POP table ─────────────────────────────────────────────────────


def build_empirical_pop_table(
    trades: pd.DataFrame,
    *,
    delta_buckets: Iterable[float] = DEFAULT_DELTA_BUCKETS,
    dte_buckets: Iterable[int] = DEFAULT_DTE_BUCKETS,
    persist: bool = True,
) -> pd.DataFrame:
    """Aggregate historical trades into a per-bucket win-rate / PnL table.

    Buckets on `|entry_delta|` and `dte_entry`. Returns a DataFrame with n,
    wins, win_rate, avg_pnl_per_share, worst_pnl_per_share, std_pnl_per_share.
    """
    if trades.empty:
        return pd.DataFrame(columns=[
            "delta_bucket", "dte_bucket", "n", "wins", "win_rate",
            "avg_pnl_per_share", "worst_pnl_per_share", "std_pnl_per_share",
        ])
    t = trades.copy()
    t["abs_delta"] = t["entry_delta"].abs()
    d_edges = sorted(set(float(x) for x in delta_buckets)) + [1.0]
    dte_edges = sorted(set(int(x) for x in dte_buckets)) + [365]
    t["delta_bucket"] = pd.cut(t["abs_delta"], bins=d_edges, include_lowest=True)
    t["dte_bucket"] = pd.cut(t["dte_entry"], bins=dte_edges, include_lowest=True)

    grouped = t.groupby(["delta_bucket", "dte_bucket"], observed=True)["pnl_per_share"]
    out = grouped.agg(
        n="count",
        wins=lambda s: int((s > 0).sum()),
        avg_pnl_per_share="mean",
        worst_pnl_per_share="min",
        std_pnl_per_share="std",
    ).reset_index()
    out["win_rate"] = out["wins"] / out["n"]
    # Intervals are hard to serialise to parquet; stringify the bins.
    out["delta_bucket"] = out["delta_bucket"].astype(str)
    out["dte_bucket"] = out["dte_bucket"].astype(str)
    if persist:
        EMPIRICAL_POP_PATH.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(EMPIRICAL_POP_PATH, index=False)
    return out


def lookup_empirical_pop(
    delta: float,
    dte: int,
    *,
    table: pd.DataFrame | None = None,
) -> dict[str, float]:
    """Nearest-bucket lookup. Returns {win_rate, avg_pnl, n} (NaNs if no data)."""
    if table is None:
        if not EMPIRICAL_POP_PATH.exists():
            return {"win_rate": float("nan"), "avg_pnl_per_share": float("nan"), "n": 0}
        table = pd.read_parquet(EMPIRICAL_POP_PATH)
    if table.empty:
        return {"win_rate": float("nan"), "avg_pnl_per_share": float("nan"), "n": 0}

    # Parse back the interval midpoints for nearest-bucket selection.
    def _mid(label: str) -> float:
        try:
            lo, hi = label.strip("([])").replace(" ", "").split(",")
            return (float(lo) + float(hi)) / 2.0
        except Exception:
            return float("nan")

    tbl = table.copy()
    tbl["d_mid"] = tbl["delta_bucket"].map(_mid)
    tbl["dte_mid"] = tbl["dte_bucket"].map(_mid)
    tbl["err"] = (tbl["d_mid"] - abs(delta)).abs() + 0.01 * (tbl["dte_mid"] - dte).abs()
    best = tbl.sort_values("err").iloc[0]
    return {
        "win_rate": float(best["win_rate"]),
        "avg_pnl_per_share": float(best["avg_pnl_per_share"]),
        "n": int(best["n"]),
    }


# ── Summary stats — moved to nfo.engine.metrics (re-exported at top) ────────


# ── Grid-search thresholds ──────────────────────────────────────────────────


DEFAULT_PARAM_GRID: Mapping[str, Iterable[float]] = {
    # India VIX bounds — recalibrated from CBOE-VIX territory (18-24) to
    # India's empirical distribution (median ~13, 70th ~14-15, 90th ~18).
    # See docs/india-fno-nuances.md §4.
    "vix_rich": (13.0, 14.0, 15.0, 16.0, 18.0),
    "vix_pct_rich": (0.5, 0.6, 0.7, 0.8),
    "iv_rv_rich": (-2.0, 0.0, 2.0, 4.0),
    "pullback_atr": (0.5, 1.0, 1.5, 2.0),
}


def _default_filter(row: pd.Series, params: Mapping[str, float]) -> bool:
    """Row passes if all four signals meet the threshold. Expected columns:
       vix, vix_pct_3mo, iv_minus_rv, pullback_atr (enrich before calling)."""
    vix_ok = row.get("vix", 0) > params["vix_rich"]
    vp_ok = row.get("vix_pct_3mo", 0) >= params["vix_pct_rich"]
    iv_ok = row.get("iv_minus_rv", 0) >= params["iv_rv_rich"]
    pb_ok = row.get("pullback_atr", 0) >= params["pullback_atr"]
    score = sum((vix_ok, vp_ok, iv_ok, pb_ok))
    # "Take trade when score ≥ 3" — roughly A/A+ grade.
    return score >= 3


def grid_search_thresholds(
    trades_enriched: pd.DataFrame,
    *,
    param_grid: Mapping[str, Iterable[float]] = DEFAULT_PARAM_GRID,
    metric: str = "sharpe",
    filter_fn: Callable[[pd.Series, Mapping[str, float]], bool] = _default_filter,
    persist: bool = True,
    min_trades: int = 5,
) -> dict:
    """Sweep `param_grid` and return the combo that maximises `metric`.

    `trades_enriched` must carry vix, vix_pct_3mo, iv_minus_rv, pullback_atr
    columns at entry (the script does this enrichment from cached index
    bars; calibrate.py stays IO-free).
    """
    if trades_enriched.empty:
        return {"error": "empty trades", "best": None, "all": []}

    keys = list(param_grid.keys())
    combos = list(itertools.product(*(list(param_grid[k]) for k in keys)))

    results: list[dict] = []
    for vals in combos:
        params = dict(zip(keys, vals))
        mask = trades_enriched.apply(lambda r: filter_fn(r, params), axis=1)
        subset = trades_enriched[mask]
        if len(subset) < min_trades:
            continue
        stats = summary_stats(subset)
        results.append({**params, **stats.to_dict()})

    if not results:
        return {"error": "no combo produced ≥ min_trades", "best": None, "all": []}

    results_df = pd.DataFrame(results)
    best_row = results_df.sort_values(metric, ascending=False).iloc[0].to_dict()

    # Baseline = no filter at all, for reporting delta.
    baseline = summary_stats(trades_enriched).to_dict()

    out = {
        "metric": metric,
        "n_combos_evaluated": len(results),
        "best": best_row,
        "baseline_unfiltered": baseline,
        "top5": results_df.sort_values(metric, ascending=False).head(5).to_dict(orient="records"),
    }
    if persist:
        TUNED_THRESHOLDS_PATH.parent.mkdir(parents=True, exist_ok=True)
        TUNED_THRESHOLDS_PATH.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


def load_tuned_thresholds() -> dict | None:
    """Read the best-combo JSON written by grid_search_thresholds."""
    if not TUNED_THRESHOLDS_PATH.exists():
        return None
    try:
        return json.loads(TUNED_THRESHOLDS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
