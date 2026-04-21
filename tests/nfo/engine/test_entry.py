"""Unit tests for engine.entry.resolve_entry_date (master design §6.3, §12 item 3)."""
from __future__ import annotations

from datetime import date

import pytest

from nfo.engine.entry import resolve_entry_date
from nfo.specs.strategy import (
    CapitalSpec, EntrySpec, ExitSpec, SelectionSpec, SlippageSpec,
    StrategySpec, TriggerSpec, UniverseSpec,
)


def _spec(
    mode: str,
    *,
    allow_pre_fire: bool = False,
    earliest: int = 0,
) -> StrategySpec:
    # live_rule forbids allow_pre_fire_entry=True via a model_validator,
    # so construct the StrategySpec only in combinations it accepts. For
    # defense-in-depth tests we hand-build the nested models directly.
    return StrategySpec(
        strategy_id="test",
        strategy_version="1.0.0",
        description="test",
        universe=UniverseSpec(
            underlyings=["NIFTY"], delta_target=0.30, delta_tolerance=0.05,
            width_rule="fixed", width_value=100.0, dte_target=35, dte_tolerance=3,
        ),
        feature_set=["x"],
        trigger_rule=TriggerSpec(),
        selection_rule=SelectionSpec(mode=mode, preferred_exit_variant="hte"),
        entry_rule=EntrySpec(
            earliest_entry_relative_to_first_fire=earliest,
            allow_pre_fire_entry=allow_pre_fire,
        ),
        exit_rule=ExitSpec(variant="hte", profit_take_fraction=1.0, manage_at_dte=None),
        capital_rule=CapitalSpec(fixed_capital_inr=1_000_000),
        slippage_rule=SlippageSpec(),
    )


SESSIONS = [
    date(2025, 3, 24),   # Mon
    date(2025, 3, 25),   # Tue
    date(2025, 3, 26),   # Wed
    date(2025, 3, 27),   # Thu
    date(2025, 3, 28),   # Fri
    date(2025, 3, 31),   # Mon (weekend skipped)
]


def test_live_rule_returns_fire_date_when_it_is_a_session():
    spec = _spec("live_rule")
    out = resolve_entry_date(
        spec=spec, first_fire_date=date(2025, 3, 24), sessions=SESSIONS,
    )
    assert out == date(2025, 3, 24)


def test_live_rule_snaps_forward_over_weekend():
    spec = _spec("live_rule")
    # Fire on Saturday 2025-03-29 → snap forward to Monday 2025-03-31
    out = resolve_entry_date(
        spec=spec, first_fire_date=date(2025, 3, 29), sessions=SESSIONS,
    )
    assert out == date(2025, 3, 31)


def test_live_rule_returns_none_past_last_session():
    spec = _spec("live_rule")
    out = resolve_entry_date(
        spec=spec, first_fire_date=date(2025, 4, 1), sessions=SESSIONS,
    )
    assert out is None


def test_live_rule_rejects_pre_fire_entry_flag():
    # StrategySpec's model_validator catches this; build-by-hand and bypass
    # validation is non-trivial here, so assert the function itself also guards.
    # We do this by constructing a plain namespace that mimics the spec.
    from types import SimpleNamespace
    fake_spec = SimpleNamespace(
        selection_rule=SimpleNamespace(mode="live_rule"),
        entry_rule=SimpleNamespace(allow_pre_fire_entry=True,
                                   earliest_entry_relative_to_first_fire=0),
    )
    with pytest.raises(ValueError, match="live_rule"):
        resolve_entry_date(
            spec=fake_spec, first_fire_date=date(2025, 3, 24), sessions=SESSIONS,
        )


def test_cycle_matched_uses_canonical_date_when_pre_fire_allowed():
    spec = _spec("cycle_matched", allow_pre_fire=True)
    out = resolve_entry_date(
        spec=spec, first_fire_date=date(2025, 3, 26),
        sessions=SESSIONS, canonical_entry_date=date(2025, 3, 20),
    )
    assert out == date(2025, 3, 20)


def test_cycle_matched_snaps_forward_when_pre_fire_not_allowed():
    spec = _spec("cycle_matched", allow_pre_fire=False)
    out = resolve_entry_date(
        spec=spec, first_fire_date=date(2025, 3, 29),  # weekend
        sessions=SESSIONS, canonical_entry_date=date(2025, 3, 20),
    )
    # Pre-fire disabled → ignore canonical, snap forward from fire date
    assert out == date(2025, 3, 31)


def test_day_matched_returns_fire_date():
    spec = _spec("day_matched")
    out = resolve_entry_date(
        spec=spec, first_fire_date=date(2025, 3, 26), sessions=SESSIONS,
    )
    assert out == date(2025, 3, 26)
