"""Parity: engine.exits.decide_exit matches legacy backtest._manage_exit +
the expiry-settlement branch in backtest._run_cycle (i.e. spread_payoff_per_share).

Legacy combines two stages:
  1. `_manage_exit(merged, cfg, net_credit)` → (row_or_None, outcome_or_"")
  2. If None: call `spread_payoff_per_share(...)` for intrinsic settlement.

Engine combines both into `decide_exit(...)` returning an `ExitDecision`.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from nfo.backtest import _manage_exit
from nfo.engine.exits import decide_exit
from nfo.spread import SpreadConfig, spread_payoff_per_share
from nfo.specs.strategy import ExitSpec


def _merged(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["date", "short_close", "long_close", "net_close", "dte"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _legacy_cfg(profit_take: float, manage_at_dte: int | None) -> SpreadConfig:
    return SpreadConfig(
        underlying="NIFTY",
        target_delta=0.25,
        target_dte=35,
        profit_take=profit_take,
        manage_at_dte=manage_at_dte,
    )


def _exit_spec(profit_take: float, manage_at_dte: int | None) -> ExitSpec:
    """Bridge legacy cfg → new ExitSpec. Variant is cosmetic for engine logic;
    we still set it so the model validators don't reject."""
    if profit_take >= 1.0 and manage_at_dte is None:
        return ExitSpec(variant="hte", profit_take_fraction=1.0, manage_at_dte=None)
    # dte-only (manage with no pt) → dte2 variant
    if profit_take >= 1.0 and manage_at_dte is not None:
        return ExitSpec(variant="dte2", profit_take_fraction=1.0, manage_at_dte=manage_at_dte)
    # map pt value → nearest variant label (cosmetic)
    if profit_take == 0.25:
        variant = "pt25"
    elif profit_take == 0.75:
        variant = "pt75"
    else:
        variant = "pt50"
    return ExitSpec(
        variant=variant,
        profit_take_fraction=profit_take,
        manage_at_dte=manage_at_dte,
    )


def _run_legacy(
    merged: pd.DataFrame,
    cfg: SpreadConfig,
    net_credit: float,
    short_strike: float,
    long_strike: float,
    spot_at_expiry: float,
    expiry_date: date,
) -> dict:
    """Mirror the two-stage legacy exit logic the same way backtest._run_cycle does."""
    exit_row, outcome = _manage_exit(merged, cfg, net_credit) if not merged.empty else (None, "")
    if exit_row is not None:
        net_close_at_exit = float(exit_row["net_close"])
        pnl_per_share = net_credit - net_close_at_exit
        return {
            "outcome": outcome,
            "closed_before_expiry": True,
            "net_close_at_exit": net_close_at_exit,
            "pnl_per_share": pnl_per_share,
            "exit_date": exit_row["date"].date(),
            "dte_exit": int(exit_row["dte"]),
            "short_exit_premium": float(exit_row.get("short_close", net_close_at_exit)),
            "long_exit_premium": float(exit_row.get("long_close", 0.0)),
        }
    # Settle at expiry
    pnl_per_share, outcome = spread_payoff_per_share(
        short_strike, long_strike, net_credit, spot_at_expiry,
    )
    return {
        "outcome": outcome,
        "closed_before_expiry": False,
        "net_close_at_exit": net_credit - pnl_per_share,
        "pnl_per_share": pnl_per_share,
        "exit_date": expiry_date,
        "dte_exit": 0,
        "short_exit_premium": 0.0,
        "long_exit_premium": 0.0,
    }


def _assert_parity(legacy: dict, engine) -> None:
    assert engine.outcome == legacy["outcome"]
    assert engine.closed_before_expiry == legacy["closed_before_expiry"]
    assert engine.net_close_at_exit == pytest.approx(legacy["net_close_at_exit"])
    assert engine.pnl_per_share == pytest.approx(legacy["pnl_per_share"])
    assert engine.exit_date == legacy["exit_date"]
    assert engine.dte_exit == legacy["dte_exit"]
    assert engine.short_exit_premium == pytest.approx(legacy["short_exit_premium"])
    assert engine.long_exit_premium == pytest.approx(legacy["long_exit_premium"])


