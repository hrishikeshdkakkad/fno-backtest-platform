"""Filesystem-backed HashSources factory for the index generator."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from nfo.datasets.staleness import HashSources
from nfo.specs.hashing import spec_hash
from nfo.specs.strategy import StrategySpec


def filesystem_hash_sources(*, strategies_root: Path, datasets_root: Path) -> HashSources:
    def strategy_hash(strategy_id: str, strategy_version: str) -> str | None:
        if not strategies_root.exists():
            return None
        for yml in strategies_root.glob("*.yaml"):
            try:
                raw = yaml.safe_load(yml.read_text())
            except Exception:
                continue
            if raw.get("strategy_id") == strategy_id and raw.get("strategy_version") == strategy_version:
                return spec_hash(StrategySpec.model_validate(raw))
        return None

    def dataset_hash(dataset_id: str) -> str | None:
        if not datasets_root.exists():
            return None
        for manifest_path in datasets_root.rglob("manifest.json"):
            try:
                raw = json.loads(manifest_path.read_text())
            except Exception:
                continue
            if raw.get("dataset_id") == dataset_id:
                return raw.get("parquet_sha256")
        return None

    return HashSources(strategy_hash_fn=strategy_hash, dataset_hash_fn=dataset_hash)
