"""Tests for datasets.features.ingest_features_parquet + hashing helpers (master design §7.1, §4.5)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from nfo.datasets._hashing import schema_fingerprint, sha256_file
from nfo.datasets.features import features_dataset_dir, ingest_features_parquet
from nfo.specs.manifest import DatasetManifest


# ---------------------------------------------------------------------------
# Hashing helper tests
# ---------------------------------------------------------------------------


def test_sha256_file_stable(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello")
    a = sha256_file(p)
    b = sha256_file(p)
    assert a == b
    assert len(a) == 64


def test_sha256_file_changes_on_content(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"a")
    h1 = sha256_file(p)
    p.write_bytes(b"b")
    h2 = sha256_file(p)
    assert h1 != h2


def test_schema_fingerprint_ignores_values():
    df1 = pd.DataFrame({"a": [1, 2, 3], "b": [4.0, 5.0, 6.0]})
    df2 = pd.DataFrame({"a": [99, 100], "b": [7.5, 8.5]})
    assert schema_fingerprint(df1) == schema_fingerprint(df2)


def test_schema_fingerprint_changes_on_schema():
    df1 = pd.DataFrame({"a": [1]})
    df2 = pd.DataFrame({"a": [1], "b": [2.0]})
    assert schema_fingerprint(df1) != schema_fingerprint(df2)


def test_schema_fingerprint_independent_of_column_order():
    df1 = pd.DataFrame({"a": [1], "b": [2.0]})
    df2 = pd.DataFrame({"b": [2.0], "a": [1]})
    assert schema_fingerprint(df1) == schema_fingerprint(df2)


# ---------------------------------------------------------------------------
# ingest_features_parquet tests
# ---------------------------------------------------------------------------


def _seed_parquet(tmp_path, dates=("2025-01-01", "2025-01-02", "2025-01-03")) -> Path:
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({
        "date": pd.to_datetime(list(dates)),
        "vix": [15.0, 16.0, 17.0],
        "trend_score": [2, 3, 1],
    })
    p = tmp_path / "seed.parquet"
    df.to_parquet(p)
    return p


def test_ingest_creates_manifest_and_parquet(tmp_path):
    parq = _seed_parquet(tmp_path)
    datasets_root = tmp_path / "datasets"
    m = ingest_features_parquet(
        parquet_path=parq, dataset_id="ds_x",
        datasets_root=datasets_root,
    )
    dest_dir = features_dataset_dir(datasets_root, "ds_x")
    assert (dest_dir / "dataset.parquet").exists()
    assert (dest_dir / "manifest.json").exists()
    assert m.dataset_id == "ds_x"
    assert m.dataset_type == "features"


def test_manifest_fields_populated(tmp_path):
    parq = _seed_parquet(tmp_path)
    m = ingest_features_parquet(
        parquet_path=parq, dataset_id="ds_y",
        datasets_root=tmp_path / "datasets",
    )
    assert m.row_count == 3
    assert m.date_window == (date(2025, 1, 1), date(2025, 1, 3))
    assert len(m.parquet_sha256) == 64
    assert len(m.schema_fingerprint) == 64
    assert m.source_paths == [parq]
    assert m.upstream_datasets == []


def test_manifest_roundtrip_on_disk(tmp_path):
    parq = _seed_parquet(tmp_path)
    m = ingest_features_parquet(
        parquet_path=parq, dataset_id="ds_r",
        datasets_root=tmp_path / "datasets",
    )
    written = (tmp_path / "datasets" / "features" / "ds_r" / "manifest.json").read_text()
    back = DatasetManifest.model_validate_json(written)
    assert back == m


def test_parquet_sha256_stable(tmp_path):
    parq = _seed_parquet(tmp_path)
    m1 = ingest_features_parquet(
        parquet_path=parq, dataset_id="ds_a",
        datasets_root=tmp_path / "datasets_a",
    )
    m2 = ingest_features_parquet(
        parquet_path=parq, dataset_id="ds_b",
        datasets_root=tmp_path / "datasets_b",
    )
    assert m1.parquet_sha256 == m2.parquet_sha256


def test_parquet_sha256_changes_on_content(tmp_path):
    parq1 = _seed_parquet(tmp_path / "p1")
    parq2 = _seed_parquet(tmp_path / "p2", dates=("2025-02-01", "2025-02-02", "2025-02-03"))
    m1 = ingest_features_parquet(
        parquet_path=parq1, dataset_id="ds_c",
        datasets_root=tmp_path / "datasets",
    )
    m2 = ingest_features_parquet(
        parquet_path=parq2, dataset_id="ds_d",
        datasets_root=tmp_path / "datasets",
    )
    assert m1.parquet_sha256 != m2.parquet_sha256


def test_missing_parquet_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="features source parquet"):
        ingest_features_parquet(
            parquet_path=tmp_path / "nope.parquet", dataset_id="ds_e",
            datasets_root=tmp_path / "datasets",
        )


def test_upstream_datasets_propagate(tmp_path):
    parq = _seed_parquet(tmp_path)
    m = ingest_features_parquet(
        parquet_path=parq, dataset_id="ds_u",
        datasets_root=tmp_path / "datasets",
        upstream_datasets=["raw_nifty_2024", "raw_vix_2024"],
    )
    assert m.upstream_datasets == ["raw_nifty_2024", "raw_vix_2024"]
