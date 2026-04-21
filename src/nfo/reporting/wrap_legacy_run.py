"""wrap_legacy_run — lets P1 scripts emit a run directory without changing their
business logic (master design §10.1).

Usage (from a wrapper script):
  result = wrap_legacy_run(
      study_type="capital_analysis",
      strategy_path=REPO/"configs/nfo/strategies/v3_frozen.yaml",
      study_path=REPO/"configs/nfo/studies/capital_analysis_10l.yaml",
      legacy_artifacts=[RESULTS/"v3_capital_trades_hte.csv", ...],
      window=(window_start, window_end),
      run_logic=_run,   # returns {"metrics": {...}, "body_markdown": "...", "warnings": [...]}
      runs_root=RESULTS/"runs",
  )
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from nfo.engine.cycles import build_run_id
from nfo.reporting.artifacts import RunDirectory, open_run_directory
from nfo.reporting.git_version import current_code_version
from nfo.specs.hashing import short_hash
from nfo.specs.loader import load_strategy, load_study
from nfo.specs.manifest import RunManifest
from nfo.specs.study import DatasetRef, StudyType


@dataclass
class WrappedRun:
    run_dir: RunDirectory
    manifest: RunManifest


def _resolve_dataset_hashes(refs: list[DatasetRef]) -> dict[str, str]:
    """For each ref, read <ref.path>/manifest.json and extract parquet_sha256.

    Missing manifests are logged and skipped (returning no entry for that ref) —
    so an incomplete dataset setup doesn't crash the run, but the resulting
    manifest will have an empty hash for that dataset_id which is_run_stale
    will later flag as `dataset_missing`.
    """
    import json
    import logging

    log = logging.getLogger("wrap_legacy_run")
    out: dict[str, str] = {}
    for ref in refs:
        mpath = Path(ref.path) / "manifest.json"
        if not mpath.exists():
            log.warning("dataset manifest missing for %s at %s", ref.dataset_id, mpath)
            continue
        try:
            raw = json.loads(mpath.read_text())
        except Exception as exc:
            log.warning("failed to parse dataset manifest %s: %s", mpath, exc)
            continue
        h = raw.get("parquet_sha256")
        if h:
            out[ref.dataset_id] = h
    return out


def wrap_legacy_run(
    *,
    study_type: StudyType,
    strategy_path: Path,
    study_path: Path | None,
    legacy_artifacts: list[Path],
    window: tuple[date, date],
    run_logic: Callable[[], dict[str, Any]],
    runs_root: Path,
    code_version: str | None = None,
    dataset_refs: list[DatasetRef] | None = None,
) -> WrappedRun:
    strategy, strategy_hash_hex = load_strategy(strategy_path)
    study_hash_hex = ""
    if study_path is not None:
        _, study_hash_hex = load_study(study_path)

    created_at = datetime.now(timezone.utc)
    run_id = build_run_id(
        created_at=created_at,
        study_id=study_path.stem if study_path else study_type,
        strategy_hash_short=short_hash(strategy),
    )
    runs_root.mkdir(parents=True, exist_ok=True)
    rd = open_run_directory(root=runs_root, run_id=run_id)

    t0 = time.perf_counter()
    result = run_logic() or {}
    dt = time.perf_counter() - t0

    warnings = list(result.get("warnings", []) or [])
    status = "warnings" if warnings else "ok"

    dataset_hashes = _resolve_dataset_hashes(dataset_refs or [])

    manifest = RunManifest(
        run_id=run_id,
        created_at=created_at,
        code_version=code_version or current_code_version(repo_root=Path.cwd()),
        study_spec_hash=study_hash_hex or "",
        strategy_spec_hash=strategy_hash_hex,
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.strategy_version,
        study_type=study_type,
        selection_mode=strategy.selection_rule.mode,
        dataset_hashes=dataset_hashes,
        window_start=window[0],
        window_end=window[1],
        artifacts=[],
        status=status,
        warnings=warnings,
        stale_inputs_detected=[],
        duration_seconds=dt,
    )
    rd.write_manifest(manifest)

    metrics = dict(result.get("metrics") or {})
    rd.write_metrics(metrics)

    tables_dir = rd.path / "tables"
    for src in legacy_artifacts:
        if not src.exists():
            continue
        if src.suffix in {".csv", ".parquet"}:
            dst = tables_dir / src.name
            shutil.copy2(src, dst)
            rd._record_artifact(f"tables/{src.name}")

    body = result.get("body_markdown") or ""
    rd.write_report(body_markdown=body)

    return WrappedRun(run_dir=rd, manifest=manifest)
