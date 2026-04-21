"""Tests for staleness detection (master design §7.2)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from nfo.datasets.staleness import HashSources, is_run_stale
from nfo.specs.manifest import RunManifest


def _m(*, strategy_hash="s" * 64, dataset_hashes=None) -> RunManifest:
    return RunManifest(
        run_id="r1",
        created_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
        code_version="a1b2c3d",
        study_spec_hash="x" * 64,
        strategy_spec_hash=strategy_hash,
        strategy_id="v3",
        strategy_version="3.0.0",
        study_type="capital_analysis",
        selection_mode="cycle_matched",
        dataset_hashes=dataset_hashes or {},
        window_start=date(2024, 2, 1),
        window_end=date(2026, 4, 18),
        artifacts=[],
        status="ok",
        duration_seconds=1.0,
    )


def test_fresh_when_hashes_match():
    sources = HashSources(
        strategy_hash_fn=lambda sid, ver: "s" * 64,
        dataset_hash_fn=lambda did: "d" * 64,
    )
    assert is_run_stale(_m(dataset_hashes={"ds": "d" * 64}), sources) == []


def test_stale_when_strategy_hash_drifts():
    sources = HashSources(
        strategy_hash_fn=lambda sid, ver: "NEW" + "s" * 61,
        dataset_hash_fn=lambda did: "d" * 64,
    )
    reasons = is_run_stale(_m(dataset_hashes={"ds": "d" * 64}), sources)
    assert any("strategy_spec_hash_changed" in r for r in reasons)


def test_stale_when_dataset_hash_drifts():
    sources = HashSources(
        strategy_hash_fn=lambda sid, ver: "s" * 64,
        dataset_hash_fn=lambda did: "NEW" + "d" * 61,
    )
    reasons = is_run_stale(_m(dataset_hashes={"ds": "d" * 64}), sources)
    assert any("dataset_hash_changed:ds" in r for r in reasons)


def test_stale_when_dataset_missing():
    sources = HashSources(
        strategy_hash_fn=lambda sid, ver: "s" * 64,
        dataset_hash_fn=lambda did: None,
    )
    reasons = is_run_stale(_m(dataset_hashes={"ds": "d" * 64}), sources)
    assert any("dataset_missing:ds" in r for r in reasons)


def test_stale_when_strategy_absent():
    sources = HashSources(
        strategy_hash_fn=lambda sid, ver: None,
        dataset_hash_fn=lambda did: "d" * 64,
    )
    reasons = is_run_stale(_m(dataset_hashes={"ds": "d" * 64}), sources)
    assert any("strategy_missing" in r for r in reasons)
