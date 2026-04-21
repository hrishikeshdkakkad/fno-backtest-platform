"""Per-underlying static constants.

Security IDs come from Dhan's index-segment master (IDX_I). Lot sizes and strike
steps are NSE-published. margin_multiplier is the rough SPAN+exposure proxy used
to scale `max_loss × lot` into buying-power estimates; calibrate against the
broker's margin calculator when the first grid run lands.

Lot-size review schedule: SEBI mandates semi-annual reviews (June + December).
Last revision was 2025-12-30 (NIFTY 75→65). Next review: June 2026 — verify
against current NSE circulars before sizing a production run (see
docs/india-fno-nuances.md §2).
"""
from __future__ import annotations

from dataclasses import dataclass


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
