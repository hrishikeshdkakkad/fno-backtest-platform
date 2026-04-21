"""Shared hashing helpers for dataset manifests (master design §4.5)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's raw bytes. Streamed in 1 MiB chunks."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def schema_fingerprint(df: pd.DataFrame) -> str:
    """Stable hex SHA-256 of (column_name, dtype_str) pairs, sorted by column name.

    Value-stable: same schema -> same fingerprint regardless of row values.
    Order-stable: column ordering in the frame doesn't matter.
    """
    pairs = sorted((str(col), str(df.dtypes[col])) for col in df.columns)
    blob = json.dumps(pairs, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
