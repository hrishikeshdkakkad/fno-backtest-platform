"""Calendar expiry-derivation tests (no network)."""
from __future__ import annotations

from datetime import date

import pandas as pd

from nfo.calendar_nfo import (
    _first_trading_day_on_or_after,
    _last_weekday_of_month,
    build_cycles,
    monthly_expiry,
)
from nfo.universe import get


def _spot(dates: list[date]) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(dates), "close": [1.0] * len(dates)})


def test_last_weekday_of_month_thursday() -> None:
    assert _last_weekday_of_month(2024, 1, weekday=3) == date(2024, 1, 25)
    assert _last_weekday_of_month(2024, 2, weekday=3) == date(2024, 2, 29)
    assert _last_weekday_of_month(2024, 3, weekday=3) == date(2024, 3, 28)


def test_last_weekday_of_month_tuesday() -> None:
    assert _last_weekday_of_month(2026, 4, weekday=1) == date(2026, 4, 28)
    assert _last_weekday_of_month(2026, 5, weekday=1) == date(2026, 5, 26)


def test_nifty_expiry_pre_reform_uses_thursday() -> None:
    spot = _spot([date(2024, 1, d) for d in range(2, 31)])
    # 2024-01 last Thursday = 2024-01-25
    assert monthly_expiry(get("NIFTY"), 2024, 1, spot) == date(2024, 1, 25)


def test_nifty_expiry_post_reform_uses_tuesday() -> None:
    # 2025-10 last Tuesday = 2025-10-28
    days = pd.date_range("2025-10-01", "2025-10-31").date.tolist()
    spot = _spot(days)
    assert monthly_expiry(get("NIFTY"), 2025, 10, spot) == date(2025, 10, 28)


def test_banknifty_always_thursday() -> None:
    days = pd.date_range("2026-05-01", "2026-05-31").date.tolist()
    spot = _spot(days)
    assert monthly_expiry(get("BANKNIFTY"), 2026, 5, spot) == date(2026, 5, 28)


def test_holiday_adjusts_backwards() -> None:
    # Pretend 2024-01-25 was a holiday → fall back to Wednesday the 24th.
    days = pd.date_range("2024-01-02", "2024-01-31").date.tolist()
    days.remove(date(2024, 1, 25))
    spot = _spot(days)
    assert monthly_expiry(get("NIFTY"), 2024, 1, spot) == date(2024, 1, 24)


def test_build_cycles_produces_one_per_month() -> None:
    spot = _spot(pd.date_range("2024-01-01", "2024-07-01").date.tolist())
    cycles = build_cycles(get("NIFTY"), spot, date(2024, 1, 1), date(2024, 6, 30), target_dte=35)
    # Jan entry lands at Jan 2 (35 days before last Thursday Jan 25 ≈ Dec 21,
    # snapped forward to first-trading-day = Jan 2). July cycle's entry would
    # be May 27 for the June expiry — already covered as cycle #6.
    assert [c.month for c in cycles] == [1, 2, 3, 4, 5, 6]


def test_first_trading_day_on_or_after() -> None:
    spot = _spot([date(2024, 1, 2), date(2024, 1, 5), date(2024, 1, 8)])
    assert _first_trading_day_on_or_after(date(2024, 1, 3), spot) == date(2024, 1, 5)
    assert _first_trading_day_on_or_after(date(2024, 1, 9), spot) is None
