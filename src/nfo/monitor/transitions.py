"""Monitor state machine (master design §9.2, §10.3).

Pure function: next_state(current, evidence) -> (new_state, reason_codes).
No I/O, no side effects. The state machine is deterministic given (current, evidence).

Transitions:
  idle      -> watch     : cycle begins
  watch     -> fire      : trigger_passed=True
  watch     -> expired   : is_expired=True
  fire      -> entered   : is_entered=True
  fire      -> invalidated : is_invalidated=True OR trigger_passed=False (drop-off)
  fire      -> expired   : is_expired=True
  entered   -> expired   : is_expired=True
  expired, invalidated   : terminal (self-loop)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

State = Literal["idle", "watch", "fire", "entered", "invalidated", "expired"]


@dataclass
class Evidence:
    trigger_passed: bool = False
    is_entered: bool = False
    is_expired: bool = False
    is_invalidated: bool = False


def next_state(current: State, evidence: Evidence) -> tuple[State, list[str]]:
    # Terminal states
    if current == "expired":
        return "expired", ["terminal:expired"]
    if current == "invalidated":
        return "invalidated", ["terminal:invalidated"]

    # Expiry supersedes other transitions (except terminal)
    if evidence.is_expired and current in ("watch", "fire", "entered"):
        return "expired", [f"expired_from_{current}"]

    if current == "idle":
        return "watch", ["cycle_started"]

    if current == "watch":
        if evidence.trigger_passed:
            return "fire", ["trigger_passed"]
        return "watch", ["watch_only"]

    if current == "fire":
        if evidence.is_entered:
            return "entered", ["trade_placed"]
        if evidence.is_invalidated or not evidence.trigger_passed:
            return "invalidated", ["trigger_dropped_off"]
        return "fire", ["trigger_still_passing"]

    if current == "entered":
        return "entered", ["awaiting_expiry"]

    raise ValueError(f"unknown state: {current!r}")
