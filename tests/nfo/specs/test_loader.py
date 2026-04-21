"""Tests for YAML loader + StrategyDriftError (master design §4.2)."""
from __future__ import annotations

import textwrap

import pytest

from nfo.specs.loader import (
    StrategyDriftError,
    load_strategy,
    load_study,
    reset_registry_for_tests,
)


STRAT_YAML = textwrap.dedent("""
    strategy_id: v3
    strategy_version: 3.0.0
    description: V3 credit spread filter
    universe:
      underlyings: [NIFTY]
      delta_target: 0.30
      delta_tolerance: 0.05
      width_rule: fixed
      width_value: 100.0
      dte_target: 35
      dte_tolerance: 3
    feature_set: [vix, iv_rank, trend_score]
    trigger_rule:
      specific_pass_gates: [s3_iv_rv, s6_trend, s8_events]
      event_window_days: 10
      feature_thresholds: {vix_abs_min: 20.0, iv_rank_min: 0.60}
    selection_rule:
      mode: cycle_matched
      preferred_exit_variant: hte
    entry_rule: {}
    exit_rule:
      variant: hte
      profit_take_fraction: 1.0
      manage_at_dte: null
    capital_rule:
      fixed_capital_inr: 1000000
    slippage_rule:
      flat_rupees_per_lot: 0.0
""")


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    reset_registry_for_tests(tmp_path / "registry.json")
    yield


def test_load_strategy_returns_model_and_hash(tmp_path):
    p = tmp_path / "v3.yaml"
    p.write_text(STRAT_YAML)
    spec, h = load_strategy(p)
    assert spec.strategy_id == "v3"
    assert len(h) == 64


def test_load_strategy_rejects_version_drift(tmp_path):
    p = tmp_path / "v3.yaml"
    p.write_text(STRAT_YAML)
    load_strategy(p)
    modified = STRAT_YAML.replace("event_window_days: 10", "event_window_days: 7")
    p.write_text(modified)
    with pytest.raises(StrategyDriftError, match="content hash changed"):
        load_strategy(p)


def test_load_strategy_allows_new_version(tmp_path):
    p = tmp_path / "v3.yaml"
    p.write_text(STRAT_YAML)
    load_strategy(p)
    bumped = STRAT_YAML.replace("strategy_version: 3.0.0", "strategy_version: 3.1.0")
    bumped = bumped.replace("event_window_days: 10", "event_window_days: 7")
    p.write_text(bumped)
    spec, _ = load_strategy(p)
    assert spec.strategy_version == "3.1.0"


def test_load_study(tmp_path):
    strat = tmp_path / "v3.yaml"
    strat.write_text(STRAT_YAML)
    study = tmp_path / "capital.yaml"
    study.write_text(textwrap.dedent(f"""
        study_id: capital_analysis_10l
        study_type: capital_analysis
        strategy_spec_ref: {strat}
        dataset_refs: []
        parameters:
          capital_inr: 1000000
    """))
    spec, h = load_study(study)
    assert spec.study_id == "capital_analysis_10l"
    assert len(h) == 64
