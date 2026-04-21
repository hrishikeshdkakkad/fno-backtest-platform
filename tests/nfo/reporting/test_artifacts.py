"""Tests for RunDirectory writer (master design §8.1)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from nfo.reporting.artifacts import open_run_directory
from nfo.specs.manifest import RunManifest


def _manifest(run_id: str) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        created_at=datetime(2026, 4, 21, 14, 30, 0, tzinfo=timezone.utc),
        code_version="a1b2c3d",
        study_spec_hash="a" * 64,
        strategy_spec_hash="b" * 64,
        strategy_id="v3",
        strategy_version="3.0.0",
        study_type="capital_analysis",
        selection_mode="cycle_matched",
        dataset_hashes={},
        window_start=date(2024, 2, 1),
        window_end=date(2026, 4, 18),
        artifacts=[],
        status="ok",
        duration_seconds=0.0,
    )


def test_open_run_directory_creates_structure(tmp_path):
    rd = open_run_directory(root=tmp_path, run_id="20260421T143000-test-abcdef")
    assert rd.path.is_dir()
    assert (rd.path / "tables").is_dir()
    assert (rd.path / "logs").is_dir()


def test_write_manifest_adds_to_artifacts(tmp_path):
    rd = open_run_directory(root=tmp_path, run_id="r1")
    m = _manifest("r1")
    rd.write_manifest(m)
    written = json.loads((rd.path / "manifest.json").read_text())
    assert written["run_id"] == "r1"
    assert "manifest.json" in written["artifacts"]


def test_write_metrics_adds_to_artifacts(tmp_path):
    rd = open_run_directory(root=tmp_path, run_id="r2")
    m = _manifest("r2")
    rd.write_manifest(m)
    rd.write_metrics({"total_pnl_inr": 123456.0, "win_rate": 0.875})
    metrics = json.loads((rd.path / "metrics.json").read_text())
    assert metrics["total_pnl_inr"] == 123456.0
    manifest_after = json.loads((rd.path / "manifest.json").read_text())
    assert "metrics.json" in manifest_after["artifacts"]


def test_write_table_csv(tmp_path):
    rd = open_run_directory(root=tmp_path, run_id="r3")
    rd.write_manifest(_manifest("r3"))
    df = pd.DataFrame([{"a": 1, "b": "x"}])
    rd.write_table("selected_trades", df, fmt="csv")
    csv_path = rd.path / "tables" / "selected_trades.csv"
    assert csv_path.exists()
    manifest_after = json.loads((rd.path / "manifest.json").read_text())
    assert "tables/selected_trades.csv" in manifest_after["artifacts"]


def test_write_report_prepends_header(tmp_path):
    rd = open_run_directory(root=tmp_path, run_id="r4")
    rd.write_manifest(_manifest("r4"))
    rd.write_report(body_markdown="## Summary\n\nBody text.\n")
    content = (rd.path / "report.md").read_text()
    assert content.startswith("<!-- methodology:begin -->")
    assert "Body text." in content


def test_write_report_refuses_duplicate_header(tmp_path):
    rd = open_run_directory(root=tmp_path, run_id="r5")
    rd.write_manifest(_manifest("r5"))
    with pytest.raises(ValueError, match="methodology:begin"):
        rd.write_report(body_markdown="<!-- methodology:begin -->\nsneaky\n")
