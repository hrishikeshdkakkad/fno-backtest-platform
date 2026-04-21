"""Tests for monitor-research parity (master design §10.4)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from nfo.monitor.parity import (
    ParityMismatch,
    ParityReport,
    compare_monitor_vs_research,
)
from nfo.monitor.snapshot import MonitorSnapshot
from nfo.monitor.store import append_snapshot
from nfo.specs.strategy import (
    CapitalSpec, EntrySpec, ExitSpec, SelectionSpec, SlippageSpec,
    StrategySpec, TriggerSpec, UniverseSpec,
)


def _v3_spec() -> StrategySpec:
    return StrategySpec(
        strategy_id="v3", strategy_version="3.0.0", description="t",
        universe=UniverseSpec(
            underlyings=["NIFTY"], delta_target=0.30, delta_tolerance=0.05,
            width_rule="fixed", width_value=100.0, dte_target=35, dte_tolerance=3,
        ),
        feature_set=["vix_abs"],
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


def _features_for(dates: list[date], *, fire_mask: list[bool]) -> pd.DataFrame:
    rows = []
    for d, fires in zip(dates, fire_mask):
        if fires:
            rows.append({
                "date": pd.Timestamp(d),
                "vix": 25.0, "vix_pct_3mo": 0.9,
                "iv_minus_rv": 0.5, "iv_rank_12mo": 0.75,
                "trend_score": 3, "dte": 35,
                "event_risk_v3": "none", "target_expiry": "2025-05-29",
            })
        else:
            rows.append({
                "date": pd.Timestamp(d),
                "vix": 10.0, "vix_pct_3mo": 0.1,
                "iv_minus_rv": -5.0, "iv_rank_12mo": 0.2,
                "trend_score": 0, "dte": 35,
                "event_risk_v3": "none", "target_expiry": "2025-05-29",
            })
    return pd.DataFrame(rows)


def _seed_snap(root, ts: datetime, trigger_passed: bool):
    snap = MonitorSnapshot(
        snapshot_id="a" * 16, timestamp=ts,
        strategy_spec_id="v3", strategy_version="3.0.0",
        strategy_spec_hash="h" * 64, underlying="NIFTY",
        cycle_id="NIFTY:2025-05-29:3.0.0",
        target_expiry=date(2025, 5, 29),
        current_state="watch", current_grade="B",
        trigger_passed=trigger_passed,
        trigger_details={"s3": True, "s6": True, "s8": True} if trigger_passed else {"s3": False, "s6": False, "s8": False},
        reason_codes=[],
    )
    append_snapshot(snap, root=root)


def test_parity_happy_path(tmp_path):
    _seed_snap(tmp_path, datetime(2025, 3, 24, 10, 0, tzinfo=timezone.utc), True)
    _seed_snap(tmp_path, datetime(2025, 3, 25, 10, 0, tzinfo=timezone.utc), False)
    df = _features_for([date(2025, 3, 24), date(2025, 3, 25)], fire_mask=[True, False])
    atr = pd.Series([100.0, 100.0], index=df["date"])
    report = compare_monitor_vs_research(
        spec=_v3_spec(), monitor_jsonl_root=tmp_path,
        features_df=df, atr_series=atr,
    )
    assert report.total_snapshots == 2
    assert report.matched == 2
    assert report.mismatches == []
    assert report.ok is True


def test_parity_detects_mismatch(tmp_path):
    # Snapshot says passed=True, but features for that day don't fire
    _seed_snap(tmp_path, datetime(2025, 3, 24, 10, 0, tzinfo=timezone.utc), True)
    df = _features_for([date(2025, 3, 24)], fire_mask=[False])
    atr = pd.Series([100.0], index=df["date"])
    report = compare_monitor_vs_research(
        spec=_v3_spec(), monitor_jsonl_root=tmp_path,
        features_df=df, atr_series=atr,
    )
    assert report.total_snapshots == 1
    assert report.matched == 0
    assert len(report.mismatches) == 1
    mm = report.mismatches[0]
    assert mm.monitor_trigger_passed is True
    assert mm.engine_trigger_passed is False
    assert report.ok is False


def test_parity_missing_features_row(tmp_path):
    _seed_snap(tmp_path, datetime(2025, 3, 24, 10, 0, tzinfo=timezone.utc), True)
    # features_df has no entry for 2025-03-24
    df = _features_for([date(2025, 3, 25)], fire_mask=[True])
    atr = pd.Series([100.0], index=df["date"])
    report = compare_monitor_vs_research(
        spec=_v3_spec(), monitor_jsonl_root=tmp_path,
        features_df=df, atr_series=atr,
    )
    assert len(report.mismatches) == 1
    assert report.mismatches[0].engine_detail.get("missing") is True


def test_parity_filters_by_date_range(tmp_path):
    _seed_snap(tmp_path, datetime(2025, 3, 24, 10, 0, tzinfo=timezone.utc), True)
    _seed_snap(tmp_path, datetime(2025, 4, 24, 10, 0, tzinfo=timezone.utc), True)
    df = _features_for([date(2025, 3, 24), date(2025, 4, 24)], fire_mask=[True, True])
    atr = pd.Series([100.0, 100.0], index=df["date"])
    report = compare_monitor_vs_research(
        spec=_v3_spec(), monitor_jsonl_root=tmp_path,
        features_df=df, atr_series=atr,
        start=date(2025, 4, 1), end=date(2025, 4, 30),
    )
    assert report.total_snapshots == 1   # only April snapshot in range


def test_parity_report_ok_alias():
    r = ParityReport(total_snapshots=5, matched=5, mismatches=[])
    assert r.ok is True
    r2 = ParityReport(total_snapshots=5, matched=4, mismatches=[
        ParityMismatch(
            snapshot_id="x", timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            monitor_trigger_passed=True, engine_trigger_passed=False,
            monitor_detail={}, engine_detail={},
        )
    ])
    assert r2.ok is False
