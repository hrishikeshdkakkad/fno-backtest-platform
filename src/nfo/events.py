"""Event calendar — RBI, Budget, FOMC, US-CPI, NIFTY-50 earnings, NSE holidays.

Why this module exists: every max-loss cycle in our 2.5 y backtest clusters
around a handful of scheduled events (Feb-Budget, RBI MPC, FOMC decisions,
US CPI surprises, quarterly-results season). Blindly selling puts through
these dates is the single largest avoidable contributor to the −$6,001
worst month. `regime_watch.py` should downgrade or skip cycles where any
high-severity event falls within DTE.

All network I/O happens inside `refresh_all()`; the rest of the module is
a pure parquet-and-date filter layer that the live TUI can hammer safely.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from .config import DATA_DIR
from .parallel_client import ParallelClient, ParallelOfflineMiss, default_client

EVENTS_PATH: Path = DATA_DIR / "events.parquet"

EventKind = Literal["RBI", "BUDGET", "FOMC", "CPI", "EARNINGS", "EXPIRY", "HOLIDAY", "OTHER"]
Severity = Literal["low", "medium", "high"]


# ── Pydantic schemas (consumed by Parallel Task API) ────────────────────────


class EventRecord(BaseModel):
    date: date
    name: str
    kind: EventKind = "OTHER"
    severity: Severity = "medium"
    source_url: str = ""
    notes: str | None = None


class EventBundle(BaseModel):
    """Container the Parallel Task API fills in — one call returns all macro events."""
    events: list[EventRecord] = Field(default_factory=list)
    horizon_days: int = 90


# ── Public accessors (pure lookups) ─────────────────────────────────────────


@dataclass(slots=True)
class EventFlag:
    severity: Severity
    events: list[EventRecord]

    def any(self) -> bool:
        return bool(self.events)


def load_events() -> pd.DataFrame:
    """Return the cached events DataFrame; empty frame if refresh has never run."""
    if not EVENTS_PATH.exists():
        return pd.DataFrame(columns=["date", "name", "kind", "severity", "source_url", "notes"])
    return pd.read_parquet(EVENTS_PATH)


def upcoming_events(entry_date: date, dte: int) -> list[EventRecord]:
    """Events in [entry_date, entry_date + dte]. Sorted ascending by date."""
    df = load_events()
    if df.empty:
        return []
    cutoff = entry_date + pd.Timedelta(days=dte)
    mask = (df["date"] >= pd.Timestamp(entry_date)) & (df["date"] <= pd.Timestamp(cutoff))
    rows = df.loc[mask].sort_values("date").to_dict(orient="records")
    return [EventRecord(**_coerce(r)) for r in rows]


def event_risk_flag(upcoming: Iterable[EventRecord]) -> EventFlag:
    """Aggregate list of events into a single severity label.

    Rules:
      - Any RBI / BUDGET / FOMC / CPI / high-severity → severity=high
      - Any medium-severity event                     → severity=medium
      - Otherwise                                     → severity=low
    """
    events = list(upcoming)
    if not events:
        return EventFlag(severity="low", events=[])
    high_kinds = {"RBI", "BUDGET", "FOMC", "CPI"}
    if any(e.kind in high_kinds or e.severity == "high" for e in events):
        return EventFlag(severity="high", events=events)
    if any(e.severity == "medium" for e in events):
        return EventFlag(severity="medium", events=events)
    return EventFlag(severity="low", events=events)


# V3 set: CPI demoted to "medium". V3's gate only fails on RBI / FOMC / Budget
# appearing in the first N days of the cycle — CPI is informational, not
# blocking. Chosen in the 2026-04-20 iterative redesign (see
# results/nfo/redesign_winner.json + project_winning_filter_v3 memory).
V3_HIGH_KINDS: frozenset[str] = frozenset({"RBI", "BUDGET", "FOMC"})
V3_EVENT_WINDOW_DAYS: int = 10


def v3_event_risk_flag(
    entry_date: date,
    dte: int,
    *,
    window_days: int = V3_EVENT_WINDOW_DAYS,
    high_kinds: frozenset[str] = V3_HIGH_KINDS,
) -> EventFlag:
    """V3's event check — CPI demoted to medium, only first N days checked.

    Reads the same `events.parquet` cache as `event_risk_flag` but applies
    V3's tighter rules:
      - `high_kinds` defaults to {RBI, FOMC, BUDGET} only.
      - Only events in `[entry_date, entry_date + window_days]` count.

    Returns an EventFlag whose severity is "high" iff at least one event of
    the restricted high_kinds falls in the window; "medium" if other events
    are present; "low" otherwise.
    """
    if not np.isfinite(dte) or dte <= 0:
        return EventFlag(severity="low", events=[])
    look = min(int(dte), int(window_days))
    window_events = upcoming_events(entry_date, look)
    if not window_events:
        return EventFlag(severity="low", events=[])
    # V3 decides severity by KIND only. Not by the severity field baked in
    # at refresh time (refresh_macro_events force-sets every macro event to
    # severity='high', which would undo V3's kind-based CPI demotion).
    if any(e.kind in high_kinds for e in window_events):
        return EventFlag(severity="high", events=window_events)
    non_v3_high_kinds = {"CPI"}  # present but demoted — show as medium
    if any(e.kind in non_v3_high_kinds for e in window_events):
        return EventFlag(severity="medium", events=window_events)
    return EventFlag(severity="low", events=window_events)


# ── Refresh path (network-backed) ───────────────────────────────────────────


_MACRO_PROMPT = (
    "Produce a structured list of all upcoming Indian and US monetary / fiscal "
    "events relevant to NIFTY index options traders in the next {horizon_days} days. "
    "Include: RBI MPC decisions (kind=RBI), Union Budget of India (kind=BUDGET), "
    "US FOMC decisions (kind=FOMC), US CPI release dates (kind=CPI). "
    "For each, set severity='high'. Include authoritative source URLs (RBI, "
    "ministry of finance, Federal Reserve, BLS). Today's date is {today}. "
    "Only include events with a confirmed scheduled date."
)


def refresh_macro_events(
    horizon_days: int = 90,
    *,
    client: ParallelClient | None = None,
    today: date | None = None,
) -> list[EventRecord]:
    """Deep-research call returning RBI / Budget / FOMC / CPI macro events."""
    c = client or default_client()
    t = today or date.today()
    bundle = c.task(
        input=_MACRO_PROMPT.format(horizon_days=horizon_days, today=t.isoformat()),
        output_model=EventBundle,
        processor="core",
        ttl_sec=86_400,
    )
    # Ensure every event is tagged high-severity regardless of what the model returned.
    for ev in bundle.events:
        ev.severity = "high"
    return bundle.events


def refresh_earnings(
    horizon_days: int = 90,
    *,
    client: ParallelClient | None = None,
    today: date | None = None,
) -> list[EventRecord]:
    """FindAll-API call for NIFTY-50 constituents with earnings dates in window."""
    c = client or default_client()
    t = today or date.today()
    matches = c.findall(
        objective=(
            "Find every NIFTY 50 constituent with a confirmed earnings / quarterly "
            f"results announcement in the next {horizon_days} calendar days from {t.isoformat()}."
        ),
        entity_type="NIFTY 50 constituent earnings announcement",
        match_conditions=[
            {
                "name": "company_name",
                "description": "NSE ticker symbol of the NIFTY 50 constituent.",
            },
            {
                "name": "announcement_date",
                "description": "ISO-8601 date (YYYY-MM-DD) of the scheduled results announcement.",
            },
            {
                "name": "source_url",
                "description": "Authoritative URL — NSE corporate announcements or the company investor-relations page.",
            },
        ],
        generator="core",
        match_limit=60,
        ttl_sec=86_400,
    )
    records: list[EventRecord] = []
    for m in matches:
        # Each Candidate is {name, url, output: {match_condition_name: value, ...}, ...}.
        # We prefer the `output` dict for structured fields, then fall back to
        # top-level Candidate fields (`name` / `url`) for the entity label and
        # the existing "fields" key for legacy cached shapes.
        fields = m.get("output") or m.get("fields") or {}
        dstr = _extract_field(fields, ("announcement_date", "date"))
        if not dstr:
            continue
        try:
            d = date.fromisoformat(str(dstr)[:10])
        except ValueError:
            continue
        ticker = (_extract_field(fields, ("company_name", "ticker"))
                  or m.get("name")
                  or "UNKNOWN")
        source_url = (_extract_field(fields, ("source_url", "url"))
                      or m.get("url")
                      or "")
        records.append(
            EventRecord(
                date=d,
                name=f"Earnings — {ticker}",
                kind="EARNINGS",
                severity="medium",
                source_url=source_url,
            )
        )
    return records


def refresh_holidays(
    *,
    client: ParallelClient | None = None,
) -> list[EventRecord]:
    """Extract NSE trading-holiday list for the current calendar year."""
    c = client or default_client()
    payload = c.extract(
        urls=["https://www.nseindia.com/resources/exchange-communication-holidays"],
        objective=(
            "Extract the list of NSE trading holidays for the current calendar year. "
            "Return each holiday's date (YYYY-MM-DD), name, and note the segment "
            "(equity / F&O)."
        ),
        ttl_sec=86_400 * 7,
    )
    results = payload.get("results") or []
    out: list[EventRecord] = []
    for r in results:
        excerpts = r.get("excerpts") or []
        # Parallel returns natural-language excerpts; we fall back on them as notes
        # and skip date-parsing here. A more elaborate parser is future work.
        for ex in excerpts:
            out.append(
                EventRecord(
                    date=date.today(),   # placeholder — upgraded in a future iteration
                    name="NSE holiday (see excerpt)",
                    kind="HOLIDAY",
                    severity="low",
                    source_url=r.get("url", ""),
                    notes=ex if isinstance(ex, str) else None,
                )
            )
    return out


def refresh_all(
    *,
    horizon_days: int = 90,
    include_earnings: bool = True,
    include_holidays: bool = False,
    client: ParallelClient | None = None,
    today: date | None = None,
) -> pd.DataFrame:
    """One-shot refresh: macro + earnings (+ holidays). Writes events.parquet."""
    c = client or default_client()
    t = today or date.today()

    all_events: list[EventRecord] = []
    try:
        all_events.extend(refresh_macro_events(horizon_days, client=c, today=t))
    except ParallelOfflineMiss:
        # Offline with no cache — surface empty but don't crash.
        pass
    if include_earnings:
        try:
            all_events.extend(refresh_earnings(horizon_days, client=c, today=t))
        except ParallelOfflineMiss:
            pass
    if include_holidays:
        try:
            all_events.extend(refresh_holidays(client=c))
        except ParallelOfflineMiss:
            pass

    df = _to_dataframe(all_events)
    df = df.sort_values("date").drop_duplicates(subset=["date", "name"]).reset_index(drop=True)
    df.to_parquet(EVENTS_PATH, index=False)
    return df


# ── Helpers ─────────────────────────────────────────────────────────────────


def _to_dataframe(events: Iterable[EventRecord]) -> pd.DataFrame:
    rows = [e.model_dump() for e in events]
    if not rows:
        return pd.DataFrame(columns=["date", "name", "kind", "severity", "source_url", "notes"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _extract_field(fields: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = fields.get(k)
        if v:
            return str(v)
    return None


def _coerce(r: dict) -> dict:
    """Coerce a parquet row back into an EventRecord-compatible dict."""
    out = dict(r)
    if isinstance(out.get("date"), pd.Timestamp):
        out["date"] = out["date"].date()
    elif isinstance(out.get("date"), datetime):
        out["date"] = out["date"].date()
    for k in ("kind", "severity", "name", "source_url"):
        if out.get(k) is None:
            out[k] = "OTHER" if k == "kind" else ("low" if k == "severity" else "")
    return out
