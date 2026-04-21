"""Tests for RunManifest + DatasetManifest (master design §4.4, §4.5)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from nfo.specs.manifest import DatasetManifest, RunManifest


def _run(**overrides) -> dict:
    base = dict(
        run_id="20260421T143000-capital_analysis-7a3f9b",
        created_at=datetime(2026, 4, 21, 14, 30, 0, tzinfo=timezone.utc),
        code_version="a1b2c3d",
        study_spec_hash="x" * 64,
        strategy_spec_hash="y" * 64,
        strategy_id="v3",
        strategy_version="3.0.0",
        study_type="capital_analysis",
        selection_mode="cycle_matched",
        dataset_hashes={"historical_features_2024-01_2026-04": "z" * 64},
        window_start=date(2024, 2, 1),
        window_end=date(2026, 4, 18),
        artifacts=["manifest.json", "metrics.json", "tables/selected_trades.csv", "report.md"],
        status="ok",
        warnings=[],
        stale_inputs_detected=[],
        duration_seconds=12.4,
    )
    base.update(overrides)
    return base


def test_run_manifest_roundtrip():
    m = RunManifest.model_validate(_run())
    j = m.model_dump_json()
    back = RunManifest.model_validate_json(j)
    assert back == m


def test_run_manifest_rejects_bad_selection_mode():
    with pytest.raises(ValidationError):
        RunManifest.model_validate(_run(selection_mode="bogus"))


def test_run_manifest_dirty_code_version_ok():
    m = RunManifest.model_validate(_run(code_version="a1b2c3d-dirty"))
    assert m.code_version.endswith("-dirty")


def _dataset(**overrides) -> dict:
    base = dict(
        dataset_id="historical_features_2024-01_2026-04",
        dataset_type="features",
        source_paths=[Path("data/nfo/index/NIFTY_2023-12-15_2026-04-18.parquet")],
        date_window=(date(2024, 1, 15), date(2026, 4, 18)),
        row_count=559,
        build_time=datetime(2026, 4, 21, tzinfo=timezone.utc),
        code_version="a1b2c3d",
        upstream_datasets=[],
        parquet_sha256="p" * 64,
        schema_fingerprint="s" * 64,
    )
    base.update(overrides)
    return base


def test_dataset_manifest_roundtrip():
    m = DatasetManifest.model_validate(_dataset())
    back = DatasetManifest.model_validate_json(m.model_dump_json())
    assert back == m


def test_dataset_manifest_allows_no_date_window():
    m = DatasetManifest.model_validate(_dataset(date_window=None))
    assert m.date_window is None
