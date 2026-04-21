"""Tests for MonitorSnapshot schema + build_snapshot_id (master design §9.1)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from nfo.monitor.snapshot import MonitorSnapshot, build_snapshot_id


def _valid(**overrides) -> dict:
    base = dict(
        snapshot_id="a" * 16,
        timestamp=datetime(2026, 4, 22, 9, 30, 0, tzinfo=timezone.utc),
        strategy_spec_id="v3",
        strategy_version="3.0.0",
        strategy_spec_hash="b" * 64,
        underlying="NIFTY",
        cycle_id="NIFTY:2026-05-29:3.0.0",
        target_expiry=date(2026, 5, 29),
        current_state="watch",
        first_fire_date=None,
        current_grade="B+",
        trigger_passed=False,
        trigger_details={"s3": True, "s6": False, "s8": True},
        selection_preview=None,
        proposed_trade=None,
        reason_codes=["watch_only", "trigger_below_score_floor"],
    )
    base.update(overrides)
    return base


def test_roundtrip():
    m = MonitorSnapshot.model_validate(_valid())
    j = m.model_dump_json()
    back = MonitorSnapshot.model_validate_json(j)
    assert back == m


def test_rejects_bad_state():
    with pytest.raises(ValidationError):
        MonitorSnapshot.model_validate(_valid(current_state="bogus"))


def test_rejects_bad_underlying():
    with pytest.raises(ValidationError):
        MonitorSnapshot.model_validate(_valid(underlying="NASDAQ"))


def test_rejects_extra_fields():
    with pytest.raises(ValidationError):
        MonitorSnapshot.model_validate({**_valid(), "surprise": 1})


def test_fire_state_with_first_fire_date():
    m = MonitorSnapshot.model_validate(
        _valid(current_state="fire",
               first_fire_date=date(2026, 4, 22),
               trigger_passed=True)
    )
    assert m.current_state == "fire"
    assert m.first_fire_date == date(2026, 4, 22)


def test_build_snapshot_id_is_hex_16():
    sid = build_snapshot_id(
        strategy_id="v3", strategy_version="3.0.0",
        underlying="NIFTY",
        timestamp=datetime(2026, 4, 22, 9, 30, 0, tzinfo=timezone.utc),
    )
    assert len(sid) == 16
    assert all(c in "0123456789abcdef" for c in sid)


def test_build_snapshot_id_deterministic():
    kw = dict(
        strategy_id="v3", strategy_version="3.0.0",
        underlying="NIFTY",
        timestamp=datetime(2026, 4, 22, 9, 30, 0, tzinfo=timezone.utc),
    )
    assert build_snapshot_id(**kw) == build_snapshot_id(**kw)


def test_build_snapshot_id_changes_on_timestamp():
    base = dict(strategy_id="v3", strategy_version="3.0.0", underlying="NIFTY")
    a = build_snapshot_id(
        **base, timestamp=datetime(2026, 4, 22, 9, 30, tzinfo=timezone.utc)
    )
    b = build_snapshot_id(
        **base, timestamp=datetime(2026, 4, 22, 9, 31, tzinfo=timezone.utc)
    )
    assert a != b
