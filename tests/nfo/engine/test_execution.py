"""Unit tests for engine.execution (master design §6, §10).

Exercises `simulate_cycle_pure` with hand-built merged_legs frames, so no
Dhan dependency. Parity against legacy `backtest._run_cycle` lives in
test_execution_parity.py.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from nfo.costs import spread_roundtrip_cost
from nfo.engine.cycles import cycle_id, trade_id
from nfo.engine.execution import SimulatedTrade, simulate_cycle_pure
from nfo.specs.strategy import (
    CapitalSpec,
    EntrySpec,
    ExitSpec,
    SelectionSpec,
    SlippageSpec,
    StrategySpec,
    TriggerSpec,
    UniverseSpec,
)
from nfo.universe import get as get_under


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _merged(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["date", "short_close", "long_close", "net_close", "dte"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _strategy_spec(
    *,
    variant: str,
    profit_take_fraction: float | None,
    manage_at_dte: int | None,
    delta_target: float = 0.30,
    width_value: float = 100.0,
    dte_target: int = 35,
    strategy_version: str = "3.0.0",
    strategy_id: str = "v3",
) -> StrategySpec:
    return StrategySpec(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        description="test spec",
        universe=UniverseSpec(
            underlyings=["NIFTY"],
            delta_target=delta_target,
            delta_tolerance=0.05,
            width_rule="fixed",
            width_value=width_value,
            dte_target=dte_target,
            dte_tolerance=3,
            allowed_contract_families=["PE"],
        ),
        feature_set=["vix_abs"],
        trigger_rule=TriggerSpec(),
        selection_rule=SelectionSpec(
            mode="cycle_matched",
            preferred_exit_variant=variant,
        ),
        entry_rule=EntrySpec(),
        exit_rule=ExitSpec(
            variant=variant,
            profit_take_fraction=profit_take_fraction,
            manage_at_dte=manage_at_dte,
        ),
        capital_rule=CapitalSpec(fixed_capital_inr=1_000_000),
        slippage_rule=SlippageSpec(),
    )


def _meta(
    *,
    short_strike: float = 21850.0,
    long_strike: float = 21750.0,
    short_premium: float = 15.0,
    long_premium: float = 3.0,
    spot_at_entry: float = 22000.0,
    short_delta: float = -0.30,
    short_iv: float = 14.0,
) -> dict:
    net_credit = short_premium - long_premium
    width = short_strike - long_strike
    max_loss = width - net_credit
    return {
        "short_strike": short_strike,
        "long_strike": long_strike,
        "short_premium": short_premium,
        "long_premium": long_premium,
        "net_credit": net_credit,
        "spot_at_entry": spot_at_entry,
        "short_delta": short_delta,
        "short_iv": short_iv,
        "max_loss": max_loss,
        "width": width,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_simulate_cycle_pure_profit_take_pt50():
    """pt50, credit=12, merged has net_close=4 on row 2 (below threshold 6) — exits early."""
    under = get_under("NIFTY")
    spec = _strategy_spec(
        variant="pt50", profit_take_fraction=0.5, manage_at_dte=21,
    )
    meta = _meta(short_premium=15.0, long_premium=3.0)  # credit=12
    merged = _merged([
        {"date": "2025-03-24", "short_close": 10.0, "long_close": 2.0, "net_close": 8.0, "dte": 30},
        {"date": "2025-03-26", "short_close": 5.0, "long_close": 1.0, "net_close": 4.0, "dte": 28},
        {"date": "2025-03-28", "short_close": 3.0, "long_close": 0.5, "net_close": 2.5, "dte": 26},
    ])
    out = simulate_cycle_pure(
        strategy_spec=spec, under=under, spread_meta=meta,
        merged_legs=merged,
        entry_date=date(2025, 3, 20),
        expiry_date=date(2025, 4, 24),
        spot_at_expiry=22050.0,
    )

    assert isinstance(out, SimulatedTrade)
    trade = out.spread_trade
    assert trade.outcome == "profit_take"
    assert trade.exit_date == date(2025, 3, 26)
    assert trade.dte_exit == 28
    # pnl_per_share = 12 - 4 = 8
    assert trade.pnl_per_share == pytest.approx(8.0)
    # gross = 8 * 65 = 520
    assert trade.gross_pnl_contract == pytest.approx(8.0 * under.lot_size)

    expected_cost = spread_roundtrip_cost(
        short_entry_premium=15.0, short_exit_premium=5.0,
        long_entry_premium=3.0, long_exit_premium=1.0,
        lot=under.lot_size, closed_before_expiry=True,
        settle_intrinsic_long=0.0,
    )
    assert trade.txn_cost_contract == pytest.approx(expected_cost)
    assert trade.pnl_contract == pytest.approx(
        8.0 * under.lot_size - expected_cost
    )
    # width and strikes propagated
    assert trade.width == pytest.approx(100.0)
    assert trade.short_strike == pytest.approx(21850.0)
    assert trade.long_strike == pytest.approx(21750.0)
    # entry metadata copied
    assert trade.entry_delta == pytest.approx(-0.30)
    assert trade.entry_iv == pytest.approx(14.0)
    assert trade.spot_entry == pytest.approx(22000.0)
    assert trade.spot_exit == pytest.approx(22050.0)
    # March size mult = 1.2
    assert trade.size_mult == pytest.approx(1.2)
    # buying_power = max_loss * lot * margin_mult * size_mult
    #              = 88 * 65 * 1.5 * 1.2
    assert trade.buying_power == pytest.approx(88.0 * 65 * 1.5 * 1.2)


def test_simulate_cycle_pure_hte_expired_worthless():
    """HTE (pt=1.0, manage=None), empty merged, spot way above short strike → expired_worthless."""
    under = get_under("NIFTY")
    spec = _strategy_spec(
        variant="hte", profit_take_fraction=1.0, manage_at_dte=None,
    )
    meta = _meta(short_premium=15.0, long_premium=3.0)  # credit=12

    out = simulate_cycle_pure(
        strategy_spec=spec, under=under, spread_meta=meta,
        merged_legs=_merged([]),
        entry_date=date(2025, 2, 18),  # Feb → size_mult=1.0
        expiry_date=date(2025, 3, 27),
        spot_at_expiry=22500.0,  # well above short_strike=21850
    )
    trade = out.spread_trade
    assert trade.outcome == "expired_worthless"
    assert trade.exit_date == date(2025, 3, 27)
    assert trade.dte_exit == 0
    # full credit retained
    assert trade.pnl_per_share == pytest.approx(12.0)
    # no exit orders, only entry + (no settlement since long leg OTM)
    expected_cost = spread_roundtrip_cost(
        short_entry_premium=15.0, short_exit_premium=0.0,
        long_entry_premium=3.0, long_exit_premium=0.0,
        lot=under.lot_size, closed_before_expiry=False,
        settle_intrinsic_long=0.0,
    )
    assert trade.txn_cost_contract == pytest.approx(expected_cost)
    assert trade.gross_pnl_contract == pytest.approx(12.0 * under.lot_size)
    assert trade.pnl_contract == pytest.approx(
        12.0 * under.lot_size - expected_cost
    )
    assert trade.size_mult == pytest.approx(1.0)


def test_simulate_cycle_pure_max_loss_settles_with_long_itm():
    """Both legs ITM at expiry → max_loss, and settlement STT applies on long leg."""
    under = get_under("NIFTY")
    spec = _strategy_spec(
        variant="hte", profit_take_fraction=1.0, manage_at_dte=None,
    )
    meta = _meta(short_premium=15.0, long_premium=3.0)  # credit=12, width=100

    out = simulate_cycle_pure(
        strategy_spec=spec, under=under, spread_meta=meta,
        merged_legs=_merged([]),
        entry_date=date(2025, 2, 18),
        expiry_date=date(2025, 3, 27),
        spot_at_expiry=21500.0,  # < long_strike=21750 ⇒ max_loss
    )
    trade = out.spread_trade
    assert trade.outcome == "max_loss"
    # pnl = credit - width = 12 - 100 = -88
    assert trade.pnl_per_share == pytest.approx(-88.0)
    # settlement intrinsic on long = 21750 - 21500 = 250
    expected_cost = spread_roundtrip_cost(
        short_entry_premium=15.0, short_exit_premium=0.0,
        long_entry_premium=3.0, long_exit_premium=0.0,
        lot=under.lot_size, closed_before_expiry=False,
        settle_intrinsic_long=250.0,
    )
    assert trade.txn_cost_contract == pytest.approx(expected_cost)


def test_simulated_trade_cycle_id_matches_helper():
    """SimulatedTrade.cycle_id matches the engine.cycles.cycle_id helper output."""
    under = get_under("NIFTY")
    spec = _strategy_spec(
        variant="hte", profit_take_fraction=1.0, manage_at_dte=None,
        strategy_version="3.0.0",
    )
    out = simulate_cycle_pure(
        strategy_spec=spec, under=under, spread_meta=_meta(),
        merged_legs=_merged([]),
        entry_date=date(2025, 2, 18),
        expiry_date=date(2025, 3, 27),
        spot_at_expiry=22500.0,
    )
    expected = cycle_id("NIFTY", date(2025, 3, 27), "3.0.0")
    assert out.cycle_id == expected
    assert out.cycle_id == "NIFTY:2025-03-27:3.0.0"


def test_simulated_trade_trade_id_16_hex_and_varies_by_strike():
    """trade_id is a 16-char hex digest that changes when strikes change."""
    under = get_under("NIFTY")
    spec = _strategy_spec(
        variant="hte", profit_take_fraction=1.0, manage_at_dte=None,
    )

    def _make(short_strike: float, long_strike: float) -> SimulatedTrade:
        meta = _meta(short_strike=short_strike, long_strike=long_strike)
        return simulate_cycle_pure(
            strategy_spec=spec, under=under, spread_meta=meta,
            merged_legs=_merged([]),
            entry_date=date(2025, 2, 18),
            expiry_date=date(2025, 3, 27),
            spot_at_expiry=22500.0,
        )

    a = _make(21850.0, 21750.0)
    b = _make(21900.0, 21800.0)

    for tid in (a.trade_id, b.trade_id):
        assert len(tid) == 16
        assert all(c in "0123456789abcdef" for c in tid)

    assert a.trade_id != b.trade_id

    # Sanity: helper gives same digest when called with the same args.
    expected_a = trade_id(
        underlying="NIFTY",
        expiry_date=date(2025, 3, 27),
        short_strike=21850.0,
        long_strike=21750.0,
        width=a.spread_trade.width,
        delta_target=spec.universe.delta_target,
        exit_variant=spec.exit_rule.variant,
        entry_date=date(2025, 2, 18),
    )
    assert a.trade_id == expected_a


def test_simulate_cycle_pure_profit_take_precedence():
    """Both pt and manage_at_dte in spec: pt should fire first (matches exits.decide_exit branch order)."""
    under = get_under("NIFTY")
    spec = _strategy_spec(
        variant="pt50", profit_take_fraction=0.5, manage_at_dte=21,
    )
    meta = _meta(short_premium=14.0, long_premium=4.0)  # credit=10
    # Row 1 trips pt (net_close 4 ≤ 5 threshold) at dte 30 (> 21)
    # Row 2 is at dte 20 (would have been managed if pt didn't fire first)
    merged = _merged([
        {"date": "2025-03-24", "short_close": 5.0, "long_close": 1.0, "net_close": 4.0, "dte": 30},
        {"date": "2025-04-04", "short_close": 6.0, "long_close": 1.0, "net_close": 5.0, "dte": 20},
    ])
    out = simulate_cycle_pure(
        strategy_spec=spec, under=under, spread_meta=meta,
        merged_legs=merged,
        entry_date=date(2025, 3, 20),
        expiry_date=date(2025, 4, 24),
        spot_at_expiry=22050.0,
    )
    assert out.spread_trade.outcome == "profit_take"
    assert out.spread_trade.exit_date == date(2025, 3, 24)
    assert out.spread_trade.dte_exit == 30


def test_simulate_cycle_pure_width_fallback_when_not_in_meta():
    """If spread_meta omits 'width', the engine derives it as short - long."""
    under = get_under("NIFTY")
    spec = _strategy_spec(
        variant="hte", profit_take_fraction=1.0, manage_at_dte=None,
    )
    meta = _meta()
    del meta["width"]  # force fallback

    out = simulate_cycle_pure(
        strategy_spec=spec, under=under, spread_meta=meta,
        merged_legs=_merged([]),
        entry_date=date(2025, 2, 18),
        expiry_date=date(2025, 3, 27),
        spot_at_expiry=22500.0,
    )
    # derived width = short - long = 21850 - 21750 = 100
    assert out.spread_trade.width == pytest.approx(100.0)


def test_simulate_cycle_pure_may_size_mult_half():
    """May entry → size_mult=0.5 (pre-election/budget vol regime)."""
    under = get_under("NIFTY")
    spec = _strategy_spec(
        variant="hte", profit_take_fraction=1.0, manage_at_dte=None,
    )
    meta = _meta()
    out = simulate_cycle_pure(
        strategy_spec=spec, under=under, spread_meta=meta,
        merged_legs=_merged([]),
        entry_date=date(2025, 5, 5),
        expiry_date=date(2025, 5, 29),
        spot_at_expiry=22500.0,
    )
    assert out.spread_trade.size_mult == pytest.approx(0.5)
    # buying_power shrinks accordingly
    assert out.spread_trade.buying_power == pytest.approx(
        meta["max_loss"] * under.lot_size * 1.5 * 0.5
    )
