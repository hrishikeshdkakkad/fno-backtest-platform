"""Tests for top-level index generator (master design §8.2)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from nfo.datasets.staleness import HashSources
from nfo.reporting.artifacts import open_run_directory
from nfo.reporting.index import IndexResult, generate_index
from nfo.specs.manifest import RunManifest


def _make_run(root: Path, run_id: str, study_type: str, strategy_hash: str,
              dataset_hashes: dict[str, str], created: datetime) -> None:
    rd = open_run_directory(root=root, run_id=run_id)
    m = RunManifest(
        run_id=run_id,
        created_at=created,
        code_version="a1b2c3d",
        study_spec_hash="x" * 64,
        strategy_spec_hash=strategy_hash,
        strategy_id="v3",
        strategy_version="3.0.0",
        study_type=study_type,
        selection_mode="cycle_matched",
        dataset_hashes=dataset_hashes,
        window_start=date(2024, 2, 1),
        window_end=date(2026, 4, 18),
        artifacts=[],
        status="ok",
        duration_seconds=1.0,
    )
    rd.write_manifest(m)


def test_generate_index_lists_all_runs(tmp_path):
    runs_root = tmp_path / "runs"
    _make_run(runs_root, "r1", "capital_analysis", "s" * 64, {"ds": "d" * 64},
              datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc))
    _make_run(runs_root, "r2", "robustness", "s" * 64, {"ds": "d" * 64},
              datetime(2026, 4, 21, 11, 0, tzinfo=timezone.utc))
    sources = HashSources(
        strategy_hash_fn=lambda sid, ver: "s" * 64,
        dataset_hash_fn=lambda did: "d" * 64,
    )
    res: IndexResult = generate_index(runs_root=runs_root, out_root=tmp_path, sources=sources)
    md = (tmp_path / "index.md").read_text()
    latest = json.loads((tmp_path / "latest.json").read_text())
    assert "r1" in md and "r2" in md
    assert latest["capital_analysis"]["run_id"] == "r1"
    assert latest["robustness"]["run_id"] == "r2"
    assert res.total_runs == 2
    assert res.stale_runs == 0


def test_generate_index_marks_stale(tmp_path):
    runs_root = tmp_path / "runs"
    _make_run(runs_root, "r1", "capital_analysis", "s" * 64, {"ds": "d" * 64},
              datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc))
    sources = HashSources(
        strategy_hash_fn=lambda sid, ver: "NEW" + "s" * 61,
        dataset_hash_fn=lambda did: "d" * 64,
    )
    res = generate_index(runs_root=runs_root, out_root=tmp_path, sources=sources)
    md = (tmp_path / "index.md").read_text()
    assert "stale" in md.lower()
    assert res.stale_runs == 1


def test_generate_index_handles_empty(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    sources = HashSources(strategy_hash_fn=lambda s, v: None, dataset_hash_fn=lambda d: None)
    res = generate_index(runs_root=runs_root, out_root=tmp_path, sources=sources)
    assert res.total_runs == 0
    assert (tmp_path / "index.md").exists()
    assert json.loads((tmp_path / "latest.json").read_text()) == {}
