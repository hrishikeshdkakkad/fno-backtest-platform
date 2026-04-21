"""Tests for reporting.master_summary.generate_master_summary (master design §9.3)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from nfo.reporting.artifacts import open_run_directory
from nfo.reporting.master_summary import generate_master_summary
from nfo.specs.manifest import RunManifest


def _seed_run(
    root: Path,
    run_id: str,
    study_type: str,
    created: datetime,
    *,
    metrics: dict | None = None,
    strategy_id: str = "v3",
    strategy_version: str = "3.0.0",
    selection_mode: str = "cycle_matched",
) -> None:
    rd = open_run_directory(root=root, run_id=run_id)
    m = RunManifest(
        run_id=run_id, created_at=created,
        code_version="abc1234",
        study_spec_hash="x" * 64, strategy_spec_hash="s" * 64,
        strategy_id=strategy_id, strategy_version=strategy_version,
        study_type=study_type,  # type: ignore[arg-type]
        selection_mode=selection_mode,  # type: ignore[arg-type]
        dataset_hashes={},
        window_start=date(2024, 2, 1), window_end=date(2026, 4, 18),
        artifacts=[], status="ok", duration_seconds=1.0,
    )
    rd.write_manifest(m)
    rd.write_metrics(metrics or {"headline": 42.0})


def test_generate_master_summary_empty(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    out_path = tmp_path / "master_summary.md"
    res = generate_master_summary(runs_root=runs_root, out_path=out_path)
    assert res.total_runs == 0
    assert out_path.exists()


def test_generate_master_summary_picks_latest_per_study(tmp_path):
    runs_root = tmp_path / "runs"
    _seed_run(runs_root, "r-old", "capital_analysis",
              datetime(2026, 4, 1, tzinfo=timezone.utc),
              metrics={"total_pnl_inr": 100.0})
    _seed_run(runs_root, "r-new", "capital_analysis",
              datetime(2026, 4, 22, tzinfo=timezone.utc),
              metrics={"total_pnl_inr": 999.0})
    _seed_run(runs_root, "r-rob", "robustness",
              datetime(2026, 4, 10, tzinfo=timezone.utc),
              metrics={"sharpe": 2.3})
    out_path = tmp_path / "master_summary.md"
    res = generate_master_summary(runs_root=runs_root, out_path=out_path)
    assert res.total_runs == 3
    assert res.latest_per_study == {
        "capital_analysis": "r-new", "robustness": "r-rob",
    }
    content = out_path.read_text()
    assert "r-new" in content
    assert "r-rob" in content
    # older run should NOT appear as the latest entry for capital_analysis
    # (it can appear elsewhere, e.g. a run-count footer, but the heading block
    # should show r-new).
    cap_heading_start = content.index("## capital_analysis")
    next_block = content[cap_heading_start:cap_heading_start + 1000]
    assert "r-new" in next_block
    assert "999" in next_block   # the headline metric


def test_generate_master_summary_headers(tmp_path):
    runs_root = tmp_path / "runs"
    _seed_run(runs_root, "r1", "capital_analysis",
              datetime(2026, 4, 22, tzinfo=timezone.utc))
    out_path = tmp_path / "master_summary.md"
    generate_master_summary(runs_root=runs_root, out_path=out_path)
    content = out_path.read_text()
    assert content.startswith("# NFO Platform — Master Summary")
    assert "## capital_analysis" in content


def test_generate_master_summary_handles_missing_metrics(tmp_path):
    """A run without metrics.json should be listed but with 'no metrics' note."""
    runs_root = tmp_path / "runs"
    rd = open_run_directory(root=runs_root, run_id="r-bare")
    m = RunManifest(
        run_id="r-bare", created_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
        code_version="abc",
        study_spec_hash="x" * 64, strategy_spec_hash="s" * 64,
        strategy_id="v3", strategy_version="3.0.0",
        study_type="robustness", selection_mode="cycle_matched",
        dataset_hashes={},
        window_start=date(2024, 2, 1), window_end=date(2026, 4, 18),
        artifacts=[], status="ok", duration_seconds=1.0,
    )
    rd.write_manifest(m)
    # Intentionally skip write_metrics

    out_path = tmp_path / "master_summary.md"
    res = generate_master_summary(runs_root=runs_root, out_path=out_path)
    assert res.total_runs == 1
    assert "r-bare" in out_path.read_text()
