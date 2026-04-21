"""Data fetch helpers that sit on top of the raw client and Parquet cache."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from . import cache
from .client import MassiveClient


def _bars_to_df(bars: list[dict]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=["t", "o", "h", "l", "c", "v", "vw", "n", "date"])
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["t"], unit="ms").dt.tz_localize(None).dt.normalize()
    return df


def load_stock_bars(
    client: MassiveClient,
    ticker: str,
    start: date,
    end: date,
    refresh: bool = False,
) -> pd.DataFrame:
    key = ticker
    existing = None if refresh else cache.load("stock_bars", key)
    if existing is not None and not existing.empty:
        have_start = existing["date"].min().date()
        have_end = existing["date"].max().date()
        if have_start <= start and have_end >= end:
            mask = (existing["date"] >= pd.Timestamp(start)) & (existing["date"] <= pd.Timestamp(end))
            return existing.loc[mask].reset_index(drop=True)

    bars = client.stock_aggs(ticker, 1, "day", start.isoformat(), end.isoformat())
    df = _bars_to_df(bars)
    if df.empty:
        return df
    combined = cache.upsert_bars("stock_bars", key, df)
    mask = (combined["date"] >= pd.Timestamp(start)) & (combined["date"] <= pd.Timestamp(end))
    return combined.loc[mask].reset_index(drop=True)


def load_option_bars(
    client: MassiveClient,
    option_ticker: str,
    start: date,
    end: date,
    refresh: bool = False,
) -> pd.DataFrame:
    key = option_ticker
    existing = None if refresh else cache.load("option_bars", key)
    if existing is not None and not existing.empty:
        have_start = existing["date"].min().date()
        have_end = existing["date"].max().date()
        if have_start <= start and have_end >= end:
            mask = (existing["date"] >= pd.Timestamp(start)) & (existing["date"] <= pd.Timestamp(end))
            return existing.loc[mask].reset_index(drop=True)

    bars = client.option_aggs(option_ticker, 1, "day", start.isoformat(), end.isoformat())
    df = _bars_to_df(bars)
    if df.empty:
        return df
    combined = cache.upsert_bars("option_bars", key, df)
    mask = (combined["date"] >= pd.Timestamp(start)) & (combined["date"] <= pd.Timestamp(end))
    return combined.loc[mask].reset_index(drop=True)
