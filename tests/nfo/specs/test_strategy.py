"""Tests for StrategySpec + nested models (master design §4.1)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

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


def _valid_universe() -> UniverseSpec:
    return UniverseSpec(
        underlyings=["NIFTY"],
        delta_target=0.30,
        delta_tolerance=0.05,
        width_rule="fixed",
        width_value=100.0,
        dte_target=35,
        dte_tolerance=3,
    )


def _valid_trigger() -> TriggerSpec:
    return TriggerSpec(
        score_gates={"min_score": 4},
        specific_pass_gates=["s3_iv_rv", "s6_trend", "s8_events"],
        event_window_days=10,
        feature_thresholds={"vix_abs_min": 20.0, "iv_rank_min": 0.60},
    )


def _valid_strategy(**overrides) -> dict:
    base = dict(
        strategy_id="v3",
        strategy_version="3.0.0",
        description="V3 credit spread filter",
        universe=_valid_universe(),
        feature_set=["vix", "iv_rank", "trend_score", "event_risk"],
        trigger_rule=_valid_trigger(),
        selection_rule=SelectionSpec(
            mode="cycle_matched",
            preferred_exit_variant="hte",
        ),
        entry_rule=EntrySpec(),
        exit_rule=ExitSpec(
            variant="hte",
            profit_take_fraction=1.0,
            manage_at_dte=None,
        ),
        capital_rule=CapitalSpec(fixed_capital_inr=1_000_000),
        slippage_rule=SlippageSpec(flat_rupees_per_lot=0.0),
    )
    base.update(overrides)
    return base


def test_universe_accepts_nifty():
    u = _valid_universe()
    assert u.underlyings == ["NIFTY"]


def test_universe_rejects_delta_out_of_range():
    with pytest.raises(ValidationError):
        UniverseSpec(underlyings=["NIFTY"], delta_target=1.5,
                     delta_tolerance=0.05, width_rule="fixed",
                     width_value=100.0, dte_target=35, dte_tolerance=3)


def test_universe_fixed_requires_width_value():
    with pytest.raises(ValidationError):
        UniverseSpec(underlyings=["NIFTY"], delta_target=0.30,
                     delta_tolerance=0.05, width_rule="fixed",
                     width_value=None, dte_target=35, dte_tolerance=3)


def test_selection_mode_literal():
    with pytest.raises(ValidationError):
        SelectionSpec(mode="bogus", preferred_exit_variant="hte")


def test_entry_default_ok():
    e = EntrySpec()
    assert e.earliest_entry_relative_to_first_fire == 0
    assert e.session_snap_rule == "forward_only"
    assert e.allow_pre_fire_entry is False


def test_exit_hte_requires_no_manage_dte():
    with pytest.raises(ValidationError, match="hte"):
        ExitSpec(variant="hte", profit_take_fraction=1.0, manage_at_dte=5)


def test_exit_hte_requires_pt_1():
    with pytest.raises(ValidationError, match="hte"):
        ExitSpec(variant="hte", profit_take_fraction=0.5, manage_at_dte=None)


def test_exit_pt50_ok():
    e = ExitSpec(variant="pt50", profit_take_fraction=0.5, manage_at_dte=21)
    assert e.variant == "pt50"


def test_strategy_roundtrip():
    s = StrategySpec.model_validate(_valid_strategy())
    assert s.strategy_id == "v3"
    assert s.strategy_version == "3.0.0"


def test_strategy_rejects_bad_semver():
    with pytest.raises(ValidationError):
        StrategySpec.model_validate(_valid_strategy(strategy_version="3.0"))


def test_strategy_rejects_extra_fields():
    with pytest.raises(ValidationError):
        StrategySpec.model_validate({**_valid_strategy(), "surprise": True})


def test_live_rule_forbids_pre_fire_entry():
    bad = _valid_strategy(
        selection_rule=SelectionSpec(mode="live_rule", preferred_exit_variant="hte"),
        entry_rule=EntrySpec(allow_pre_fire_entry=True),
    )
    with pytest.raises(ValidationError, match="live_rule"):
        StrategySpec.model_validate(bad)


def test_live_rule_forbids_nonzero_earliest_entry():
    bad = _valid_strategy(
        selection_rule=SelectionSpec(mode="live_rule", preferred_exit_variant="hte"),
        entry_rule=EntrySpec(earliest_entry_relative_to_first_fire=2),
    )
    with pytest.raises(ValidationError, match="live_rule"):
        StrategySpec.model_validate(bad)
