"""Canonical identifier helpers (master design §5).

Only id-construction is exposed in P1. Full cycle-grouping and trigger
evaluation lands in P2 under this same module.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone


def feature_day_id(underlying: str, on_date: date) -> str:
    return f"{underlying}:{on_date.isoformat()}"


def cycle_id(underlying: str, target_expiry: date, strategy_version: str) -> str:
    return f"{underlying}:{target_expiry.isoformat()}:{strategy_version}"


def fire_id(cycle_id_: str, fire_date: date) -> str:
    return f"{cycle_id_}:{fire_date.isoformat()}"


def selection_id(cycle_id_: str, selection_mode: str, exit_variant: str) -> str:
    return f"{cycle_id_}:{selection_mode}:{exit_variant}"


def trade_id(
    *,
    underlying: str,
    expiry_date: date,
    short_strike: float,
    long_strike: float,
    width: float,
    delta_target: float,
    exit_variant: str,
    entry_date: date,
) -> str:
    payload = {
        "underlying": underlying,
        "expiry_date": expiry_date.isoformat(),
        "short_strike": float(short_strike),
        "long_strike": float(long_strike),
        "width": float(width),
        "delta_target": float(delta_target),
        "exit_variant": exit_variant,
        "entry_date": entry_date.isoformat(),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:16]


def build_run_id(*, created_at: datetime, study_id: str, strategy_hash_short: str) -> str:
    if created_at.tzinfo is not None:
        created_at = created_at.astimezone(tz=timezone.utc)
    ts = created_at.strftime("%Y%m%dT%H%M%S")
    return f"{ts}-{study_id}-{strategy_hash_short}"
