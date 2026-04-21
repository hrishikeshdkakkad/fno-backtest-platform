"""YAML loader for strategy/study specs with drift detection (master design §4.2)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from nfo.specs.hashing import spec_hash
from nfo.specs.strategy import StrategySpec
from nfo.specs.study import StudySpec


class StrategyDriftError(Exception):
    """Raised when strategy_version did not bump despite content changes."""


_REGISTRY_PATH: Path = Path("configs/nfo/.registry.json")


def reset_registry_for_tests(path: Path) -> None:
    """Test helper: point the loader at a fresh registry file."""
    global _REGISTRY_PATH
    _REGISTRY_PATH = path
    _REGISTRY_PATH.write_text(json.dumps({"strategies": {}}))


def _read_registry() -> dict:
    if not _REGISTRY_PATH.exists():
        return {"strategies": {}}
    return json.loads(_REGISTRY_PATH.read_text())


def _write_registry(reg: dict) -> None:
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(json.dumps(reg, indent=2, sort_keys=True))


def load_strategy(path: Path) -> tuple[StrategySpec, str]:
    raw = yaml.safe_load(Path(path).read_text())
    spec = StrategySpec.model_validate(raw)
    current_hash = spec_hash(spec)

    reg = _read_registry()
    key = f"{spec.strategy_id}@{spec.strategy_version}"
    entry = reg["strategies"].get(key)
    if entry is not None and entry["hash"] != current_hash:
        raise StrategyDriftError(
            f"strategy_id={spec.strategy_id!r} version={spec.strategy_version!r} "
            f"content hash changed ({entry['hash'][:12]} -> {current_hash[:12]}). "
            f"Bump strategy_version before editing spec content."
        )
    reg["strategies"][key] = {
        "hash": current_hash,
        "path": str(path),
        "loaded_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_registry(reg)
    return spec, current_hash


def load_study(path: Path) -> tuple[StudySpec, str]:
    raw = yaml.safe_load(Path(path).read_text())
    spec = StudySpec.model_validate(raw)
    return spec, spec_hash(spec)
