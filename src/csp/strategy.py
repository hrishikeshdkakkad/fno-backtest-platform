"""CSP strategy configuration and per-cycle trade selection.

We select strikes analytically using realized vol as an IV proxy, then verify
the chosen contract's implied delta ex-post. This keeps the API call budget
low (1 contract pull per cycle vs ~15 for full chain probing) and still gives
a delta-targeted entry in practice because realized vol and implied vol on
major ETFs correlate strongly on ~30-day horizons.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy.stats import norm

from . import bsm
from .client import MassiveClient
from .data import load_option_bars
from .universe import make_option_ticker


@dataclass(slots=True)
class StrategyConfig:
    underlying: str
    target_delta: float = 0.20
    target_dte: int = 35
    profit_take: float = 0.5
    stop_loss_mult: float | None = None
    div_yield: float = 0.0
    risk_free_rate: float = 0.045
    manage_at_dte: int | None = None
    strike_increment: float = 1.0          # $1 for most ETFs; $0.5 for lower-priced names
    vol_window: int = 30                   # days for realized-vol proxy


@dataclass(slots=True)
class TradeCandidate:
    cfg: StrategyConfig
    entry_date: pd.Timestamp
    expiry_date: date
    spot_at_entry: float
    strike: float
    option_ticker: str
    entry_premium: float          # per-share put close on entry day
    entry_iv: float               # implied vol solved from entry_premium
    entry_delta: float            # negative for put
    estimated_strike: float       # what our RV proxy suggested


def realized_vol(stock_df: pd.DataFrame, as_of: pd.Timestamp, window: int = 30) -> float:
    """Annualized realized vol from log returns over the trailing `window` trading days."""
    past = stock_df.loc[stock_df["date"] <= as_of].tail(window + 1)
    if len(past) < 5:
        return 0.20  # sensible default
    rets = np.log(past["c"].values[1:] / past["c"].values[:-1])
    return float(np.std(rets, ddof=1) * math.sqrt(252))


def estimate_target_strike(
    spot: float,
    sigma: float,
    t_years: float,
    target_delta: float,
    r: float,
    q: float,
) -> float:
    """Closed-form strike such that a European put has -target_delta delta.

    put delta = e^(-qT) * (N(d1) - 1) = -target_delta
    ⇒ N(d1) = 1 - target_delta / e^(-qT)
    ⇒ d1 = Φ^-1(1 - target_delta * e^(qT))
    And k = s * exp(-(d1 * sigma * sqrt(t) - (r - q + 0.5 sigma^2) * t))
    """
    if sigma <= 0 or t_years <= 0:
        return spot
    target = 1.0 - target_delta * math.exp(q * t_years)
    target = min(max(target, 1e-4), 1 - 1e-4)
    d1 = norm.ppf(target)
    exponent = -(d1 * sigma * math.sqrt(t_years) - (r - q + 0.5 * sigma * sigma) * t_years)
    return spot * math.exp(exponent)


def round_strike(k: float, increment: float) -> float:
    return math.floor(k / increment) * increment  # round down → more OTM → safer


def pick_put_for_cycle(
    client: MassiveClient,
    cfg: StrategyConfig,
    stock_df: pd.DataFrame,
    entry_ts: pd.Timestamp,
    expiry: date,
) -> TradeCandidate | None:
    row = stock_df.loc[stock_df["date"] == entry_ts]
    if row.empty:
        return None
    spot = float(row["c"].iloc[0])

    sigma_hat = realized_vol(stock_df, entry_ts, cfg.vol_window)
    t_years = max((expiry - entry_ts.date()).days, 1) / 365.0

    est_strike = estimate_target_strike(
        spot, sigma_hat, t_years, cfg.target_delta, cfg.risk_free_rate, cfg.div_yield
    )
    k0 = round_strike(est_strike, cfg.strike_increment)

    # Primary probe at k0, then only fall back to neighbors if empty.
    probed: dict[float, TradeCandidate | None] = {}

    def probe(k: float) -> TradeCandidate | None:
        if k <= 0 or k >= spot:
            return None
        if k in probed:
            return probed[k]
        ticker = make_option_ticker(cfg.underlying, expiry, "P", k)
        bars = load_option_bars(client, ticker, entry_ts.date(), expiry)
        if bars.empty:
            probed[k] = None
            return None
        entry_bars = bars[bars["date"] == entry_ts]
        if entry_bars.empty:
            entry_bars = bars.head(1)
            if entry_bars.empty:
                probed[k] = None
                return None
        put_close = float(entry_bars["c"].iloc[0])
        if put_close <= 0:
            probed[k] = None
            return None
        iv = bsm.implied_vol_put(put_close, spot, k, t_years, cfg.risk_free_rate, cfg.div_yield)
        if iv != iv or iv <= 0:
            probed[k] = None
            return None
        delta = bsm.put_delta(spot, k, t_years, cfg.risk_free_rate, cfg.div_yield, iv)
        candidate = TradeCandidate(
            cfg=cfg,
            entry_date=entry_ts,
            expiry_date=expiry,
            spot_at_entry=spot,
            strike=k,
            option_ticker=ticker,
            entry_premium=put_close,
            entry_iv=iv,
            entry_delta=delta,
            estimated_strike=est_strike,
        )
        probed[k] = candidate
        return candidate

    # 1) try estimated strike
    primary = probe(k0)
    if primary is not None and abs(abs(primary.entry_delta) - cfg.target_delta) <= 0.05:
        return primary

    # 2) walk neighbors (up to ±3 ticks) looking for valid contracts with
    #    delta closer to target
    best = primary
    best_err = float("inf") if primary is None else abs(abs(primary.entry_delta) - cfg.target_delta)
    for step in (1, 2, 3):
        for sign in (-1, 1):
            k = k0 + sign * step * cfg.strike_increment
            c = probe(k)
            if c is None:
                continue
            err = abs(abs(c.entry_delta) - cfg.target_delta)
            if err < best_err:
                best_err = err
                best = c
        if best is not None and best_err <= 0.03:
            break
    return best
