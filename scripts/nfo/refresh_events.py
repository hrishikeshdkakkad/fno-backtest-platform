"""Refresh the cached event calendar + FII/DII flow via Parallel.ai.

Idempotent; safe to run daily from cron at 07:00 IST. Cached responses are
honoured for TTLs defined in `src/nfo/events.py` and `src/nfo/enrich.py`.

Usage:
    .venv/bin/python scripts/nfo/refresh_events.py
    .venv/bin/python scripts/nfo/refresh_events.py --horizon-days 60
    .venv/bin/python scripts/nfo/refresh_events.py --include events,fii_dii
    .venv/bin/python scripts/nfo/refresh_events.py --dry-run         # offline mode

Environment:
    PARALLEL_API_KEY   (required unless --dry-run or PARALLEL_OFFLINE=1)
    PARALLEL_OFFLINE=1 (optional — force offline, cache-only)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from nfo import enrich, events
from nfo.parallel_client import ParallelClient, ParallelOfflineMiss


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--horizon-days", type=int, default=90)
    p.add_argument("--include", default="events,fii_dii",
                   help="Comma-separated list: events,earnings,holidays,fii_dii,brief")
    p.add_argument("--dry-run", action="store_true",
                   help="Force offline mode; do not make network calls.")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("refresh_events")

    wanted = {s.strip() for s in args.include.split(",") if s.strip()}
    client = ParallelClient(offline=args.dry_run or None)

    log.info("Refreshing: %s (horizon=%d days)", sorted(wanted), args.horizon_days)
    n_ok = 0
    if "events" in wanted or "earnings" in wanted or "holidays" in wanted:
        try:
            df = events.refresh_all(
                horizon_days=args.horizon_days,
                include_earnings=("earnings" in wanted),
                include_holidays=("holidays" in wanted),
                client=client,
            )
            log.info("events.parquet: %d rows", len(df))
            n_ok += 1
        except ParallelOfflineMiss:
            log.warning("events: no cached response, skipping (offline).")
        except Exception as exc:
            log.exception("events refresh failed: %s", exc)

    if "fii_dii" in wanted:
        try:
            df = enrich.fii_dii_flow(client=client, lookback_days=args.horizon_days)
            log.info("fii_dii_flow.parquet: %d rows", len(df))
            n_ok += 1
        except ParallelOfflineMiss:
            log.warning("fii_dii: no cached response, skipping.")
        except Exception as exc:
            log.exception("fii_dii refresh failed: %s", exc)

    if "brief" in wanted:
        try:
            brief = enrich.macro_brief(client=client)
            log.info("macro_brief refreshed: %s", brief.summary[:120])
            n_ok += 1
        except ParallelOfflineMiss:
            log.warning("brief: no cached response, skipping.")
        except Exception as exc:
            log.exception("brief refresh failed: %s", exc)

    log.info("Done (%d / %d paths refreshed).", n_ok, len(wanted))
    return 0 if n_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
