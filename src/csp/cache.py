"""Parquet-backed cache for Massive API results.

We cache by (kind, key) where kind is one of
    stock_bars/<TICKER>.parquet
    option_bars/<O_TICKER>.parquet
    chain/<UNDERLYING>_<EXPIRY>.parquet

Reads return a pandas DataFrame; writes upsert by key column.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import DATA_DIR


def _path(kind: str, key: str) -> Path:
    safe = key.replace("/", "_").replace(":", "_")
    p = DATA_DIR / kind / f"{safe}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load(kind: str, key: str) -> pd.DataFrame | None:
    p = _path(kind, key)
    if not p.exists():
        return None
    return pd.read_parquet(p)


def save(kind: str, key: str, df: pd.DataFrame) -> None:
    p = _path(kind, key)
    df.to_parquet(p, index=False)


def upsert_bars(kind: str, key: str, new_df: pd.DataFrame, ts_col: str = "t") -> pd.DataFrame:
    """Merge `new_df` into any existing cached DataFrame, de-duplicating on ts."""
    existing = load(kind, key)
    if existing is None or existing.empty:
        combined = new_df.copy()
    else:
        combined = pd.concat([existing, new_df], ignore_index=True)
    if ts_col in combined.columns:
        combined = combined.drop_duplicates(subset=[ts_col]).sort_values(ts_col).reset_index(drop=True)
    save(kind, key, combined)
    return combined
