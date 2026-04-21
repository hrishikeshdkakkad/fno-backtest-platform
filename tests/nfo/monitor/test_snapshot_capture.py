"""Tests for capture_snapshot producer (master design §9)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from nfo.monitor.snapshot import MonitorSnapshot, capture_snapshot
from nfo.specs.strategy import (
    CapitalSpec, EntrySpec, ExitSpec, SelectionSpec, SlippageSpec,
    StrategySpec, TriggerSpec, UniverseSpec,
)


def _v3_spec() -> StrategySpec:
    return StrategySpec(
        strategy_id="v3", strategy_version="3.0.0", description="test",
        universe=UniverseSpec(
            underlyings=["NIFTY"], delta_target=0.30, delta_tolerance=0.05,
            width_rule="fixed", width_value=100.0, dte_target=35, dte_tolerance=3,
        ),
        feature_set=["vix_abs","vix_pct_3mo","iv_rank","iv_minus_rv","trend_score","event_risk_v3"],
        trigger_rule=TriggerSpec(
            score_gates={"min_score": 4},
            specific_pass_gates=["s3_iv_rv","s6_trend","s8_events"],
            event_window_days=10,
            feature_thresholds={
                "iv_minus_rv_min_vp": -2.0, "trend_score_min": 2.0,
                "vix_abs_min": 20.0, "vix_pct_3mo_min": 0.80, "iv_rank_min": 0.60,
            },
        ),
        selection_rule=SelectionSpec(mode="cycle_matched", preferred_exit_variant="hte"),
        entry_rule=EntrySpec(allow_pre_fire_entry=True),
        exit_rule=ExitSpec(variant="hte", profit_take_fraction=1.0, manage_at_dte=None),
        capital_rule=CapitalSpec(fixed_capital_inr=1_000_000),
        slippage_rule=SlippageSpec(),
    )


def test_capture_snapshot_fires_when_gates_pass():
    spec = _v3_spec()
    row = pd.Series({
        "date": pd.Timestamp("2025-03-24"),
        "vix": 25.0, "vix_pct_3mo": 0.90,
        "iv_minus_rv": 0.5, "iv_rank_12mo": 0.75,
        "trend_score": 3, "dte": 35,
        "event_risk_v3": "none",
    })
    snap = capture_snapshot(
        spec=spec, spec_hash="h" * 64,
        features_row=row, atr_value=100.0,
        target_expiry=date(2025, 4, 24),
        current_state="watch",
        now=datetime(2025, 3, 24, 9, 30, tzinfo=timezone.utc),
    )
    assert isinstance(snap, MonitorSnapshot)
    assert snap.strategy_spec_id == "v3"
    assert snap.strategy_version == "3.0.0"
    assert snap.underlying == "NIFTY"
    assert snap.trigger_passed is True
    assert snap.current_state == "watch"
    assert snap.target_expiry == date(2025, 4, 24)
    # trigger_details has the engine's pass dict
    assert "s3" in snap.trigger_details


def test_capture_snapshot_no_fire_when_gates_fail():
    spec = _v3_spec()
    row = pd.Series({
        "date": pd.Timestamp("2025-03-24"),
        "vix": 10.0, "vix_pct_3mo": 0.1,
        "iv_minus_rv": -5.0, "iv_rank_12mo": 0.2,
        "trend_score": 0, "dte": 35,
        "event_risk_v3": "none",
    })
    snap = capture_snapshot(
        spec=spec, spec_hash="h" * 64,
        features_row=row, atr_value=100.0,
        target_expiry=date(2025, 4, 24),
        current_state="watch",
        now=datetime(2025, 3, 24, 9, 30, tzinfo=timezone.utc),
    )
    assert snap.trigger_passed is False


def test_capture_snapshot_defaults_now_to_utc_now():
    spec = _v3_spec()
    row = pd.Series({
        "date": pd.Timestamp("2025-03-24"),
        "vix": 25.0, "vix_pct_3mo": 0.90,
        "iv_minus_rv": 0.5, "iv_rank_12mo": 0.75,
        "trend_score": 3, "dte": 35, "event_risk_v3": "none",
    })
    snap = capture_snapshot(
        spec=spec, spec_hash="h" * 64,
        features_row=row, atr_value=100.0,
        target_expiry=date(2025, 4, 24),
        current_state="watch",
    )
    assert snap.timestamp.tzinfo is not None


def test_capture_snapshot_event_resolver_used():
    spec = _v3_spec()
    row = pd.Series({
        "date": pd.Timestamp("2025-03-24"),
        "vix": 25.0, "vix_pct_3mo": 0.90,
        "iv_minus_rv": 0.5, "iv_rank_12mo": 0.75,
        "trend_score": 3, "dte": 35, "event_risk_v3": "none",
    })
    resolver_calls = []
    def _resolver(entry, dte):
        resolver_calls.append((entry, dte))
        return "high"   # force s8 fail
    snap = capture_snapshot(
        spec=spec, spec_hash="h" * 64,
        features_row=row, atr_value=100.0,
        target_expiry=date(2025, 4, 24),
        current_state="watch",
        now=datetime(2025, 3, 24, 9, 30, tzinfo=timezone.utc),
        event_resolver=_resolver,
    )
    assert resolver_calls, "event_resolver should have been called"
    assert snap.trigger_passed is False
    assert snap.trigger_details["s8"] is False


def test_capture_snapshot_sets_snapshot_id():
    spec = _v3_spec()
    row = pd.Series({
        "date": pd.Timestamp("2025-03-24"),
        "vix": 25.0, "vix_pct_3mo": 0.90,
        "iv_minus_rv": 0.5, "iv_rank_12mo": 0.75,
        "trend_score": 3, "dte": 35, "event_risk_v3": "none",
    })
    snap = capture_snapshot(
        spec=spec, spec_hash="h" * 64,
        features_row=row, atr_value=100.0,
        target_expiry=date(2025, 4, 24),
        current_state="watch",
        now=datetime(2025, 3, 24, 9, 30, tzinfo=timezone.utc),
    )
    assert len(snap.snapshot_id) == 16
    assert all(c in "0123456789abcdef" for c in snap.snapshot_id)


def test_capture_snapshot_cycle_id_matches_helper():
    from nfo.engine.cycles import cycle_id
    spec = _v3_spec()
    row = pd.Series({
        "date": pd.Timestamp("2025-03-24"),
        "vix": 25.0, "vix_pct_3mo": 0.90,
        "iv_minus_rv": 0.5, "iv_rank_12mo": 0.75,
        "trend_score": 3, "dte": 35, "event_risk_v3": "none",
    })
    snap = capture_snapshot(
        spec=spec, spec_hash="h" * 64,
        features_row=row, atr_value=100.0,
        target_expiry=date(2025, 4, 24),
        current_state="watch",
        now=datetime(2025, 3, 24, 9, 30, tzinfo=timezone.utc),
    )
    expected = cycle_id("NIFTY", date(2025, 4, 24), "3.0.0")
    assert snap.cycle_id == expected