# ---------------------------------------------------------------------------
# Scenario A: profit-take fires before manage-at-DTE
# ---------------------------------------------------------------------------
def test_parity_profit_take_fires():
    cfg = _legacy_cfg(profit_take=0.5, manage_at_dte=21)
    spec = _exit_spec(profit_take=0.5, manage_at_dte=21)
    net_credit = 12.0
    merged = _merged([
        {"date": "2025-03-24", "short_close": 10.0, "long_close": 2.0, "net_close": 8.0, "dte": 30},
        {"date": "2025-03-28", "short_close": 7.5, "long_close": 2.0, "net_close": 5.5, "dte": 26},
        {"date": "2025-04-02", "short_close": 4.0, "long_close": 1.0, "net_close": 3.0, "dte": 22},
    ])
    legacy = _run_legacy(
        merged, cfg, net_credit,
        short_strike=100.0, long_strike=90.0, spot_at_expiry=105.0,
        expiry_date=date(2025, 4, 24),
    )
    engine = decide_exit(
        merged, exit_spec=spec, net_credit=net_credit,
        short_strike=100.0, long_strike=90.0, spot_at_expiry=105.0,
        expiry_date=date(2025, 4, 24),
    )
    assert legacy["outcome"] == "profit_take"
    _assert_parity(legacy, engine)


# ---------------------------------------------------------------------------
# Scenario B: manage_at_dte fires (no pt hit)
# ---------------------------------------------------------------------------
def test_parity_managed_at_dte_fires():
    # profit_take high enough nothing trips it
    cfg = _legacy_cfg(profit_take=1.0, manage_at_dte=21)
    spec = _exit_spec(profit_take=1.0, manage_at_dte=21)
    net_credit = 10.0
    merged = _merged([
        {"date": "2025-03-24", "short_close": 10.0, "long_close": 2.0, "net_close": 8.0, "dte": 30},
        {"date": "2025-03-28", "short_close": 9.0, "long_close": 1.5, "net_close": 7.5, "dte": 26},
        {"date": "2025-04-02", "short_close": 8.0, "long_close": 1.0, "net_close": 7.0, "dte": 22},
        {"date": "2025-04-03", "short_close": 7.5, "long_close": 1.0, "net_close": 6.5, "dte": 21},
        {"date": "2025-04-05", "short_close": 7.0, "long_close": 0.5, "net_close": 6.5, "dte": 19},
    ])
    legacy = _run_legacy(
        merged, cfg, net_credit,
        short_strike=100.0, long_strike=90.0, spot_at_expiry=105.0,
        expiry_date=date(2025, 4, 24),
    )
    engine = decide_exit(
        merged, exit_spec=spec, net_credit=net_credit,
        short_strike=100.0, long_strike=90.0, spot_at_expiry=105.0,
        expiry_date=date(2025, 4, 24),
    )
    assert legacy["outcome"] == "managed"
    _assert_parity(legacy, engine)


# ---------------------------------------------------------------------------
# Scenario C: settle at expiry — expired_worthless
# ---------------------------------------------------------------------------
def test_parity_settle_expired_worthless():
    cfg = _legacy_cfg(profit_take=1.0, manage_at_dte=None)
    spec = _exit_spec(profit_take=1.0, manage_at_dte=None)
    net_credit = 10.0
    merged = _merged([
        {"date": "2025-03-24", "short_close": 10.0, "long_close": 2.0, "net_close": 8.0, "dte": 30},
        {"date": "2025-04-20", "short_close": 0.5, "long_close": 0.1, "net_close": 0.4, "dte": 4},
    ])
    legacy = _run_legacy(
        merged, cfg, net_credit,
        short_strike=100.0, long_strike=90.0, spot_at_expiry=110.0,
        expiry_date=date(2025, 4, 24),
    )
    engine = decide_exit(
        merged, exit_spec=spec, net_credit=net_credit,
        short_strike=100.0, long_strike=90.0, spot_at_expiry=110.0,
        expiry_date=date(2025, 4, 24),
    )
    assert legacy["outcome"] == "expired_worthless"
    _assert_parity(legacy, engine)


