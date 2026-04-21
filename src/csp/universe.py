"""Option universe helpers: monthly expiries, chain discovery, strike selection.

On major-ETF and liquid-single-name underlyings, option tickers can be
constructed deterministically (`O:<UND><YYMMDD><C|P><STRIKE*1000:08d>`) which
lets us avoid chain-reference calls entirely when strikes come at known
increments (mostly $1 for ETFs in our universe).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from .client import MassiveClient


def make_option_ticker(underlying: str, expiry: date, kind: str, strike: float) -> str:
    """Build an OCC-style ticker. `kind` is 'C' or 'P'. `strike` dollars (float).

    Strike precision: strike * 1000 → integer → zero-padded 8.
    """
    k_int = int(round(strike * 1000))
    yymmdd = expiry.strftime("%y%m%d")
    return f"O:{underlying}{yymmdd}{kind.upper()}{k_int:08d}"


def third_friday(year: int, month: int) -> date:
    """Standard US equity-option monthly expiration: 3rd Friday of the month."""
    # calendar.monthcalendar returns weeks starting Monday; Friday is index 4
    cal = calendar.monthcalendar(year, month)
    fridays = [week[calendar.FRIDAY] for week in cal if week[calendar.FRIDAY] != 0]
    return date(year, month, fridays[2])


def monthly_expirations(start: date, end: date) -> list[date]:
    out: list[date] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        exp = third_friday(y, m)
        if start <= exp <= end:
            out.append(exp)
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def latest_trading_day_on_or_before(df_stock: pd.DataFrame, target: date) -> pd.Timestamp | None:
    """Given a DataFrame of stock bars with `date` col (Timestamp), return the
    most recent bar date ≤ target."""
    mask = df_stock["date"] <= pd.Timestamp(target)
    if not mask.any():
        return None
    return df_stock.loc[mask, "date"].max()


@dataclass(slots=True)
class ChainRow:
    ticker: str
    underlying: str
    strike: float
    expiration: date


def fetch_put_chain(
    client: MassiveClient, underlying: str, expiration: date
) -> list[ChainRow]:
    rows = client.contracts_for_expiration(
        underlying_ticker=underlying,
        expiration_date=expiration.isoformat(),
        contract_type="put",
        include_expired=True,
    )
    out: list[ChainRow] = []
    for r in rows:
        exp_str = r.get("expiration_date")
        try:
            exp = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        strike = r.get("strike_price")
        ticker = r.get("ticker")
        if strike is None or ticker is None:
            continue
        out.append(ChainRow(ticker, underlying, float(strike), exp))
    out.sort(key=lambda c: c.strike)
    return out


def select_candidate_strikes(
    chain: list[ChainRow], spot: float, otm_lo: float = 0.03, otm_hi: float = 0.15
) -> list[ChainRow]:
    """Puts with strike in [spot*(1-otm_hi), spot*(1-otm_lo)]."""
    k_min = spot * (1.0 - otm_hi)
    k_max = spot * (1.0 - otm_lo)
    return [c for c in chain if k_min <= c.strike <= k_max]


# ---------- date utilities ----------


def days_between(a: date, b: date) -> int:
    return (b - a).days


def year_fraction(a: date, b: date) -> float:
    return max(days_between(a, b), 0) / 365.0
