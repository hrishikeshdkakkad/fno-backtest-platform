"""Macro narrative, FII/DII flow, news snapshot — Parallel-backed enrichment.

The TUI's "macro brief" panel is the rationale layer on top of the numbers:
a human-readable, cited summary that tells the trader *why* the grade is
what it is. Implemented via Parallel Task API with a Pydantic schema so we
get structured fields (rate_outlook, flow_regime, earnings_tone, summary,
citations) rather than free-text we'd have to parse.

Gated on cost:
  • `macro_brief()`           processor="core",  TTL 30 min
  • `pre_trade_deep_brief()`  processor="ultra", only on A+/A grades
  • `fii_dii_flow()`          Extract API, TTL 24 h
  • `news_snapshot()`         Search API, TTL 15 min
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
from pydantic import BaseModel, Field

from .config import DATA_DIR
from .parallel_client import ParallelClient, ParallelOfflineMiss, default_client

BRIEF_PATH: Path = DATA_DIR / "macro_brief.json"
FII_DII_PATH: Path = DATA_DIR / "fii_dii_flow.parquet"


# ── Pydantic schemas ────────────────────────────────────────────────────────


class MacroBrief(BaseModel):
    """Schema returned by Parallel Task API.

    NOTE: `generated_at` is typed as `str` (not `datetime`) because Parallel's
    JSON-Schema validator rejects the `format: date-time` keyword Pydantic
    emits for datetime fields. We set the ISO string client-side in the
    save path.
    """
    summary: str = Field(description="A 3-sentence briefing: rate-cycle tone, flow regime, earnings-season tone.")
    rate_outlook: str = Field(description="One sentence on near-term RBI / Fed posture.")
    flow_regime: str = Field(description="One sentence on recent FII / DII cash-market behaviour.")
    earnings_tone: str = Field(description="One sentence on the current quarterly-results season.")
    citations: list[str] = Field(default_factory=list, description="Authoritative URLs that support the summary.")
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO-8601 timestamp; set client-side, not expected from the model.",
    )


class DeepBrief(BaseModel):
    headline: str
    scenario_bull: str = Field(description="What has to be true for NIFTY to drift higher into expiry.")
    scenario_base: str = Field(description="Most-likely scenario given current data.")
    scenario_bear: str = Field(description="Biggest-loss path for a short-put seller.")
    key_levels: list[float] = Field(default_factory=list, description="Support / resistance prices to watch.")
    citations: list[str] = Field(default_factory=list)
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO-8601 timestamp; set client-side, not expected from the model.",
    )


class FlowRow(BaseModel):
    date: date
    fii_cash: float | None = None     # ₹ crore, +ve = net buying
    dii_cash: float | None = None
    fii_fno: float | None = None
    dii_fno: float | None = None
    source_url: str = ""


class FlowBundle(BaseModel):
    rows: list[FlowRow] = Field(default_factory=list)
    lookback_days: int = 30


# ── Public functions ────────────────────────────────────────────────────────


def macro_brief(
    *,
    snap: dict[str, Any] | None = None,
    client: ParallelClient | None = None,
) -> MacroBrief:
    """Generate the daily 3-sentence cited macro brief.

    `snap` is optional context (today's regime grade, VIX, spot) that gets
    woven into the prompt so the brief reflects the current state. If the
    Parallel call fails / offline, returns the last cached brief from disk
    (raising ParallelOfflineMiss only if even the disk copy is absent).
    """
    c = client or default_client()
    context_blob = ""
    if snap:
        context_blob = (
            f" Current regime grade: {snap.get('grade')}. "
            f"NIFTY spot: {snap.get('spot')}. "
            f"India VIX: {snap.get('vix')}. "
            f"ATM IV: {snap.get('atm_iv')}. "
            f"IV-RV spread: {snap.get('iv_minus_rv')} pp."
        )
    prompt = (
        "You are briefing a retail Indian NIFTY options seller who runs 0.30-delta "
        "put credit spreads. Summarise, in three sentences, (a) the near-term "
        "rate-cycle / RBI tone, (b) the FII & DII flow regime over the last "
        "2 weeks, and (c) the earnings-season tone for NIFTY-50 constituents. "
        "Cite authoritative URLs (RBI, NSE, Bloomberg, Reuters, Moneycontrol, "
        "ET Markets). Be factual; do not give trade advice." + context_blob
    )
    try:
        brief = c.task(prompt, output_model=MacroBrief, processor="core", ttl_sec=1800)
    except ParallelOfflineMiss:
        cached = _load_brief()
        if cached is None:
            raise
        return cached
    _save_brief(brief)
    return brief


def pre_trade_deep_brief(
    snap: dict[str, Any],
    *,
    client: ParallelClient | None = None,
) -> DeepBrief:
    """Ultra-tier deep research for A+/A grades only — caller is expected to gate this."""
    c = client or default_client()
    prompt = (
        "Write a pre-trade research brief for a NIFTY 0.30-delta put-credit-spread "
        f"entry with {snap.get('dte')} DTE. Current spot {snap.get('spot')}, "
        f"VIX {snap.get('vix')}, ATM IV {snap.get('atm_iv')}, IV Rank {snap.get('iv_rank')}, "
        f"skew {snap.get('skew_vol_pts')} vp, regime grade {snap.get('grade')}. "
        "Cover: headline call, bull / base / bear scenarios, key support/resistance "
        "levels for NIFTY, and cite at least 3 authoritative sources."
    )
    return c.task(prompt, output_model=DeepBrief, processor="ultra", ttl_sec=14_400)


def fii_dii_flow(
    *,
    lookback_days: int = 30,
    client: ParallelClient | None = None,
) -> pd.DataFrame:
    """FII/DII daily cash & F&O flow for the last `lookback_days`.

    Uses Parallel Extract on the NSE/Moneycontrol FII-DII endpoints.
    Writes `data/nfo/fii_dii_flow.parquet` and also returns it.
    """
    c = client or default_client()
    prompt_blob = FlowBundle(rows=[], lookback_days=lookback_days).model_dump_json()
    prompt = (
        f"Return the daily Foreign Institutional Investor (FII) and Domestic "
        f"Institutional Investor (DII) cash-market and F&O net-flow figures "
        f"(in ₹ crore, positive = net buying) for the Indian equity market "
        f"over the last {lookback_days} calendar days. Format each row with "
        f"an ISO date. Template:\n{prompt_blob}"
    )
    try:
        bundle = c.task(prompt, output_model=FlowBundle, processor="core", ttl_sec=86_400)
    except ParallelOfflineMiss:
        if FII_DII_PATH.exists():
            return pd.read_parquet(FII_DII_PATH)
        raise
    df = pd.DataFrame([r.model_dump() for r in bundle.rows])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    df.to_parquet(FII_DII_PATH, index=False)
    return df


def news_snapshot(
    objective: str = "Market-moving NIFTY / Indian-equity headlines from the last 24 hours.",
    queries: Sequence[str] | None = None,
    *,
    client: ParallelClient | None = None,
) -> dict[str, Any]:
    c = client or default_client()
    return c.search(
        objective=objective,
        queries=list(queries or ["NIFTY today", "India equity market news", "FII DII flow today"]),
        processor="base",
        mode="one-shot",
        max_results=8,
        ttl_sec=900,
    )


# ── Persistence ─────────────────────────────────────────────────────────────


def _save_brief(brief: MacroBrief) -> None:
    payload = brief.model_dump(mode="json")
    BRIEF_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_brief() -> MacroBrief | None:
    if not BRIEF_PATH.exists():
        return None
    try:
        return MacroBrief.model_validate_json(BRIEF_PATH.read_text(encoding="utf-8"))
    except Exception:   # corrupt / schema drift → treat as missing
        return None


def latest_brief() -> MacroBrief | None:
    """Read the persisted brief without calling Parallel."""
    return _load_brief()
