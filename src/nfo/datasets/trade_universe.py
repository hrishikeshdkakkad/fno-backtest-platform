"""Trade universe dataset ingestion (master design §7.1 stage 4).

Reads candidate-trade CSVs (spread_trades.csv + gaps), concatenates in
order, writes single parquet + manifest.json under
data/nfo/datasets/trade_universe/<dataset_id>/.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from nfo.datasets._hashing import schema_fingerprint, sha256_file
from nfo.specs.manifest import DatasetManifest


def trade_universe_dataset_dir(datasets_root: Path, dataset_id: str) -> Path:
    return Path(datasets_root) / "trade_universe" / dataset_id


def ingest_trade_universe_csv(
    *,
    csv_paths: list[Path],
    dataset_id: str,
    datasets_root: Path,
    upstream_datasets: list[str] | None = None,
    code_version: str | None = None,
) -> DatasetManifest:
    """Concatenate CSVs -> single parquet + manifest.json.

    - csv_paths: ordered list of CSV paths (e.g. [spread_trades.csv, gaps.csv]).
      Missing paths raise FileNotFoundError.
    - Each CSV is read via pandas.read_csv, concatenated via pd.concat in the
      provided order (ignore_index=True).
    - Output parquet: dataset.parquet in the dataset dir.
    - date_window: derived from entry_date column if present.
    """
    csv_paths = [Path(p) for p in csv_paths]
    if not csv_paths:
        raise ValueError("csv_paths must be non-empty")
    frames = []
    for p in csv_paths:
        if not p.exists():
            raise FileNotFoundError(f"trade universe source CSV missing: {p}")
        frames.append(pd.read_csv(p))
    df = pd.concat(frames, ignore_index=True)

    dest_dir = trade_universe_dataset_dir(datasets_root, dataset_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_parquet = dest_dir / "dataset.parquet"
    df.to_parquet(dest_parquet, index=False)

    row_count = int(len(df))
    date_window: tuple[date, date] | None = None
    if "entry_date" in df.columns and row_count:
        dates = pd.to_datetime(df["entry_date"])
        date_window = (dates.min().date(), dates.max().date())

    manifest = DatasetManifest(
        dataset_id=dataset_id,
        dataset_type="trade_universe",
        source_paths=csv_paths,
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
