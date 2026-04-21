"""Unit tests for engine.selection.select_live_rule.

Uses a fake client + stubbed run_cycle_from_dhan monkeypatch so tests run
fast and offline. Parity against the real v3_live_rule_backtest output
lives in tests/nfo/studies/test_live_replay.py.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from nfo.engine.cycles import CycleFires
from nfo.engine.selection import select_live_rule
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


def _live_rule_spec():
    return StrategySpec(
        strategy_id="v3", strategy_version="3.0.1",
        description="test",
        universe=UniverseSpec(
            underlyings=["NIFTY"], delta_target=0.30, delta_tolerance=0.05,
            width_rule="fixed", width_value=100.0, dte_target=35, dte_tolerance=3,
        ),
        feature_set=["vix"],
        trigger_rule=TriggerSpec(),
        selection_rule=SelectionSpec(mode="live_rule", preferred_exit_variant="hte"),
        entry_rule=EntrySpec(allow_pre_fire_entry=False),
        exit_rule=ExitSpec(variant="hte", profit_take_fraction=1.0, manage_at_dte=None),
        capital_rule=CapitalSpec(fixed_capital_inr=1_000_000),
        slippage_rule=SlippageSpec(),
    )


def _make_simulated_trade(entry_date: date, expiry_date: date):
    from nfo.backtest import SpreadTrade
    from nfo.engine.execution import SimulatedTrade

    trade = SpreadTrade(
        underlying="NIFTY", cycle_year=2025, cycle_month=4,
        entry_date=entry_date, expiry_date=expiry_date,
        exit_date=expiry_date, dte_entry=(expiry_date - entry_date).days,
        dte_exit=0, spot_entry=24000.0, spot_exit=24500.0,
        short_strike=23500.0, long_strike=23400.0, width=100.0,
        net_credit=20.0, net_close_at_exit=0.0,
        pnl_per_share=20.0, pnl_contract=1300.0,
        gross_pnl_contract=1300.0, txn_cost_contract=0.0,
        buying_power=100_000.0, outcome="expired_worthless",
        entry_delta=-0.30, entry_iv=12.0,
    )
    return SimulatedTrade(
        spread_trade=trade,
        cycle_id="NIFTY:2025-04-24:3.0.1",
        trade_id="a" * 16,
    )


def test_select_live_rule_snaps_entry_forward(monkeypatch):
    spec = _live_rule_spec()
    cycles = {
        "cid1": CycleFires(
            cycle_id="NIFTY:2025-04-24:3.0.1",
            first_fire_date=date(2025, 3, 29),   # Saturday
            target_expiry=date(2025, 4, 24),
            fire_dates=[date(2025, 3, 29)],
        )
    }
    sessions = [date(2025, 3, 28), date(2025, 3, 31), date(2025, 4, 24)]  # Mon after Sat
    captured = []

    def _fake(*, client, under, strategy_spec, entry_date, expiry_date, spot_daily):
        captured.append((entry_date, expiry_date))
        return _make_simulated_trade(entry_date, expiry_date)

    monkeypatch.setattr("nfo.engine.execution.run_cycle_from_dhan", _fake, raising=True)
    out = select_live_rule(
        cycles, spec, sessions, client=None, under=None, spot_daily=pd.DataFrame(),
    )
    assert len(out) == 1
    assert captured[0][0] == date(2025, 3, 31)   # snapped forward from Saturday
    assert "cycle_id" in out.columns
    assert "trade_id" in out.columns
    assert "first_fire_date" in out.columns
    assert "selection_id" in out.columns
    # first_fire_date should match the original (pre-snap) fire.
    assert out.iloc[0]["first_fire_date"] == "2025-03-29"


def test_select_live_rule_skips_when_no_session_before_expiry(monkeypatch):
    spec = _live_rule_spec()
    cycles = {
        "cid1": CycleFires(
            cycle_id="NIFTY:2025-04-24:3.0.1",
            first_fire_date=date(2025, 4, 25),   # after expiry
            target_expiry=date(2025, 4, 24),
            fire_dates=[date(2025, 4, 25)],
        )
    }
    sessions = [date(2025, 4, 28)]  # next session past expiry
    monkeypatch.setattr(
        "nfo.engine.execution.run_cycle_from_dhan",
        lambda **kw: None, raising=True,
    )
    out = select_live_rule(
        cycles, spec, sessions, client=None, under=None, spot_daily=pd.DataFrame(),
    )
    assert out.empty


def test_select_live_rule_skips_when_simulator_returns_none(monkeypatch):
    spec = _live_rule_spec()
    cycles = {
        "cid1": CycleFires(
            cycle_id="NIFTY:2025-04-24:3.0.1",
            first_fire_date=date(2025, 3, 24),
            target_expiry=date(2025, 4, 24),
            fire_dates=[date(2025, 3, 24)],
        )
    }
    sessions = [date(2025, 3, 24), date(2025, 4, 24)]
    monkeypatch.setattr(
        "nfo.engine.execution.run_cycle_from_dhan",
        lambda **kw: None, raising=True,
    )
    out = select_live_rule(
        cycles, spec, sessions, client=None, under=None, spot_daily=pd.DataFrame(),
    )
    assert out.empty


def test_select_live_rule_enriches_row_with_canonical_ids(monkeypatch):
    spec = _live_rule_spec()
    cycles = {
        "cid1": CycleFires(
            cycle_id="NIFTY:2025-04-24:3.0.1",
            first_fire_date=date(2025, 3, 24),
            target_expiry=date(2025, 4, 24),
            fire_dates=[date(2025, 3, 24)],
        )
    }
    sessions = [date(2025, 3, 24), date(2025, 4, 24)]

    def _fake(*, client, under, strategy_spec, entry_date, expiry_date, spot_daily):
        return _make_simulated_trade(entry_date, expiry_date)

    monkeypatch.setattr("nfo.engine.execution.run_cycle_from_dhan", _fake, raising=True)
    out = select_live_rule(
        cycles, spec, sessions, client=None, under=None, spot_daily=pd.DataFrame(),
    )
    assert len(out) == 1
    row = out.iloc[0]
    assert row["cycle_id"] == "NIFTY:2025-04-24:3.0.1"
    assert row["trade_id"] == "a" * 16
    # selection_id format: cycle_id:mode:variant
    assert row["selection_id"] == "NIFTY:2025-04-24:3.0.1:live_rule:hte"
    # Core SpreadTrade fields are present via asdict().
    assert row["outcome"] == "expired_worthless"
    assert row["pnl_contract"] == 1300.0
    assert row["underlying"] == "NIFTY"
