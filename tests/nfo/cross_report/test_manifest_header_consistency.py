"""Cross-report consistency — header facts must match manifest facts."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from nfo.specs.manifest import RunManifest


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = REPO_ROOT / "results" / "nfo" / "runs"


def _all_run_dirs() -> list[Path]:
    if not RUNS_DIR.exists():
        return []
    return [p for p in RUNS_DIR.iterdir() if (p / "manifest.json").exists()]


@pytest.mark.parametrize("run_dir", _all_run_dirs(), ids=lambda p: p.name)
def test_report_header_matches_manifest(run_dir):
    manifest = RunManifest.model_validate_json((run_dir / "manifest.json").read_text())
    report = (run_dir / "report.md").read_text()
    assert f"`{manifest.run_id}`" in report
    assert f"`{manifest.strategy_id}`" in report
    assert f"`{manifest.strategy_version}`" in report
    assert manifest.selection_mode in report
    assert manifest.window_start.isoformat() in report
    assert manifest.window_end.isoformat() in report


@pytest.mark.parametrize("run_dir", _all_run_dirs(), ids=lambda p: p.name)
def test_manifest_artifacts_exist(run_dir):
    manifest = RunManifest.model_validate_json((run_dir / "manifest.json").read_text())
    for rel in manifest.artifacts:
        assert (run_dir / rel).exists(), f"missing artifact: {rel}"


@pytest.mark.parametrize("run_dir", _all_run_dirs(), ids=lambda p: p.name)
def test_run_id_format(run_dir):
    manifest = RunManifest.model_validate_json((run_dir / "manifest.json").read_text())
    assert re.match(r"^\d{8}T\d{6}-[a-z0-9_]+-[0-9a-f]{6}$", manifest.run_id), manifest.run_id
