"""Data-layer: rollingoption sweeps + fixed-strike reconstruction + daily resample.

Key function: `load_fixed_strike_daily(client, underlying, expiry_code, expiry_flag,
option_type, entry_date, exit_date, strike, offset_range) → DataFrame[date, close, iv, spot, strike, oi, volume]`

Implementation note: Dhan's rollingoption returns the option that was at a given
ATM-offset AT EACH candle, not a fixed contract. To recover a fixed strike, we
pull `range(offset_lo, offset_hi)` offsets for the trade window, concat the
candles, filter by exact `strike`, and take the last candle of each trading
day (15:15 IST bar is the closing hourly for NSE).

Timezone convention: Dhan returns epoch seconds. We always decode with
Asia/Kolkata and carry `pd.Timestamp` in the returned frame's `date` column as
tz-naive IST midnight (date-only semantics).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from . import cache
from .client import DhanClient
from .config import IST
from .universe import Underlying


@dataclass(frozen=True, slots=True)
class RollingKey:
    underlying: str
    expiry_code: int
    expiry_flag: str
    option_type: str
    offset: int


def _cache_key(k: RollingKey, from_date: str, to_date: str) -> str:
    return f"{k.underlying}_{k.expiry_flag}{k.expiry_code}_{k.option_type}_{k.offset:+d}_{from_date}_{to_date}"


def _offset_arg(offset: int) -> str:
    return "ATM" if offset == 0 else f"ATM{offset:+d}"


def fetch_rolling_offset(
    client: DhanClient,
    under: Underlying,
    *,
    expiry_code: int,
    expiry_flag: str,
    option_type: str,
    offset: int,
    from_date: str,
    to_date: str,
    refresh: bool = False,
) -> pd.DataFrame:
    """Pull one offset's hourly candle series. Cached by (offset, dates)."""
    key = RollingKey(under.name, expiry_code, expiry_flag, option_type, offset)
    cache_key = _cache_key(key, from_date, to_date)
    if not refresh:
        hit = cache.load("rolling", cache_key)
        if hit is not None:
            return hit
    resp = client.rolling_option(
        exchange_segment=under.exchange_segment,
        instrument=under.instrument,
        security_id=under.security_id,
        expiry_code=expiry_code,
        expiry_flag=expiry_flag,
        strike=_offset_arg(offset),
        drv_option_type=option_type,
        interval=60,
        from_date=from_date,
        to_date=to_date,
    )
    leg_key = "ce" if option_type == "CALL" else "pe"
    leg = (resp.get("data") or {}).get(leg_key) or {}
    if not leg.get("close"):
        df = pd.DataFrame(columns=["t", "open", "high", "low", "close", "iv", "oi", "volume", "spot", "strike"])
    else:
        df = pd.DataFrame({
            "t": leg["timestamp"],
            "open": leg["open"],
            "high": leg["high"],
            "low": leg["low"],
            "close": leg["close"],
            "iv": leg["iv"],
            "oi": leg["oi"],
            "volume": leg["volume"],
            "spot": leg["spot"],
            "strike": leg["strike"],
        })
    cache.save("rolling", cache_key, df)
    return df


def load_underlying_daily(
    client: DhanClient,
    under: Underlying,
    *,
    from_date: str,
    to_date: str,
    refresh: bool = False,
) -> pd.DataFrame:
    """Daily OHLC of the underlying index. Cached per (underlying, range)."""
    key = f"{under.name}_{from_date}_{to_date}"
    if not refresh:
        hit = cache.load("index", key)
        if hit is not None:
            return hit
    resp = client.chart_historical(
        exchange_segment=under.underlying_seg,   # IDX_I
        instrument="INDEX",
        security_id=under.security_id,
        from_date=from_date,
        to_date=to_date,
        oi=False,
    )
    if not resp.get("close"):
        df = pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    else:
        ts = pd.to_datetime(resp["timestamp"], unit="s", utc=True).tz_convert(IST)
        df = pd.DataFrame({
            "date": ts.normalize().tz_localize(None),
            "open": resp["open"],
            "high": resp["high"],
            "low": resp["low"],
            "close": resp["close"],
        })
    cache.save("index", key, df)
    return df


