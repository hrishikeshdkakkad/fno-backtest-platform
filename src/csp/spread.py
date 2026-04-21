"""Put credit spread (bull put spread) strategy module.

A put credit spread simultaneously:
  - sells an OTM put (the "short" leg) — collects premium
  - buys a further-OTM put (the "long" leg) — pays premium, caps the loss

Net credit = short premium - long premium.
Max loss per share = spread_width - net_credit.
Buying power = max_loss * 100 (per contract).

The short leg is chosen exactly the same way as a CSP short put — by
analytical delta targeting in `strategy.pick_put_for_cycle`. The long leg
is then the short strike minus `spread_width`, rounded to the valid strike
increment. We probe the long strike directly (OCC ticker construction);
if that contract doesn't exist at the exact strike, we walk ±1 tick
before giving up on the cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from .client import MassiveClient
from .data import load_option_bars
from .strategy import StrategyConfig, TradeCandidate, pick_put_for_cycle
from .universe import make_option_ticker


@dataclass(slots=True)
class SpreadConfig(StrategyConfig):
    spread_width: float = 10.0   # dollars between short strike and long strike


@dataclass(slots=True)
class SpreadCandidate:
    cfg: SpreadConfig
    entry_date: pd.Timestamp
    expiry_date: date
    spot_at_entry: float
    # short leg
    short_strike: float
    short_ticker: str
    short_premium: float
    short_iv: float
    short_delta: float
    # long leg
    long_strike: float
    long_ticker: str
    long_premium: float
    # derived
    net_credit: float        # per share (short_premium - long_premium)
    max_loss: float          # per share (width - net_credit)
    buying_power: float      # max_loss * 100 (per contract dollars)


def _load_long_leg(
    client: MassiveClient,
    underlying: str,
    expiry: date,
    long_strike: float,
    strike_increment: float,
    entry_ts: pd.Timestamp,
) -> tuple[float, float, str] | None:
    """Return (long_strike, long_close, long_ticker) — probing ±1 tick if empty."""
    candidates = [long_strike, long_strike - strike_increment, long_strike + strike_increment]
    for k in candidates:
        if k <= 0:
            continue
        ticker = make_option_ticker(underlying, expiry, "P", k)
        bars = load_option_bars(client, ticker, entry_ts.date(), expiry)
        if bars.empty:
            continue
        row = bars[bars["date"] == entry_ts]
        if row.empty:
            row = bars.head(1)
            if row.empty:
                continue
        close = float(row["c"].iloc[0])
        if close <= 0:
            continue
        return k, close, ticker
    return None


def pick_put_spread_for_cycle(
    client: MassiveClient,
    cfg: SpreadConfig,
    stock_df: pd.DataFrame,
    entry_ts: pd.Timestamp,
    expiry: date,
) -> SpreadCandidate | None:
    """Pick a put credit spread for this cycle. Short leg is delta-targeted.

    Returns None when:
      - short leg can't be found at the target delta
      - long leg contract is missing at/near the target strike
      - net credit would be non-positive (skipped cycle)
    """
    short: TradeCandidate | None = pick_put_for_cycle(client, cfg, stock_df, entry_ts, expiry)
    if short is None:
        return None

    target_long_strike = short.strike - cfg.spread_width
    long_info = _load_long_leg(
        client,
        cfg.underlying,
        expiry,
        target_long_strike,
        cfg.strike_increment,
        entry_ts,
    )
    if long_info is None:
        return None
    long_strike, long_premium, long_ticker = long_info

    net_credit = short.entry_premium - long_premium
    if net_credit <= 0:
        return None

    actual_width = short.strike - long_strike
    max_loss = actual_width - net_credit
    return SpreadCandidate(
        cfg=cfg,
        entry_date=entry_ts,
        expiry_date=expiry,
        spot_at_entry=short.spot_at_entry,
        short_strike=short.strike,
        short_ticker=short.option_ticker,
        short_premium=short.entry_premium,
        short_iv=short.entry_iv,
        short_delta=short.entry_delta,
        long_strike=long_strike,
        long_ticker=long_ticker,
        long_premium=long_premium,
        net_credit=net_credit,
        max_loss=max_loss,
        buying_power=max_loss * 100.0,
    )


def spread_payoff_per_share(
    short_strike: float, long_strike: float, net_credit: float, underlying_close: float
) -> tuple[float, str]:
    """Payoff at expiry. Returns (pnl_per_share, outcome_label).

    For a put credit spread:
      - S >= short_strike: both expire worthless, keep net_credit → outcome expired_worthless
      - long_strike <= S < short_strike: short is ITM, long is OTM → partial_loss
      - S < long_strike: both ITM, net intrinsic = width → max_loss
    """
    width = short_strike - long_strike
    if underlying_close >= short_strike:
        return net_credit, "expired_worthless"
    if underlying_close >= long_strike:
        intrinsic = short_strike - underlying_close   # long is 0
        pnl = net_credit - intrinsic
        return pnl, "partial_loss"
    # below long strike → max loss
    return net_credit - width, "max_loss"
