"""Per-underlying static constants.

Security IDs come from Dhan's index-segment master (IDX_I). Lot sizes and strike
steps are NSE-published. margin_multiplier is the rough SPAN+exposure proxy used
to scale `max_loss × lot` into buying-power estimates; calibrate against the
broker's margin calculator when the first grid run lands.

Lot-size review schedule: SEBI mandates semi-annual reviews (June + December).
Last revision was 2025-12-30 (NIFTY 75→65). Next review: June 2026 — verify
against current NSE circulars before sizing a production run (see
docs/india-fno-nuances.md §2).

``lot_size_on(name, as_of)`` returns the lot size effective on that date. The
scalar ``Underlying.lot_size`` is kept in sync with the current-day lookup for
live/production code paths; any historical backtest MUST use ``lot_size_on``
with the trade's entry date — using the scalar silently mis-scales P&L for
trades before 2024-11-20.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Underlying:
    name: str
    security_id: int
    underlying_seg: str      # Dhan enum for the index segment (feeds optionchain endpoints)
    exchange_segment: str    # Dhan enum for the F&O segment (feeds historical/rollingoption)
    instrument: str          # "OPTIDX" for index options, "OPTSTK" for stock options
    lot_size: int
    strike_step: int
    margin_multiplier: float


REGISTRY: dict[str, Underlying] = {
    "NIFTY": Underlying(
        name="NIFTY",
        security_id=13,
        underlying_seg="IDX_I",
        exchange_segment="NSE_FNO",
        instrument="OPTIDX",
        lot_size=65,
        strike_step=50,
        margin_multiplier=1.5,
    ),
    "BANKNIFTY": Underlying(
        name="BANKNIFTY",
        security_id=25,
        underlying_seg="IDX_I",
        exchange_segment="NSE_FNO",
        instrument="OPTIDX",
        lot_size=35,
        strike_step=100,
        margin_multiplier=1.5,
    ),
}


def get(name: str) -> Underlying:
    try:
        return REGISTRY[name.upper()]
    except KeyError as exc:
        raise KeyError(f"Unknown underlying {name!r}; known: {sorted(REGISTRY)}") from exc


# NSE-published lot-size revisions. Each entry is (effective_from, lot_size)
# in ascending date order — lot_size_on performs a linear scan from newest to
# oldest. Sources: NSE Circulars FAOP64625 (Nov 2024), FAOP70616 (Oct 2025);
# cross-referenced in docs/india-fno-nuances.md §2.
LOT_SIZE_HISTORY: dict[str, list[tuple[date, int]]] = {
    "NIFTY": [
        (date(1900, 1, 1), 25),       # Pre-2024-11-20 regime
        (date(2024, 11, 20), 75),     # SEBI Oct-2024 framework
        (date(2025, 12, 30), 65),     # Dec-2025 semi-annual review
    ],
    "BANKNIFTY": [
        (date(1900, 1, 1), 15),
        (date(2024, 11, 20), 30),
        # No change on 2025-12-30.
    ],
}


def lot_size_on(name: str, as_of: date) -> int:
    """Return the NSE-published lot size effective for ``name`` on ``as_of``.

    The scalar ``Underlying.lot_size`` reflects the *current* lot size only.
    Historical backtests must use this function to avoid silently mis-scaling
    P&L for trades entered before the most recent revision.
    """
    key = name.upper()
    try:
        history = LOT_SIZE_HISTORY[key]
    except KeyError as exc:
        raise KeyError(
            f"Unknown underlying {name!r}; known: {sorted(LOT_SIZE_HISTORY)}"
        ) from exc
    for effective_from, size in reversed(history):
        if as_of >= effective_from:
            return size
    return history[0][1]
