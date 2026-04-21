"""Top-level run index generator (master design §8.2)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from nfo.datasets.staleness import HashSources, is_run_stale
from nfo.specs.manifest import RunManifest


@dataclass
class IndexResult:
    total_runs: int
    stale_runs: int
    by_study: dict[str, int]


def _load_manifests(runs_root: Path) -> list[RunManifest]:
    manifests: list[RunManifest] = []
    if not runs_root.exists():
        return manifests
    for child in sorted(runs_root.iterdir()):
        mpath = child / "manifest.json"
        if not mpath.exists():
            continue
        manifests.append(RunManifest.model_validate_json(mpath.read_text()))
    return manifests


def generate_index(
    *,
    runs_root: Path,
    out_root: Path,
    sources: HashSources,
) -> IndexResult:
    manifests = _load_manifests(runs_root)
    stale_map: dict[str, list[str]] = {m.run_id: is_run_stale(m, sources) for m in manifests}

    latest: dict[str, dict] = {}
    for m in manifests:
        cur = latest.get(m.study_type)
        if cur is None or m.created_at > cur["_created_at"]:
            latest[m.study_type] = {
                "run_id": m.run_id,
                "path": str(runs_root / m.run_id),
                "created_at": m.created_at.isoformat(),
                "_created_at": m.created_at,
            }
    latest_serializable = {
        k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
        for k, v in latest.items()
    }
    (out_root / "latest.json").write_text(json.dumps(latest_serializable, indent=2, sort_keys=True))

    lines: list[str] = ["# NFO Platform - Runs Index", ""]
    by_study: dict[str, list[RunManifest]] = {}
    for m in manifests:
        by_study.setdefault(m.study_type, []).append(m)

    stale_count = 0
    for study in sorted(by_study):
        lines.append(f"## {study}")
        lines.append("")
        lines.append("| Run ID | Created | Status | Stale? |")
        lines.append("|---|---|---|---|")
        for m in sorted(by_study[study], key=lambda x: x.created_at, reverse=True):
            reasons = stale_map.get(m.run_id, [])
            stale_mark = "no" if not reasons else f"YES - {'; '.join(reasons)}"
            if reasons:
                stale_count += 1
            lines.append(f"| `{m.run_id}` | {m.created_at.isoformat()} | {m.status} | {stale_mark} |")
        lines.append("")

    if not manifests:
        lines.append("_No runs yet._")
    (out_root / "index.md").write_text("\n".join(lines) + "\n")

    return IndexResult(
        total_runs=len(manifests),
        stale_runs=stale_count,
        by_study={k: len(v) for k, v in by_study.items()},
    )
