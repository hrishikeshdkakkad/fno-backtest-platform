"""Performance analytics for a sequence of CSP trades.

Given the per-cycle trades DataFrame that `backtest.run_csp_backtest` emits,
compute metrics that are actually meaningful for income-strategy evaluation:

- Cumulative equity curve (running P/L of writing one contract every cycle)
- Max drawdown (in $ and as % of avg collateral)
- Consistency: fraction of months with P/L > 0 and > target
- Distribution: p5/p25/median/p75/p95 of monthly P/L
- Regime splits: bullish months (underlying up) vs bearish months
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def equity_curve(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["cycle", "date", "pnl", "cum_pnl"])
    df = trades.sort_values("cycle").copy()
    df["cum_pnl"] = df["pnl_dollars"].cumsum()
    return df[["cycle", "expiry", "pnl_dollars", "cum_pnl"]].rename(
        columns={"expiry": "date", "pnl_dollars": "pnl"}
    )


def max_drawdown(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"dd_dollars": 0.0, "dd_pct_of_collateral": 0.0}
    cum = trades.sort_values("cycle")["pnl_dollars"].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    dd_min = float(dd.min()) if len(dd) else 0.0
    avg_coll = float(trades["collateral"].mean())
    return {
        "dd_dollars": dd_min,
        "dd_pct_of_collateral": (dd_min / avg_coll) if avg_coll else 0.0,
    }


def distribution(trades: pd.DataFrame, target_dollars: float = 0.0) -> dict:
    if trades.empty:
        return {}
    pnl = trades["pnl_dollars"].values
    return {
        "pos_months": int((pnl > 0).sum()),
        "neg_months": int((pnl < 0).sum()),
        "months_above_target": int((pnl >= target_dollars).sum()),
        "p5": float(np.percentile(pnl, 5)),
        "p25": float(np.percentile(pnl, 25)),
        "median": float(np.percentile(pnl, 50)),
        "p75": float(np.percentile(pnl, 75)),
        "p95": float(np.percentile(pnl, 95)),
    }


def full_report(trades: pd.DataFrame, target_dollars: float = 0.0) -> dict:
    return {
        **max_drawdown(trades),
        **distribution(trades, target_dollars),
    }
