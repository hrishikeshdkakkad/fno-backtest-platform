"""Unit tests for engine.triggers — TriggerEvaluator.

Keep these tests spec-driven: use StrategySpec to drive the evaluator and
assert behavior. Legacy parity lives in test_triggers_parity.py.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from nfo.engine.triggers import TriggerEvaluator, FireRow
from nfo.specs.strategy import (
    CapitalSpec, EntrySpec, ExitSpec, SelectionSpec, SlippageSpec,
    StrategySpec, TriggerSpec, UniverseSpec,
)


def _v3_like_spec() -> StrategySpec:
    return StrategySpec(
        strategy_id="test_v3",
        strategy_version="3.0.0",
        description="test",
        universe=UniverseSpec(
            underlyings=["NIFTY"], delta_target=0.30, delta_tolerance=0.05,
            width_rule="fixed", width_value=100.0, dte_target=35, dte_tolerance=3,
        ),
        feature_set=["vix_abs", "vix_pct_3mo", "iv_rank", "iv_minus_rv", "trend_score", "event_risk_v3"],
        trigger_rule=TriggerSpec(
            score_gates={"min_score": 4},
            specific_pass_gates=["s3_iv_rv", "s6_trend", "s8_events"],
            event_window_days=10,
            feature_thresholds={
                "iv_minus_rv_min_vp": -2.0,
                "trend_score_min": 2.0,
                "vix_abs_min": 20.0,
                "vix_pct_3mo_min": 0.80,
                "iv_rank_min": 0.60,
            },
        ),
        selection_rule=SelectionSpec(mode="cycle_matched", preferred_exit_variant="hte"),
        entry_rule=EntrySpec(allow_pre_fire_entry=True),
        exit_rule=ExitSpec(variant="hte", profit_take_fraction=1.0, manage_at_dte=None),
        capital_rule=CapitalSpec(fixed_capital_inr=1_000_000),
        slippage_rule=SlippageSpec(),
    )


def test_evaluator_fires_when_all_gates_pass():
    spec = _v3_like_spec()
    ev = TriggerEvaluator(spec)
    row = pd.Series({
        "date": pd.Timestamp("2025-03-24"),
        "vix": 22.0,
        "vix_pct_3mo": 0.85,
        "iv_minus_rv": 0.5,
        "iv_rank_12mo": 0.70,
        "trend_score": 3,
        "dte": 35,
        "event_risk_v3": "none",
    })
    result = ev.evaluate_row(row, atr_value=100.0)
    assert result.fired is True
    assert result.detail["s3"] is True
    assert result.detail["s6"] is True
    assert result.detail["s8"] is True


def test_evaluator_specific_gate_fails_when_core_missing():
    spec = _v3_like_spec()
    ev = TriggerEvaluator(spec)
    row = pd.Series({
        "date": pd.Timestamp("2025-03-24"),
        "vix": 25.0,
        "vix_pct_3mo": 0.90,
        "iv_minus_rv": -5.0,   # s3 FAIL
        "iv_rank_12mo": 0.80,
        "trend_score": 3,
        "dte": 35,
        "event_risk_v3": "none",
    })
    result = ev.evaluate_row(row, atr_value=100.0)
    assert result.fired is False
    assert result.detail["s3"] is False


def test_evaluator_fire_dates_returns_only_firing():
    spec = _v3_like_spec()
    ev = TriggerEvaluator(spec)
    df = pd.DataFrame([
        {"date": pd.Timestamp("2025-03-24"), "vix": 22.0, "vix_pct_3mo": 0.85,
         "iv_minus_rv": 0.5, "iv_rank_12mo": 0.70, "trend_score": 3, "dte": 35,
         "event_risk_v3": "none"},
        {"date": pd.Timestamp("2025-03-25"), "vix": 10.0, "vix_pct_3mo": 0.1,
         "iv_minus_rv": -5.0, "iv_rank_12mo": 0.2, "trend_score": 0, "dte": 34,
         "event_risk_v3": "none"},
    ])
    atr = pd.Series([100.0, 100.0], index=df["date"])
    fires = ev.fire_dates(df, atr)
    assert len(fires) == 1
    assert fires[0][0] == date(2025, 3, 24)
