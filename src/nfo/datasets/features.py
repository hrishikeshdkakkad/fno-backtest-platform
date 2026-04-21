"""Features dataset ingestion (master design §7.1 stage 3).

Formalizes an existing features parquet (e.g. results/nfo/historical_signals.parquet)
as a manifested dataset under data/nfo/datasets/features/<dataset_id>/.
"""
from __future__ import annotations

import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from nfo.datasets._hashing import schema_fingerprint, sha256_file
from nfo.specs.manifest import DatasetManifest


def features_dataset_dir(datasets_root: Path, dataset_id: str) -> Path:
    return Path(datasets_root) / "features" / dataset_id


def ingest_features_parquet(
    *,
    parquet_path: Path,
    dataset_id: str,
    datasets_root: Path,
    upstream_datasets: list[str] | None = None,
    code_version: str | None = None,
) -> DatasetManifest:
    """Copy `parquet_path` into the features-stage tree and emit manifest.json.

    Returns the DatasetManifest (also written alongside).
    """
    parquet_path = Path(parquet_path)
    if not parquet_path.exists():
        raise FileNotFoundError(f"features source parquet missing: {parquet_path}")

    dest_dir = features_dataset_dir(datasets_root, dataset_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_parquet = dest_dir / "dataset.parquet"
    shutil.copy2(parquet_path, dest_parquet)

    df = pd.read_parquet(dest_parquet)
    row_count = int(len(df))
    date_window: tuple[date, date] | None = None
    if "date" in df.columns and row_count:
        dates = pd.to_datetime(df["date"])
        date_window = (dates.min().date(), dates.max().date())

    manifest = DatasetManifest(
        dataset_id=dataset_id,
        dataset_type="features",
        source_paths=[parquet_path],
        date_window=date_window,
        row_count=row_count,
        build_time=datetime.now(timezone.utc),
        code_version=code_version or "unversioned",
        upstream_datasets=upstream_datasets or [],
        parquet_sha256=sha256_file(dest_parquet),
        schema_fingerprint=schema_fingerprint(df),
    )
    (dest_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2))
    return manifest
