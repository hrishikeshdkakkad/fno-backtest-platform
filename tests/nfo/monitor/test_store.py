"""Tests for monitor.store (per-day JSONL, append-only)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from nfo.monitor.snapshot import MonitorSnapshot
from nfo.monitor.store import append_snapshot, load_snapshots


def _snap(ts: datetime, underlying: str = "NIFTY") -> MonitorSnapshot:
    return MonitorSnapshot(
        snapshot_id="a" * 16,
        timestamp=ts,
        strategy_spec_id="v3", strategy_version="3.0.0",
        strategy_spec_hash="h" * 64,
        underlying=underlying,
        cycle_id=f"{underlying}:2025-05-29:3.0.0",
        target_expiry=date(2025, 5, 29),
        current_state="watch",
        current_grade="B",
        trigger_passed=False,
        trigger_details={"s3": True},
        reason_codes=[],
    )


def test_append_creates_daily_file(tmp_path):
    ts = datetime(2025, 5, 1, 10, 0, tzinfo=timezone.utc)
    out_path = append_snapshot(_snap(ts), root=tmp_path)
    assert out_path.name == "2025-05-01.jsonl"
    assert out_path.parent == tmp_path
    lines = out_path.read_text().splitlines()
    assert len(lines) == 1


def test_append_is_additive(tmp_path):
    ts1 = datetime(2025, 5, 1, 10, 0, tzinfo=timezone.utc)
    ts2 = datetime(2025, 5, 1, 11, 0, tzinfo=timezone.utc)
    append_snapshot(_snap(ts1), root=tmp_path)
    append_snapshot(_snap(ts2), root=tmp_path)
    lines = (tmp_path / "2025-05-01.jsonl").read_text().splitlines()
    assert len(lines) == 2


def test_append_separates_days(tmp_path):
    append_snapshot(
        _snap(datetime(2025, 5, 1, 10, 0, tzinfo=timezone.utc)), root=tmp_path,
    )
    append_snapshot(
        _snap(datetime(2025, 5, 2, 10, 0, tzinfo=timezone.utc)), root=tmp_path,
    )
    assert (tmp_path / "2025-05-01.jsonl").exists()
    assert (tmp_path / "2025-05-02.jsonl").exists()


def test_load_all(tmp_path):
    append_snapshot(_snap(datetime(2025, 5, 1, 10, 0, tzinfo=timezone.utc)), root=tmp_path)
    append_snapshot(_snap(datetime(2025, 5, 2, 10, 0, tzinfo=timezone.utc)), root=tmp_path)
    snaps = load_snapshots(root=tmp_path)
    assert len(snaps) == 2


def test_load_range(tmp_path):
    append_snapshot(_snap(datetime(2025, 5, 1, 10, 0, tzinfo=timezone.utc)), root=tmp_path)
    append_snapshot(_snap(datetime(2025, 5, 2, 10, 0, tzinfo=timezone.utc)), root=tmp_path)
    append_snapshot(_snap(datetime(2025, 5, 3, 10, 0, tzinfo=timezone.utc)), root=tmp_path)
    window = load_snapshots(
        root=tmp_path, start=date(2025, 5, 2), end=date(2025, 5, 2),
    )
    assert len(window) == 1
    assert window[0].timestamp.date() == date(2025, 5, 2)


def test_load_empty_root(tmp_path):
    snaps = load_snapshots(root=tmp_path)
    assert snaps == []
