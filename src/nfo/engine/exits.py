"""Engine: exit-timing decisions for credit-spread cycles (master design §6).

Single source of truth for when/how a spread cycle exits. Replaces
`backtest._manage_exit` + the expiry-settlement branch in `backtest._run_cycle`.
Costs are NOT computed here — that stays in engine.execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd

from nfo.spread import spread_payoff_per_share
from nfo.specs.strategy import ExitSpec


Outcome = Literal[
    "profit_take",
    "managed",
    "expired_worthless",
    "partial_loss",
    "max_loss",
]


@dataclass
class ExitDecision:
    outcome: Outcome
    net_close_at_exit: float
    exit_date: date
    dte_exit: int
    pnl_per_share: float
    closed_before_expiry: bool
    short_exit_premium: float   # 0.0 if settled at expiry
    long_exit_premium: float    # 0.0 if settled at expiry


def decide_exit(
    merged_legs: pd.DataFrame,
    *,
    exit_spec: ExitSpec,
    net_credit: float,
    short_strike: float,
    long_strike: float,
    spot_at_expiry: float,
    expiry_date: date,
) -> ExitDecision:
    """Decide exit timing for a single spread cycle.

    Args:
        merged_legs: DataFrame with columns [date, short_close, long_close, net_close, dte]
                     (empty allowed).
        exit_spec: StrategySpec.exit_rule (variant, profit_take_fraction, manage_at_dte).
        net_credit: credit received at entry.
        short_strike, long_strike: legs.
        spot_at_expiry: underlying close on expiry_date (for intrinsic settlement).
        expiry_date: cycle expiry.

    Returns:
        ExitDecision.
    """
    # Treat None as 1.0 for branch-1 check (branch always skipped).
    pt = exit_spec.profit_take_fraction if exit_spec.profit_take_fraction is not None else 1.0
    manage_dte = exit_spec.manage_at_dte

    if not merged_legs.empty:
        # Branch 1: profit take
        if pt < 1.0:
            threshold = (1.0 - pt) * net_credit
            hits = merged_legs[merged_legs["net_close"] <= threshold]
            if not hits.empty:
                row = hits.iloc[0]
                return ExitDecision(
                    outcome="profit_take",
                    net_close_at_exit=float(row["net_close"]),
                    exit_date=row["date"].date(),
                    dte_exit=int(row["dte"]),
                    pnl_per_share=net_credit - float(row["net_close"]),
                    closed_before_expiry=True,
                    short_exit_premium=float(row.get("short_close", row["net_close"])),
                    long_exit_premium=float(row.get("long_close", 0.0)),
                )
        # Branch 2: manage at DTE
        if manage_dte is not None:
            hits = merged_legs[merged_legs["dte"] <= manage_dte]
            if not hits.empty:
                row = hits.iloc[0]
                return ExitDecision(
                    outcome="managed",
                    net_close_at_exit=float(row["net_close"]),
                    exit_date=row["date"].date(),
                    dte_exit=int(row["dte"]),
                    pnl_per_share=net_credit - float(row["net_close"]),
                    closed_before_expiry=True,
                    short_exit_premium=float(row.get("short_close", row["net_close"])),
                    long_exit_premium=float(row.get("long_close", 0.0)),
                )

    # Branch 3: settle at expiry
    pnl_per_share, outcome = spread_payoff_per_share(
        short_strike, long_strike, net_credit, spot_at_expiry,
    )
    return ExitDecision(
        outcome=outcome,
        net_close_at_exit=net_credit - pnl_per_share,
        exit_date=expiry_date,
        dte_exit=0,
        pnl_per_share=pnl_per_share,
        closed_before_expiry=False,
        short_exit_premium=0.0,
        long_exit_premium=0.0,
    )
