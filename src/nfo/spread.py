"""Bull put spread (put credit spread) construction + payoff.

Mirrors the semantics of `src/csp/spread.py` — per-share payoff is identical.
What differs from the US version:
  - long leg is looked up in the same chain snapshot (one API sweep covered both)
  - `spread_width` is in rupees/index-points (NIFTY 250 or 500, BANKNIFTY 500 or 1000)
  - lot size and margin multiplier live in `universe.Underlying`
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from .client import DhanClient
from .data import load_atm_chain_snapshot
from .strategy import ShortCandidate, StrategyConfig, pick_short_leg
from .universe import Underlying


@dataclass(slots=True)
class SpreadConfig(StrategyConfig):
    spread_width: float = 500.0


@dataclass(slots=True)
class SpreadCandidate:
    cfg: SpreadConfig
    entry_date: pd.Timestamp
    expiry_date: date
    spot_at_entry: float
    short_strike: float
    short_premium: float
    short_iv: float
    short_delta: float
    long_strike: float
    long_premium: float
    net_credit: float        # per share (short - long)
    max_loss: float          # per share (width - net_credit)


def pick_put_spread(
    client: DhanClient,
    cfg: SpreadConfig,
    under: Underlying,
    *,
    expiry_code: int,
    expiry_flag: str,
    expiry_date: date,
    entry_date: date,
) -> SpreadCandidate | None:
    """Pick a put credit spread for this cycle. Returns None on any skip condition."""
    short = pick_short_leg(
        client, cfg, under,
        expiry_code=expiry_code, expiry_flag=expiry_flag,
        expiry_date=expiry_date, entry_date=entry_date,
    )
    if short is None:
        return None

    target_long = short.strike - cfg.spread_width
    chain = load_atm_chain_snapshot(
        client, under,
        expiry_code=expiry_code, expiry_flag=expiry_flag,
        option_type="PUT", on_date=entry_date,
        offset_range=(-20, 2),   # wider range to catch the long leg
    )
    if chain.empty:
        return None
    long_row = chain[chain["strike"] == float(target_long)]
    if long_row.empty:
        return None
    long_premium = float(long_row["close"].iloc[0])
    if long_premium <= 0:
        return None
    net_credit = short.entry_premium - long_premium
    if net_credit <= 0:
        return None
    max_loss = cfg.spread_width - net_credit
    return SpreadCandidate(
        cfg=cfg,
        entry_date=short.entry_date,
        expiry_date=expiry_date,
        spot_at_entry=short.spot_at_entry,
        short_strike=short.strike,
        short_premium=short.entry_premium,
        short_iv=short.entry_iv,
        short_delta=short.entry_delta,
        long_strike=float(target_long),
        long_premium=long_premium,
        net_credit=net_credit,
        max_loss=max_loss,
    )


def spread_payoff_per_share(
    short_strike: float, long_strike: float, net_credit: float, spot_at_expiry: float
) -> tuple[float, str]:
    """Per-share payoff at expiry. Returns (pnl, outcome_label)."""
    width = short_strike - long_strike
    if spot_at_expiry >= short_strike:
        return net_credit, "expired_worthless"
    if spot_at_expiry >= long_strike:
        intrinsic = short_strike - spot_at_expiry  # long is 0
        return net_credit - intrinsic, "partial_loss"
    return net_credit - width, "max_loss"
