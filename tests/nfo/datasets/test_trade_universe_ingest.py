"""Tests for datasets.trade_universe.ingest_trade_universe_csv (master design §7.1)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from nfo.datasets.trade_universe import (
    ingest_trade_universe_csv,
    trade_universe_dataset_dir,
)
from nfo.specs.manifest import DatasetManifest


def _seed_csv(path: Path, *, entry_dates=("2024-02-01", "2024-03-05")) -> None:
    df = pd.DataFrame({
        "entry_date": list(entry_dates),
        "expiry_date": ["2024-02-29", "2024-03-28"][: len(entry_dates)],
        "param_delta": [0.30, 0.30][: len(entry_dates)],
        "param_width": [100.0, 100.0][: len(entry_dates)],
        "param_pt": [0.5, 1.0][: len(entry_dates)],
        "pnl_contract": [500.0, -200.0][: len(entry_dates)],
        "outcome": ["profit_take", "expired_worthless"][: len(entry_dates)],
    })
    df.to_csv(path, index=False)


def test_single_csv_ingests(tmp_path):
    p = tmp_path / "trades.csv"
    _seed_csv(p)
    m = ingest_trade_universe_csv(
        csv_paths=[p], dataset_id="ds_single",
        datasets_root=tmp_path / "datasets",
    )
    dest = trade_universe_dataset_dir(tmp_path / "datasets", "ds_single")
    assert (dest / "dataset.parquet").exists()
    assert (dest / "manifest.json").exists()
    assert m.dataset_type == "trade_universe"
    assert m.row_count == 2


def test_multiple_csvs_concatenate_in_order(tmp_path):
    a = tmp_path / "main.csv"
    b = tmp_path / "gaps.csv"
    _seed_csv(a, entry_dates=("2024-01-15",))
    _seed_csv(b, entry_dates=("2024-04-10",))
    m = ingest_trade_universe_csv(
        csv_paths=[a, b], dataset_id="ds_multi",
        datasets_root=tmp_path / "datasets",
    )
    assert m.row_count == 2
    assert m.date_window == (date(2024, 1, 15), date(2024, 4, 10))


def test_manifest_fields_populated(tmp_path):
    p = tmp_path / "trades.csv"
    _seed_csv(p)
    m = ingest_trade_universe_csv(
        csv_paths=[p], dataset_id="ds_fields",
        datasets_root=tmp_path / "datasets",
    )
    assert len(m.parquet_sha256) == 64
    assert len(m.schema_fingerprint) == 64
    assert m.source_paths == [p]
    assert m.date_window == (date(2024, 2, 1), date(2024, 3, 5))


def test_manifest_roundtrip_on_disk(tmp_path):
    p = tmp_path / "trades.csv"
    _seed_csv(p)
    m = ingest_trade_universe_csv(
        csv_paths=[p], dataset_id="ds_rt",
        datasets_root=tmp_path / "datasets",
    )
    written = (tmp_path / "datasets" / "trade_universe" / "ds_rt" / "manifest.json").read_text()
    back = DatasetManifest.model_validate_json(written)
    assert back == m


def test_missing_source_csv_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="trade universe source CSV"):
        ingest_trade_universe_csv(
            csv_paths=[tmp_path / "nope.csv"],
            dataset_id="ds_nope",
            datasets_root=tmp_path / "datasets",
        )


def test_empty_csv_paths_raises(tmp_path):
    with pytest.raises(ValueError, match="non-empty"):
        ingest_trade_universe_csv(
            csv_paths=[], dataset_id="ds_empty",
            datasets_root=tmp_path / "datasets",
        )


def test_upstream_datasets_propagate(tmp_path):
    p = tmp_path / "trades.csv"
    _seed_csv(p)
    m = ingest_trade_universe_csv(
        csv_paths=[p], dataset_id="ds_upstream",
        datasets_root=tmp_path / "datasets",
        upstream_datasets=["features_v3_2024"],
    )
    assert m.upstream_datasets == ["features_v3_2024"]


def test_parquet_sha256_stable_across_identical_csvs(tmp_path):
    p = tmp_path / "trades.csv"
    _seed_csv(p)
    m1 = ingest_trade_universe_csv(
        csv_paths=[p], dataset_id="ds_stable_1",
        datasets_root=tmp_path / "datasets",
    )
    m2 = ingest_trade_universe_csv(
        csv_paths=[p], dataset_id="ds_stable_2",
        datasets_root=tmp_path / "datasets",
    )
    # NOTE: parquet write may not be byte-identical across runs due to
    # pyarrow metadata timestamps. If this assertion fails, document the
    # finding in your report and change the test to assert the hash is
    # reproducible *within* a run (same manifest written at same time).
    # But it SHOULD be stable because the same pandas DataFrame serialized
    # via to_parquet with default kwargs produces deterministic output in
    # recent pyarrow versions. Verify empirically.
    assert m1.parquet_sha256 == m2.parquet_sha256


def test_schema_fingerprint_stable_across_value_changes(tmp_path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    _seed_csv(a, entry_dates=("2024-02-01",))
    _seed_csv(b, entry_dates=("2024-05-15",))
    m1 = ingest_trade_universe_csv(
        csv_paths=[a], dataset_id="ds_schema_a",
        datasets_root=tmp_path / "datasets",
    )
    m2 = ingest_trade_universe_csv(
        csv_paths=[b], dataset_id="ds_schema_b",
        datasets_root=tmp_path / "datasets",
    )
    assert m1.schema_fingerprint == m2.schema_fingerprint
