"""Master summary generator (master design §9.3 family 'master_summary').

Aggregates headline metrics across all runs; emits one markdown section per
study_type with the latest run's manifest + metrics.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from nfo.specs.manifest import RunManifest


@dataclass
class MasterSummaryResult:
    total_runs: int
    latest_per_study: dict[str, str] = field(default_factory=dict)
    out_path: Path | None = None


def _load_manifests(runs_root: Path) -> list[RunManifest]:
    out: list[RunManifest] = []
    if not runs_root.exists():
        return out
    for child in sorted(runs_root.iterdir()):
        mpath = child / "manifest.json"
        if not mpath.exists():
            continue
        try:
            out.append(RunManifest.model_validate_json(mpath.read_text()))
        except Exception:
            continue
    return out


def _load_metrics(runs_root: Path, run_id: str) -> dict | None:
    mpath = runs_root / run_id / "metrics.json"
    if not mpath.exists():
        return None
    try:
        return json.loads(mpath.read_text())
    except Exception:
        return None


def generate_master_summary(
    *,
    runs_root: Path,
    out_path: Path,
) -> MasterSummaryResult:
    manifests = _load_manifests(runs_root)
    latest: dict[str, RunManifest] = {}
    for m in manifests:
        cur = latest.get(m.study_type)
        if cur is None or m.created_at > cur.created_at:
            latest[m.study_type] = m

    lines: list[str] = [
        "# NFO Platform — Master Summary",
        "",
        f"Generated: {datetime.now().isoformat()}",
        f"Total runs: {len(manifests)}",
        f"Study types: {len(latest)}",
        "",
    ]
    if not manifests:
        lines.append("_No runs yet._")
    for study in sorted(latest):
        m = latest[study]
        metrics = _load_metrics(runs_root, m.run_id)
        lines.append(f"## {study}")
        lines.append("")
        lines.append(f"- **Latest run:** `{m.run_id}`")
        lines.append(f"- **Strategy:** `{m.strategy_id}` version `{m.strategy_version}`")
        lines.append(f"- **Selection mode:** {m.selection_mode}")
        lines.append(f"- **Window:** {m.window_start.isoformat()} → {m.window_end.isoformat()}")
        lines.append(f"- **Created:** {m.created_at.isoformat()}")
        lines.append(f"- **Status:** {m.status}")
        lines.append(f"- **Path:** `{(runs_root / m.run_id).as_posix()}`")
        if metrics:
            lines.append("- **Headline metrics:**")
            for k, v in sorted(metrics.items()):
                lines.append(f"  - `{k}`: {v}")
        else:
            lines.append("- _(no metrics.json in this run)_")
        lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")

    return MasterSummaryResult(
        total_runs=len(manifests),
        latest_per_study={k: v.run_id for k, v in latest.items()},
        out_path=out_path,
    )
