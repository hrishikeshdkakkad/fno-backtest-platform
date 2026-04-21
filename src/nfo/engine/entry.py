"""Engine: entry date resolution (master design §6.3, §12 acceptance item 3).

The ONLY place in the codebase that decides entry dates for live_rule mode.
Any selection code path constructing an entry date directly without calling
resolve_entry_date for a live_rule spec is a bug.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable


def resolve_entry_date(
    *,
    spec,
    first_fire_date: date,
    sessions: Iterable[date],
    canonical_entry_date: date | None = None,
) -> date | None:
    """Return the entry date for a cycle given its first fire and session list.

    Parameters
    ----------
    spec :
        A StrategySpec (duck-typed: must expose `selection_rule.mode` and
        `entry_rule.allow_pre_fire_entry`).
    first_fire_date :
        The earliest firing date for the cycle.
    sessions :
        Iterable of available trading session dates, ascending. Used by live_rule
        and cycle_matched (no-pre-fire) to snap forward.
    canonical_entry_date :
        Only consulted for cycle_matched with allow_pre_fire_entry=True.

    Returns
    -------
    The resolved entry date, or None if live_rule/cycle_matched cannot find a
    session on or after first_fire_date.
    """
    mode = spec.selection_rule.mode
    if mode == "day_matched":
        return first_fire_date

    if mode == "cycle_matched":
        if spec.entry_rule.allow_pre_fire_entry and canonical_entry_date is not None:
            return canonical_entry_date
        return _snap_forward(first_fire_date, sessions)

    if mode == "live_rule":
        if spec.entry_rule.allow_pre_fire_entry:
            raise ValueError(
                "selection mode 'live_rule' forbids entry_rule.allow_pre_fire_entry=True; "
                "StrategySpec validator should have caught this - defense in depth."
            )
        return _snap_forward(first_fire_date, sessions)

    raise ValueError(f"unknown selection mode: {mode!r}")


def _snap_forward(target: date, sessions: Iterable[date]) -> date | None:
    for s in sessions:
        if s >= target:
            return s
    return None
