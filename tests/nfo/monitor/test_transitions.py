"""Tests for monitor.transitions pure state machine (master design §10.3)."""
from __future__ import annotations

from nfo.monitor.transitions import Evidence, next_state


def _ev(**kw) -> Evidence:
    base = dict(trigger_passed=False, is_entered=False, is_expired=False, is_invalidated=False)
    base.update(kw)
    return Evidence(**base)


def test_idle_to_watch_on_cycle_start():
    new, reasons = next_state("idle", _ev())
    assert new == "watch"
    assert len(reasons) >= 1


def test_watch_to_fire_on_trigger():
    new, reasons = next_state("watch", _ev(trigger_passed=True))
    assert new == "fire"
    assert any("trigger" in r.lower() for r in reasons)


def test_fire_to_entered_on_entry():
    new, reasons = next_state("fire", _ev(trigger_passed=True, is_entered=True))
    assert new == "entered"


def test_fire_to_invalidated_when_trigger_drops():
    new, reasons = next_state("fire", _ev(trigger_passed=False, is_invalidated=True))
    assert new == "invalidated"


def test_fire_stays_fire_when_trigger_still_passes_and_no_entry():
    new, _ = next_state("fire", _ev(trigger_passed=True))
    assert new == "fire"


def test_entered_to_expired_at_expiry():
    new, reasons = next_state("entered", _ev(is_expired=True))
    assert new == "expired"


def test_watch_to_expired_without_trigger():
    new, reasons = next_state("watch", _ev(is_expired=True))
    assert new == "expired"


def test_fire_to_expired_without_entry():
    new, reasons = next_state("fire", _ev(is_expired=True))
    assert new == "expired"


def test_reason_codes_always_populated():
    for start in ("idle", "watch", "fire", "entered", "invalidated", "expired"):
        _, reasons = next_state(start, _ev())
        assert isinstance(reasons, list)
        assert len(reasons) >= 1


def test_expired_is_terminal():
    new, reasons = next_state("expired", _ev(trigger_passed=True))
    assert new == "expired"


def test_invalidated_is_terminal():
    new, reasons = next_state("invalidated", _ev(trigger_passed=True))
    assert new == "invalidated"