# ---------------------------------------------------------------------------
# Scenario D: settle at expiry — partial_loss
# ---------------------------------------------------------------------------
def test_parity_settle_partial_loss():
    cfg = _legacy_cfg(profit_take=1.0, manage_at_dte=None)
    spec = _exit_spec(profit_take=1.0, manage_at_dte=None)
    net_credit = 10.0
    merged = _merged([
        {"date": "2025-03-24", "short_close": 10.0, "long_close": 2.0, "net_close": 8.0, "dte": 30},
    ])
    legacy = _run_legacy(
        merged, cfg, net_credit,
        short_strike=100.0, long_strike=90.0, spot_at_expiry=95.0,
        expiry_date=date(2025, 4, 24),
    )
    engine = decide_exit(
        merged, exit_spec=spec, net_credit=net_credit,
        short_strike=100.0, long_strike=90.0, spot_at_expiry=95.0,
        expiry_date=date(2025, 4, 24),
    )
    assert legacy["outcome"] == "partial_loss"
    _assert_parity(legacy, engine)


# ---------------------------------------------------------------------------
# Scenario E: settle at expiry — max_loss
# ---------------------------------------------------------------------------
def test_parity_settle_max_loss():
    cfg = _legacy_cfg(profit_take=1.0, manage_at_dte=None)
    spec = _exit_spec(profit_take=1.0, manage_at_dte=None)
    net_credit = 3.0  # leaves room for a real max loss (width - credit)
    merged = _merged([
        {"date": "2025-03-24", "short_close": 4.0, "long_close": 1.0, "net_close": 3.0, "dte": 30},
    ])
    legacy = _run_legacy(
        merged, cfg, net_credit,
        short_strike=100.0, long_strike=90.0, spot_at_expiry=80.0,
        expiry_date=date(2025, 4, 24),
    )
    engine = decide_exit(
        merged, exit_spec=spec, net_credit=net_credit,
        short_strike=100.0, long_strike=90.0, spot_at_expiry=80.0,
        expiry_date=date(2025, 4, 24),
    )
    assert legacy["outcome"] == "max_loss"
    _assert_parity(legacy, engine)


# ---------------------------------------------------------------------------
# Scenario F: empty merged → settle at expiry
# ---------------------------------------------------------------------------
def test_parity_empty_merged_settles():
    cfg = _legacy_cfg(profit_take=0.5, manage_at_dte=21)
    spec = _exit_spec(profit_take=0.5, manage_at_dte=21)
    net_credit = 8.0
    merged = _merged([])
    legacy = _run_legacy(
        merged, cfg, net_credit,
        short_strike=100.0, long_strike=90.0, spot_at_expiry=102.0,
        expiry_date=date(2025, 4, 24),
    )
    engine = decide_exit(
        merged, exit_spec=spec, net_credit=net_credit,
        short_strike=100.0, long_strike=90.0, spot_at_expiry=102.0,
        expiry_date=date(2025, 4, 24),
    )
    # legacy outcome determined by spread_payoff_per_share
    _assert_parity(legacy, engine)


# ---------------------------------------------------------------------------
# Scenario G: pt fires when it would OVERRIDE manage_at_dte on same merged frame
# (branch 1 takes precedence over branch 2 — legacy semantics).
# ---------------------------------------------------------------------------
def test_parity_profit_take_precedence_over_manage():
    cfg = _legacy_cfg(profit_take=0.5, manage_at_dte=21)
    spec = _exit_spec(profit_take=0.5, manage_at_dte=21)
    net_credit = 10.0
    # Row 1 trips pt (net_close 4 ≤ 5 threshold) and is at dte 30 (> 21)
    # Row 2 is at dte 20 (would have been managed if pt didn't fire first)
    merged = _merged([
        {"date": "2025-03-24", "short_close": 5.0, "long_close": 1.0, "net_close": 4.0, "dte": 30},
        {"date": "2025-04-04", "short_close": 6.0, "long_close": 1.0, "net_close": 5.0, "dte": 20},
    ])
    legacy = _run_legacy(
        merged, cfg, net_credit,
        short_strike=100.0, long_strike=90.0, spot_at_expiry=105.0,
        expiry_date=date(2025, 4, 24),
    )
    engine = decide_exit(
        merged, exit_spec=spec, net_credit=net_credit,
        short_strike=100.0, long_strike=90.0, spot_at_expiry=105.0,
        expiry_date=date(2025, 4, 24),
    )
    assert legacy["outcome"] == "profit_take"
    _assert_parity(legacy, engine)
