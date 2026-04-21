"""Tests that v3_frozen.yaml matches docs/v3-spec-frozen.md contract."""
from __future__ import annotations

from pathlib import Path

import pytest

from nfo.specs.loader import load_strategy, reset_registry_for_tests


REPO_ROOT = Path(__file__).resolve().parents[3]
V3_PATH = REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml"


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")


def test_v3_loads():
    spec, _ = load_strategy(V3_PATH)
    assert spec.strategy_id == "v3"
    assert spec.strategy_version == "3.0.0"


def test_v3_universe_matches_frozen_doc():
    spec, _ = load_strategy(V3_PATH)
    assert spec.universe.underlyings == ["NIFTY"]
    assert spec.universe.delta_target == 0.30
    assert spec.universe.width_rule == "fixed"
    assert spec.universe.width_value == 100.0
    assert spec.universe.dte_target == 35


def test_v3_trigger_specific_pass_gate():
    spec, _ = load_strategy(V3_PATH)
    assert set(spec.trigger_rule.specific_pass_gates) == {"s3_iv_rv", "s6_trend", "s8_events"}


def test_v3_selection_is_cycle_matched_hte():
    spec, _ = load_strategy(V3_PATH)
    assert spec.selection_rule.mode == "cycle_matched"
    assert spec.exit_rule.variant == "hte"
    assert spec.exit_rule.manage_at_dte is None
