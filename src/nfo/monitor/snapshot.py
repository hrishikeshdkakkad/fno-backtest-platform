"""MonitorSnapshot model + snapshot_id builder (master design §9.1).

The producer function `capture_snapshot` lands in Bundle B; this module
ships only the schema + id helper so parity / transitions tests can depend
on the model before the producer exists.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Underlying = Literal["NIFTY", "BANKNIFTY", "FINNIFTY"]
MonitorState = Literal["idle", "watch", "fire", "entered", "invalidated", "expired"]


class MonitorSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    timestamp: datetime
    strategy_spec_id: str
    strategy_version: str
    strategy_spec_hash: str
    underlying: Underlying
    cycle_id: str
    target_expiry: date
    current_state: MonitorState
    first_fire_date: date | None = None
    current_grade: str
    trigger_passed: bool
    trigger_details: dict[str, Any] = Field(default_factory=dict)
    selection_preview: dict[str, Any] | None = None
    proposed_trade: dict[str, Any] | None = None
    reason_codes: list[str] = Field(default_factory=list)


def build_snapshot_id(
    *,
    strategy_id: str,
    strategy_version: str,
    underlying: str,
    timestamp: datetime,
) -> str:
    payload = {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "underlying": underlying,
        "timestamp": timestamp.isoformat(),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:16]
