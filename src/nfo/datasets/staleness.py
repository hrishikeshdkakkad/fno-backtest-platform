"""Run staleness detection (master design §7.2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from nfo.specs.manifest import RunManifest


@dataclass
class HashSources:
    strategy_hash_fn: Callable[[str, str], str | None]
    dataset_hash_fn: Callable[[str], str | None]


def is_run_stale(manifest: RunManifest, sources: HashSources) -> list[str]:
    reasons: list[str] = []
    cur_strategy = sources.strategy_hash_fn(manifest.strategy_id, manifest.strategy_version)
    if cur_strategy is None:
        reasons.append(f"strategy_missing:{manifest.strategy_id}@{manifest.strategy_version}")
    elif cur_strategy != manifest.strategy_spec_hash:
        reasons.append(
            f"strategy_spec_hash_changed:{manifest.strategy_id}@{manifest.strategy_version}"
        )
    for dsid, expected in manifest.dataset_hashes.items():
        cur = sources.dataset_hash_fn(dsid)
        if cur is None:
            reasons.append(f"dataset_missing:{dsid}")
        elif cur != expected:
            reasons.append(f"dataset_hash_changed:{dsid}")
    return reasons
