"""MonitorSnapshot model + snapshot_id builder (master design §9.1).

The producer function `capture_snapshot` composes engine.triggers +
canonical id helpers into a MonitorSnapshot.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Callable, Literal

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


# -- Producer -----------------------------------------------------------------


def capture_snapshot(
    *,
    spec: "StrategySpec",  # noqa: F821 -- forward ref to avoid circular import
    spec_hash: str,
    features_row: "pd.Series",  # noqa: F821
    atr_value: float = float("nan"),
    target_expiry: date,
    current_state: MonitorState,
    first_fire_date: date | None = None,
    current_grade: str = "",
    now: datetime | None = None,
    event_resolver: Callable | None = None,
) -> MonitorSnapshot:
    """Produce a MonitorSnapshot from a single features row + strategy spec.

    Uses engine.triggers.TriggerEvaluator to decide trigger_passed +
    trigger_details. Leaves selection_preview + proposed_trade as None
    (require live Dhan lookup, out of P4 scope).

    Args:
        spec: StrategySpec (loaded from YAML).
        spec_hash: content hash from load_strategy().
        features_row: a single row of the features DataFrame.
        atr_value: ATR-14 value at this timestamp (for trigger evaluation).
        target_expiry: the cycle's expiry.
        current_state: caller supplies (monitor tracks its own state externally).
        first_fire_date: the cycle's first fire date if known (None before fire).
        current_grade: optional grade label the caller wants to record.
        now: timestamp for this snapshot (defaults to datetime.now(UTC)).
        event_resolver: optional callable (date, dte) -> str -- pass-through
            to engine.triggers.
    """
    # Local imports avoid circular deps at module import time.
    from nfo.engine.cycles import cycle_id as _cycle_id
    from nfo.engine.triggers import TriggerEvaluator

    ts = now if now is not None else datetime.now(timezone.utc)
    underlying = spec.universe.underlyings[0]
    evaluator = TriggerEvaluator(spec, event_resolver=event_resolver)
    result = evaluator.evaluate_row(features_row, atr_value=atr_value)
    cid = _cycle_id(underlying, target_expiry, spec.strategy_version)
    sid = build_snapshot_id(
        strategy_id=spec.strategy_id,
        strategy_version=spec.strategy_version,
        underlying=underlying,
        timestamp=ts,
    )
    return MonitorSnapshot(
        snapshot_id=sid,
        timestamp=ts,
        strategy_spec_id=spec.strategy_id,
        strategy_version=spec.strategy_version,
        strategy_spec_hash=spec_hash,
        underlying=underlying,  # type: ignore[arg-type]
        cycle_id=cid,
        target_expiry=target_expiry,
        current_state=current_state,
        first_fire_date=first_fire_date,
        current_grade=current_grade,
        trigger_passed=result.fired,
        trigger_details=result.detail,
        selection_preview=None,
        proposed_trade=None,
        reason_codes=[],
    )
