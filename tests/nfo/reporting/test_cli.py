"""CLI test: `python -m nfo.reporting` regenerates index.md + latest.json."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

from nfo.reporting.artifacts import open_run_directory
from nfo.specs.manifest import RunManifest


REPO_ROOT = Path(__file__).resolve().parents[3]


def _seed_run(runs_root: Path, run_id: str, study_type: str) -> None:
    rd = open_run_directory(root=runs_root, run_id=run_id)
    m = RunManifest(
        run_id=run_id,
        created_at=datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),
        code_version="a1b2c3d",
        study_spec_hash="x" * 64,
        strategy_spec_hash="s" * 64,
        strategy_id="v3",
        strategy_version="3.0.0",
        study_type=study_type,
        selection_mode="cycle_matched",
        dataset_hashes={},
        window_start=date(2024, 2, 1),
        window_end=date(2026, 4, 18),
        artifacts=[],
        status="ok",
        duration_seconds=1.0,
    )
    rd.write_manifest(m)


def test_cli_writes_index_and_latest(tmp_path):
    runs_root = tmp_path / "runs"
    _seed_run(runs_root, "20260421T100000-capital_analysis-aaaaaa", "capital_analysis")
    _seed_run(runs_root, "20260421T110000-robustness_default-bbbbbb", "robustness")

    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir()

    env = os.environ.copy()
    result = subprocess.run(
        [".venv/bin/python", "-m", "nfo.reporting",
         "--runs-root", str(runs_root),
         "--out-root", str(tmp_path),
         "--strategies-root", str(strat_dir),
         "--datasets-root", str(tmp_path / "datasets")],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    idx = (tmp_path / "index.md").read_text()
    assert "capital_analysis" in idx
    assert "robustness" in idx
    latest = json.loads((tmp_path / "latest.json").read_text())
    assert set(latest.keys()) == {"capital_analysis", "robustness"}


def test_cli_empty_runs_dir(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    env = os.environ.copy()
    result = subprocess.run(
        [".venv/bin/python", "-m", "nfo.reporting",
         "--runs-root", str(runs_root),
         "--out-root", str(tmp_path),
         "--strategies-root", str(tmp_path / "strategies"),
         "--datasets-root", str(tmp_path / "datasets")],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0
    assert (tmp_path / "index.md").exists()
    assert json.loads((tmp_path / "latest.json").read_text()) == {}


def test_cli_also_writes_master_summary(tmp_path):
    # Seed a run dir
    from nfo.reporting.artifacts import open_run_directory
    from nfo.specs.manifest import RunManifest
    from datetime import date, datetime, timezone

    runs_root = tmp_path / "runs"
    rd = open_run_directory(root=runs_root, run_id="r-ms")
    m = RunManifest(
        run_id="r-ms", created_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
        code_version="abc",
        study_spec_hash="x" * 64, strategy_spec_hash="s" * 64,
        strategy_id="v3", strategy_version="3.0.0",
        study_type="capital_analysis", selection_mode="cycle_matched",
        dataset_hashes={},
        window_start=date(2024, 2, 1), window_end=date(2026, 4, 18),
        artifacts=[], status="ok", duration_seconds=1.0,
    )
    rd.write_manifest(m)
    rd.write_metrics({"total_pnl_inr": 123.0})

    import subprocess, os
    env = os.environ.copy()
    result = subprocess.run(
        [".venv/bin/python", "-m", "nfo.reporting",
         "--runs-root", str(runs_root),
         "--out-root", str(tmp_path),
         "--strategies-root", str(tmp_path / "strategies"),
         "--datasets-root", str(tmp_path / "datasets")],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert (tmp_path / "master_summary.md").exists()
    content = (tmp_path / "master_summary.md").read_text()
    assert "r-ms" in content
