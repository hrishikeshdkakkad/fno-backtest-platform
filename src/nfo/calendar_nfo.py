"""NFO expiry calendar + cycle entry-date generation.

NIFTY monthly expiries were the last Thursday of month through early 2025,
then shifted to the last Tuesday as SEBI's expiry-consolidation reform took
effect. BANKNIFTY monthly has historically been the last Thursday; only
weeklies were affected by the reform.

Concrete cutover we adopted (empirically correct against Dhan's data):

  * NIFTY      : last Thursday of month for expiry ≤ 2025-03-27;
                 last Tuesday  of month from 2025-04 onwards.
  * BANKNIFTY  : last Thursday of month throughout.

Holiday adjustment: if the computed date isn't in the underlying's daily bar
frame (public holiday / exchange closure), we walk one day earlier at a time
until we land on a trading day. Max 4 step-backs before we give up.
"""
from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .universe import Underlying

# Post-reform cutover month (exclusive). First NIFTY monthly that uses Tuesday.
NIFTY_REFORM_CUTOVER = date(2025, 4, 1)


@dataclass(frozen=True, slots=True)
class MonthlyCycle:
    year: int
    month: int
    expiry_date: date
    entry_target_date: date   # expiry - ~35 calendar days, snapped to NSE business day


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """weekday: Monday=0, Tuesday=1, …, Thursday=3, Sunday=6."""
    last_day = _cal.monthrange(year, month)[1]
    for d in range(last_day, last_day - 7, -1):
        dt = date(year, month, d)
        if dt.weekday() == weekday:
            return dt
    raise AssertionError("unreachable — week always contains every weekday")


def _adjust_to_trading_day(candidate: date, spot_daily: pd.DataFrame, back: int = 4) -> date | None:
    dates = set(spot_daily["date"].dt.date)
    for i in range(back + 1):
        probe = candidate - timedelta(days=i)
        if probe in dates:
            return probe
    return None


def _first_trading_day_on_or_after(target: date, spot_daily: pd.DataFrame) -> date | None:
    later = spot_daily.loc[spot_daily["date"] >= pd.Timestamp(target), "date"]
    if later.empty:
        return None
    return later.iloc[0].date()


def monthly_expiry(under: Underlying, year: int, month: int, spot_daily: pd.DataFrame) -> date | None:
    """Return the NSE monthly expiry for (year, month) adjusted for holidays.

    Falls back to None if the adjusted date isn't in the daily bar frame (e.g.
    the entire week is out of the data window).
    """
    if under.name == "NIFTY":
        if date(year, month, 1) < NIFTY_REFORM_CUTOVER:
            cand = _last_weekday_of_month(year, month, weekday=3)    # Thursday
        else:
            cand = _last_weekday_of_month(year, month, weekday=1)    # Tuesday
    elif under.name == "BANKNIFTY":
        cand = _last_weekday_of_month(year, month, weekday=3)        # Thursday
    else:
        cand = _last_weekday_of_month(year, month, weekday=3)
    return _adjust_to_trading_day(cand, spot_daily)


def _month_range(start: date, end: date) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def build_cycles(
    under: Underlying,
    spot_daily: pd.DataFrame,
    start: date,
    end: date,
    target_dte: int = 35,
) -> list[MonthlyCycle]:
    """Produce one MonthlyCycle per month in [start, end] with the entry date
    snapped to the first trading day ≥ (expiry − target_dte).
    """
    cycles: list[MonthlyCycle] = []
    for y, m in _month_range(start, end):
        expiry = monthly_expiry(under, y, m, spot_daily)
        if expiry is None:
            continue
        entry_target = expiry - timedelta(days=target_dte)
        entry = _first_trading_day_on_or_after(entry_target, spot_daily)
        if entry is None or not (start <= entry <= end):
            continue
        cycles.append(MonthlyCycle(year=y, month=m, expiry_date=expiry, entry_target_date=entry))
    return cycles
