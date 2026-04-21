"""Parquet cache for Dhan responses, keyed by `(kind, key)`.

Mirrors the semantics of `src/csp/cache.py`. Kinds in use:
    rolling/<UND>_<EXPIRY>_<OFFSET>_<OPT>.parquet
    chain/<UND>_<EXPIRY>.parquet            (live chain snapshots; v2)
    hist/<SECURITYID>.parquet               (fixed-contract bars; v2)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import DATA_DIR


def _path(kind: str, key: str) -> Path:
    safe = key.replace("/", "_").replace(":", "_").replace("+", "p").replace(" ", "")
    p = DATA_DIR / kind / f"{safe}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load(kind: str, key: str) -> pd.DataFrame | None:
    p = _path(kind, key)
    if not p.exists():
        return None
    return pd.read_parquet(p)


def save(kind: str, key: str, df: pd.DataFrame) -> None:
    df.to_parquet(_path(kind, key), index=False)


def upsert(kind: str, key: str, new_df: pd.DataFrame, ts_col: str = "t") -> pd.DataFrame:
    existing = load(kind, key)
    if existing is None or existing.empty:
        combined = new_df.copy()
    else:
        combined = pd.concat([existing, new_df], ignore_index=True)
    if ts_col in combined.columns:
        combined = (
            combined.drop_duplicates(subset=[ts_col])
            .sort_values(ts_col)
            .reset_index(drop=True)
        )
    save(kind, key, combined)
    return combined
