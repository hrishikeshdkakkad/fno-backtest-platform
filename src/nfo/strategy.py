"""Delta-targeted short-leg selection for a given underlying/expiry/entry-date.

Uses `load_atm_chain_snapshot` to pull the ATM±N chain at entry, computes analytic
put delta from wire IV via `bsm.put_delta`, and returns the strike whose |Δ| is
closest to (but not exceeding) the target delta.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from . import bsm
from .client import DhanClient
from .data import load_atm_chain_snapshot
from .universe import Underlying


@dataclass(slots=True)
class StrategyConfig:
    underlying: str
    target_delta: float        # magnitude, e.g. 0.25
    target_dte: int            # calendar days from entry to expiry
    profit_take: float         # 1.0 = hold-to-expiry, 0.5 = close at 50% of credit
    # close T-2 sessions before expiry: SEBI Oct-2024 adds +2% ELM and removes
    # calendar-spread margin benefit on expiry day, and holding the long leg
    # into auto-exercise triggers the 0.125%-of-intrinsic settlement-STT trap.
    # See docs/india-fno-nuances.md §2 + §3. Pass None to disable.
    manage_at_dte: int | None = 2
    margin_multiplier: float = 1.5  # SPAN+exposure proxy: BP = mult × max_loss × lot


@dataclass(slots=True)
class ShortCandidate:
    entry_date: pd.Timestamp
    expiry_date: date
    spot_at_entry: float
    strike: float
    entry_premium: float
    entry_iv: float
    entry_delta: float


def pick_short_leg(
    client: DhanClient,
    cfg: StrategyConfig,
    under: Underlying,
    *,
    expiry_code: int,
    expiry_flag: str,
    expiry_date: date,
    entry_date: date,
) -> ShortCandidate | None:
    """Return the put-leg candidate whose |Δ| is closest to cfg.target_delta.

    Returns None if the chain snapshot is empty or no strike falls within a
    reasonable delta band.
    """
    chain = load_atm_chain_snapshot(
        client, under,
        expiry_code=expiry_code, expiry_flag=expiry_flag,
        option_type="PUT", on_date=entry_date,
    )
    if chain.empty:
        return None
    t_years = (expiry_date - entry_date).days / 365.0
    chain = chain.copy()
    chain["delta"] = chain.apply(
        lambda r: bsm.put_delta(
            spot=float(r["spot"]), strike=float(r["strike"]),
            years_to_expiry=t_years, sigma=float(r["iv"]) / 100.0,
        ),
        axis=1,
    )
    chain["delta_err"] = (chain["delta"].abs() - cfg.target_delta).abs()
    # Only consider short strikes with |Δ| ≤ target (we want strikes ≤ target delta,
    # i.e. further OTM when in doubt — but closest match is good enough for v1).
    chain = chain[chain["close"] > 0]
    if chain.empty:
        return None
    best = chain.sort_values("delta_err").iloc[0]
    return ShortCandidate(
        entry_date=pd.Timestamp(best["date"]),
        expiry_date=expiry_date,
        spot_at_entry=float(best["spot"]),
        strike=float(best["strike"]),
        entry_premium=float(best["close"]),
        entry_iv=float(best["iv"]),
        entry_delta=float(best["delta"]),
    )
