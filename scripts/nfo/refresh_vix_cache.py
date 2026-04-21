"""Refresh the cached India-VIX daily bars used by the offline threshold tuner.

Why this exists: `tune_thresholds.py` used to approximate the `vix_pct_3mo`
feature with a rolling-30-day realized-vol distribution over NIFTY closes.
`regime_watch.py` computes the same feature against a VIX daily distribution.
Those two distributions are materially different (realized vol ≠ implied vol),
which meant thresholds learned offline were not apples-to-apples with the
signal graded live. This script caches real VIX history so the tuner can use
the same distribution as the live watcher.

Output: `data/nfo/index/VIX_{from}_{to}.parquet` — columns `date, open, high,
low, close`. Idempotent; safe to re-run daily. The tuner's loader globs the
directory and concatenates whatever ranges exist, so partial refreshes compose.

Usage:
    .venv/bin/python scripts/nfo/refresh_vix_cache.py
    .venv/bin/python scripts/nfo/refresh_vix_cache.py --from 2022-01-01
    .venv/bin/python scripts/nfo/refresh_vix_cache.py --from 2024-01-01 --to 2025-12-31
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

import pandas as pd

from nfo import cache
from nfo.client import DhanClient
from nfo.config import IST

# India VIX — Dhan identifiers. The underlying universe entry only covers
# tradable indices (NIFTY / BANKNIFTY / FINNIFTY), so we keep VIX's ids here.
VIX_SECURITY_ID = 21
VIX_EXCHANGE_SEGMENT = "IDX_I"
VIX_INSTRUMENT = "INDEX"


def _fetch_vix_daily(client: DhanClient, from_date: str, to_date: str) -> pd.DataFrame:
    resp = client.chart_historical(
        exchange_segment=VIX_EXCHANGE_SEGMENT,
        instrument=VIX_INSTRUMENT,
        security_id=VIX_SECURITY_ID,
        from_date=from_date,
        to_date=to_date,
        oi=False,
    )
    if not resp.get("close"):
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    # pd.to_datetime on a sequence returns a DatetimeIndex; use its own
    # tz_convert/normalize methods (no .dt accessor on a DatetimeIndex).
    ts = pd.to_datetime(resp["timestamp"], unit="s", utc=True).tz_convert(IST)
    return pd.DataFrame({
        "date": ts.normalize().tz_localize(None),
        "open": resp["open"],
        "high": resp["high"],
        "low": resp["low"],
        "close": resp["close"],
    })


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    # Default window matches the NIFTY index cache span currently on disk, so a
    # fresh checkout re-tunes with the same coverage the backtest assumes.
    default_from = (date.today() - timedelta(days=365 * 3)).isoformat()
    default_to = date.today().isoformat()
    p.add_argument("--from", dest="from_date", default=default_from,
                   help=f"ISO date (default: {default_from})")
    p.add_argument("--to", dest="to_date", default=default_to,
                   help=f"ISO date (default: {default_to})")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("refresh_vix_cache")

    log.info("Fetching India-VIX daily bars %s → %s", args.from_date, args.to_date)
    with DhanClient() as client:
        df = _fetch_vix_daily(client, args.from_date, args.to_date)

    if df.empty:
        log.error("No VIX bars returned for %s → %s — aborting.", args.from_date, args.to_date)
        return 1

    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    key = f"VIX_{args.from_date}_{args.to_date}"
    cache.save("index", key, df)
    log.info("Wrote %d rows → data/nfo/index/%s.parquet (span %s → %s)",
             len(df), key, df["date"].min().date(), df["date"].max().date())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
