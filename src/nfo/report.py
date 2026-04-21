"""Equity-curve and distribution analytics for a SpreadTrade DataFrame.

Mirrors the shape of `src/csp/report.py`. Designed to operate on either:
  * a single-config trades DataFrame (one underlying × config), or
  * the full grid output (which carries param_* columns for per-config slicing).
"""
from __future__ import annotations

import pandas as pd


def equity_curve(trades: pd.DataFrame, capital: float = 100_000.0) -> pd.DataFrame:
    """Cumulative P&L curve. Assumes 1 contract per cycle, unless you've pre-scaled."""
    if trades.empty:
        return pd.DataFrame(columns=["exit_date", "pnl_contract", "equity"])
    out = trades[["exit_date", "pnl_contract"]].copy()
    out["exit_date"] = pd.to_datetime(out["exit_date"])
    out = out.sort_values("exit_date").reset_index(drop=True)
    out["equity"] = capital + out["pnl_contract"].cumsum()
    return out


def max_drawdown(curve: pd.DataFrame) -> dict:
    if curve.empty:
        return {"dd_pct": 0.0, "dd_abs": 0.0, "peak_date": None, "trough_date": None}
    eq = curve["equity"].values
    peak = eq[0]
    peak_idx = 0
    trough_idx = 0
    worst_dd_abs = 0.0
    worst_dd_pct = 0.0
    worst_peak_idx = 0
    for i, v in enumerate(eq):
        if v > peak:
            peak = v
            peak_idx = i
        dd = peak - v
        if dd > worst_dd_abs:
            worst_dd_abs = dd
            worst_dd_pct = dd / peak if peak > 0 else 0.0
            trough_idx = i
            worst_peak_idx = peak_idx
    return {
        "dd_pct": float(worst_dd_pct),
        "dd_abs": float(worst_dd_abs),
        "peak_date": pd.to_datetime(curve["exit_date"].iloc[worst_peak_idx]),
        "trough_date": pd.to_datetime(curve["exit_date"].iloc[trough_idx]),
    }


def distribution(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {}
    pnl = trades["pnl_contract"]
    return {
        "n": int(len(pnl)),
        "mean": float(pnl.mean()),
        "median": float(pnl.median()),
        "std": float(pnl.std(ddof=1)) if len(pnl) > 1 else 0.0,
        "p5": float(pnl.quantile(0.05)),
        "p25": float(pnl.quantile(0.25)),
        "p75": float(pnl.quantile(0.75)),
        "p95": float(pnl.quantile(0.95)),
        "wins": int((pnl > 0).sum()),
        "losses": int((pnl < 0).sum()),
    }


def full_report(trades: pd.DataFrame, capital: float = 100_000.0) -> dict:
    curve = equity_curve(trades, capital)
    return {
        "distribution": distribution(trades),
        "drawdown": max_drawdown(curve),
        "final_equity": float(curve["equity"].iloc[-1]) if not curve.empty else capital,
        "total_pnl": float(trades["pnl_contract"].sum()) if not trades.empty else 0.0,
    }