def _resample_daily(bars: pd.DataFrame) -> pd.DataFrame:
    """Collapse hourly rows to daily close = last candle of each IST trading day."""
    if bars.empty:
        return bars.assign(date=pd.NaT).iloc[0:0]
    ts = pd.to_datetime(bars["t"], unit="s", utc=True).dt.tz_convert(IST)
    bars = bars.assign(_ts=ts, date=ts.dt.normalize().dt.tz_localize(None))
    bars = bars.sort_values("_ts")
    daily = bars.groupby("date", as_index=False).last().drop(columns=["_ts"])
    return daily


def load_fixed_strike_daily(
    client: DhanClient,
    under: Underlying,
    *,
    expiry_code: int,
    expiry_flag: str,
    option_type: str,
    strike: float,
    from_date: str,
    to_date: str,
    offset_range: tuple[int, int] = (-12, 12),
) -> pd.DataFrame:
    """Reconstruct a fixed-strike daily series from ATM-offset sweeps.

    `offset_range` is (inclusive_lo, inclusive_hi). Wider range = more rolling
    queries but better coverage as spot drifts. Default ±12 covers ~6% of spot
    at NIFTY's 50-point increment (plenty for a 35-DTE cycle).
    """
    rows: list[pd.DataFrame] = []
    lo, hi = offset_range
    for off in range(lo, hi + 1):
        bars = fetch_rolling_offset(
            client, under,
            expiry_code=expiry_code, expiry_flag=expiry_flag,
            option_type=option_type, offset=off,
            from_date=from_date, to_date=to_date,
        )
        if bars.empty:
            continue
        match = bars[bars["strike"] == float(strike)]
        if not match.empty:
            rows.append(match)
    if not rows:
        return pd.DataFrame(columns=["date", "close", "iv", "oi", "volume", "spot", "strike", "t"])
    combined = pd.concat(rows, ignore_index=True).drop_duplicates(subset=["t"])
    return _resample_daily(combined).reset_index(drop=True)


def load_atm_chain_snapshot(
    client: DhanClient,
    under: Underlying,
    *,
    expiry_code: int,
    expiry_flag: str,
    option_type: str,
    on_date: date,
    offset_range: tuple[int, int] = (-8, 2),
    window_days: int = 4,
) -> pd.DataFrame:
    """Return a DataFrame[strike, close, iv, spot] representing the chain on a
    specific date — pulled from rollingoption for each offset, last candle of
    `on_date` (or the nearest preceding trading day).

    Used by the strategy picker on entry day to find the strike at target delta.
    """
    from_d = on_date.isoformat()
    to_d = (pd.Timestamp(on_date) + pd.Timedelta(days=window_days)).date().isoformat()
    rows: list[pd.DataFrame] = []
    lo, hi = offset_range
    for off in range(lo, hi + 1):
        bars = fetch_rolling_offset(
            client, under,
            expiry_code=expiry_code, expiry_flag=expiry_flag,
            option_type=option_type, offset=off,
            from_date=from_d, to_date=to_d,
        )
        if bars.empty:
            continue
        daily = _resample_daily(bars)
        on_day = daily[daily["date"] == pd.Timestamp(on_date)]
        if on_day.empty:
            # fallback — last preceding day in range
            prior = daily[daily["date"] <= pd.Timestamp(on_date)]
            if prior.empty:
                continue
            on_day = prior.tail(1)
        rows.append(on_day.assign(offset=off))
    if not rows:
        return pd.DataFrame(columns=["strike", "close", "iv", "spot", "offset"])
    return pd.concat(rows, ignore_index=True).sort_values("strike").reset_index(drop=True)
