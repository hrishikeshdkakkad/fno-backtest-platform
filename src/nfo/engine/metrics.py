"""Engine: per-trade summary statistics (master design §6)."""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd


@dataclass(slots=True)
class SummaryStats:
    n: int
    win_rate: float
    avg_pnl_contract: float
    total_pnl_contract: float
    worst_cycle_pnl: float
    best_cycle_pnl: float
    std_pnl_contract: float
    sharpe: float
    sortino: float
    max_loss_rate: float

    def to_dict(self) -> dict[str, float]:
        return {
            "n": self.n, "win_rate": self.win_rate,
            "avg_pnl_contract": self.avg_pnl_contract,
            "total_pnl_contract": self.total_pnl_contract,
            "worst_cycle_pnl": self.worst_cycle_pnl,
            "best_cycle_pnl": self.best_cycle_pnl,
            "std_pnl_contract": self.std_pnl_contract,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "max_loss_rate": self.max_loss_rate,
        }


def summary_stats(trades: pd.DataFrame, *, periods_per_year: float = 12.0) -> SummaryStats:
    """Compute Sharpe/Sortino + win-rate over a trades DataFrame (one row per cycle)."""
    if trades.empty:
        return SummaryStats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    p = trades["pnl_contract"]
    mean = float(p.mean())
    std = float(p.std(ddof=1)) if len(p) > 1 else float("nan")
    downside = p[p < 0]
    dn_std = float(downside.std(ddof=1)) if len(downside) > 1 else float("nan")
    sharpe = (mean / std) * math.sqrt(periods_per_year) if std and std > 0 else 0.0
    sortino = (mean / dn_std) * math.sqrt(periods_per_year) if dn_std and dn_std > 0 else 0.0
    return SummaryStats(
        n=int(len(p)),
        win_rate=float((p > 0).mean()),
        avg_pnl_contract=mean,
        total_pnl_contract=float(p.sum()),
        worst_cycle_pnl=float(p.min()),
        best_cycle_pnl=float(p.max()),
        std_pnl_contract=std,
        sharpe=sharpe,
        sortino=sortino,
        max_loss_rate=float((trades.get("outcome", pd.Series([], dtype=str)) == "max_loss").mean()),
    )
