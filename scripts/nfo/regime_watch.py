"""Regime watcher — pings when NIFTY credit-spread conditions shift to A+.

Output is verbose by design: every signal explains what it measures, what
the current value is, what the threshold is, what the gap looks like, and
*why* the threshold exists. Use --brief for a terse one-line status if you
later want to run this from cron.

Dynamic behaviour:
  - Entry date = the day the script is run (or next NSE trading day if
    weekend/holiday). Nothing is hardcoded.
  - Target monthly expiry = the monthly whose DTE is closest to 35
    (classic premium-seller's sweet spot); this changes automatically
    as the calendar advances.
  - Spread proposal uses Δ ≈ 0.30 short, width = 100 points — matching
    the best config from our 2024–2025 backtest.

Run manually:
    .venv/bin/python scripts/nfo/regime_watch.py

Show last 20 snapshots from history:
    .venv/bin/python scripts/nfo/regime_watch.py --history

Loop during market hours:
    .venv/bin/python scripts/nfo/regime_watch.py --loop 30

Launch the live responsive TUI (auto-refreshes during market hours):
    .venv/bin/python scripts/nfo/regime_watch.py --tui
    .venv/bin/python scripts/nfo/regime_watch.py --tui --refresh 15
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from nfo import bsm, calendar_nfo, calibrate, signals as sig_mod, universe
from nfo.client import DhanClient
from nfo.config import DATA_DIR, RESULTS_DIR

# Optional Parallel-backed enrichment — imports lazily so the TUI still works
# when parallel-web isn't installed or PARALLEL_API_KEY is unset.
try:
    from nfo import enrich, events
    from nfo.parallel_client import ParallelOfflineMiss
    _HAS_PARALLEL = True
except ImportError:   # pragma: no cover
    enrich = None        # type: ignore[assignment]
    events = None        # type: ignore[assignment]
    ParallelOfflineMiss = Exception   # type: ignore[assignment,misc]
    _HAS_PARALLEL = False

# ── Regime thresholds (defaults; overridden by tuned JSON if present) ────────
# India VIX 2019-2025 empirical distribution: median ~13, 70th pct ~14-15,
# 90th pct ~18, 95th pct ~22. 22 is CBOE-VIX territory; on India VIX it's a
# tail event (~1-2% of days) which made the all-8 gate structurally
# unreachable. See docs/india-fno-nuances.md §4 + §10.
VIX_RICH = 15.0            # fear-is-paying absolute threshold (India 70th pct)
VIX_PCT_RICH = 0.70        # VIX ≥ 70th percentile of 3-mo range
IV_RV_SPREAD_RICH = 0.0    # IV - RV ≥ this pp (rich if ≥ 0; ideal ≥ 3pp)
PULLBACK_PCT = 2.0         # spot ≥ 2% off 10-day high
# New tier-1 gates — initial defaults, re-tuned by calibrate.grid_search.
IV_RANK_RICH = 0.60        # IV Rank ≥ 60th of 12-mo range
SKEW_RICH_MAX = 6.0        # 25Δ put-call skew below 6 vol-pts (high skew ⇒ crash-fear)
TREND_MIN_SCORE = 2        # trend filter: at least 2 of 3 votes up
EVENT_SEVERITY_MAX = "medium"   # skip when severity == "high"


def _load_tuned_thresholds() -> None:
    """Override the module-level default thresholds with calibrated values,
    if `results/nfo/tuned_thresholds.json` exists. Silent no-op otherwise."""
    tuned = calibrate.load_tuned_thresholds()
    if not tuned or not tuned.get("best"):
        return
    best = tuned["best"]
    global VIX_RICH, VIX_PCT_RICH, IV_RV_SPREAD_RICH, PULLBACK_PCT
    try:
        VIX_RICH = float(best.get("vix_rich", VIX_RICH))
        VIX_PCT_RICH = float(best.get("vix_pct_rich", VIX_PCT_RICH))
        IV_RV_SPREAD_RICH = float(best.get("iv_rv_rich", IV_RV_SPREAD_RICH))
        # `pullback_atr` is in ATR units; the legacy `PULLBACK_PCT` is in %.
        # Keep both: the legacy signal uses pct, the new ATR signal uses units.
    except (TypeError, ValueError):
        pass


_load_tuned_thresholds()


# ── Spread proposal defaults (match best backtest config) ────────────────────
TARGET_DELTA = 0.30
SPREAD_WIDTH = 100
LOT_NIFTY = 65
MARGIN_MULT = 1.5

IST_TZ = timezone(timedelta(hours=5, minutes=30))
HISTORY_PATH = DATA_DIR / "regime_history.parquet"
# 7-level ladder to accommodate 8 signals. Higher number = richer regime.
GRADE_RANK = {"A++": 6, "A+": 5, "A": 4, "A-": 3, "B+": 2, "B": 1, "B-": 0}


# ─── Data classes ────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Signal:
    name: str
    current: float
    target: float
    compare: str           # ">", ">=", "<", "<="
    passed: bool
    unit: str
    why: str
    gap_explainer: str     # human-readable gap ("need +4.79 points", etc.)


@dataclass(slots=True)
class SpreadCandidate:
    expiry: date
    dte: int
    short_strike: float
    short_mid: float
    short_delta: float
    short_iv: float
    long_strike: float
    long_mid: float
    credit: float          # per share
    max_loss: float        # per share
    break_even: float
    credit_lot: float
    max_loss_lot: float
    bp: float
    roi_if_held: float
    pop_rv: float          # prob-OTM at expiry under σ = rv_30 (N(d₂))
    pop_iv: float          # prob-OTM at expiry under σ = short-strike IV (N(d₂))
    # v2 additions — strike-specific IV and empirical calibration.
    short_strike_iv: float = 0.0       # IV at the actual short strike (vs ATM IV)
    empirical_pop: float = float("nan")
    empirical_n: int = 0


@dataclass(slots=True)
class RegimeSnap:
    when: datetime
    entry_date: date
    expiry: date
    dte: int
    spot: float
    vix: float
    vix_pct_3mo: float
    vix_range_lo: float
    vix_range_hi: float
    atm_iv: float
    rv_30d: float
    rv_60d: float
    high_10d: float
    low_10d: float
    pullback_pct: float
    signals: list[Signal]
    spread: SpreadCandidate | None
    score: int
    grade: str
    # v2 additions — new derived signals & enrichment context.
    iv_rank_12mo: float = float("nan")
    iv_pct_12mo: float = float("nan")
    atr14: float = float("nan")
    pullback_atr: float = float("nan")
    trend_score: int = 0
    trend_up: bool = False
    skew_25d: float = float("nan")         # put_iv − call_iv (vol-pts)
    term_slope: float = float("nan")
    event_severity: str = "low"            # low | medium | high
    upcoming_events: list[dict[str, Any]] = field(default_factory=list)
    macro_brief_summary: str = ""
    macro_brief_generated_at: str = ""
    # v3 additions — entry-timing technical indicators (orthogonal to grade).
    # 0-100 composite; grade ∈ {Strong, Good, Neutral, Weak, Avoid, Unknown}.
    timing_score: float = float("nan")
    timing_grade: str = "Unknown"
    bb_z: float = float("nan")
    bb_squeeze: bool = False
    bb_bandwidth: float = float("nan")
    macd_histogram: float = float("nan")
    macd_state: str = "unknown"
    stoch_k: float = float("nan")
    stoch_d: float = float("nan")
    stoch_state: str = "unknown"
    timing_reasoning: list[str] = field(default_factory=list)
    # V3 gate — optimal filter from 2026-04 iterative redesign backtest.
    # Read-only advisory; NOT used to gate the 8-signal grade or auto-trade.
    # `v3_passed` is True iff ALL of:
    #   IV-RV pass  AND  trend pass  AND  V3-event pass
    #   (first 10 days of cycle, RBI/FOMC/Budget only — CPI demoted)
    #   AND at least one of (VIX-abs, VIX-%ile, IV-Rank 12m) pass
    v3_passed: bool = False
    v3_event_severity: str = "low"    # severity under V3's rule (separate from s8)
    v3_reasoning: list[str] = field(default_factory=list)
    # Calendar-structure timing score (Shaikh-Padhi; see signals.py):
    #   -1 on Monday, +1 on Thursday-after-expiry, 0 otherwise.
    # Applied as a soft adjustment to the grade (score+dow clamped to 0..8);
    # the raw 8-signal `score` above is unchanged so `score/8` display still
    # makes sense.
    dow_score: int = 0


# ─── Helpers ─────────────────────────────────────────────────────────────────


_GRADE_MAP = {
    0: "B-", 1: "B-", 2: "B", 3: "B+",
    4: "A-", 5: "A-", 6: "A", 7: "A+", 8: "A++",
}


def _grade(score: int, dow_score: int = 0) -> str:
    """Map (signal-sum ± DoW adjustment) → 7-level grade ladder.

    `dow_score` is the Shaikh-Padhi timing adjustment from
    `signals.day_of_week_score()`: -1 on Monday (selling into adverse VIX
    drift), +1 on Thursday-after-expiry (post-crush). Applied as a soft
    grade modifier, clamped into [0, 8] so the ladder domain stays valid.
    """
    effective = max(0, min(8, int(score) + int(dow_score)))
    return _GRADE_MAP.get(effective, "B-")


def _today_ist() -> date:
    """Always use IST to pick 'today' so a non-IST host doesn't derail expiry math."""
    return datetime.now(IST_TZ).date()


def _next_trading_day_on_or_after(target: date, spot_df: pd.DataFrame) -> date:
    """Snap `target` forward to the first date present in the daily-bar frame.

    If `target` is past the last available bar (weekend / holiday / running
    after-hours), fall back to advancing weekdays. This is approximate — NSE
    holidays that don't fall on a weekend will be missed — but the regime
    numbers don't depend on which exact day trading resumes.
    """
    dates = spot_df["date"].dt.date.tolist()
    for d in dates:
        if d >= target:
            return d
    probe = target
    while probe.weekday() >= 5:
        probe += timedelta(days=1)
    return probe


def _realized_vol(closes: list[float], window: int) -> float:
    if len(closes) < window + 1:
        return 0.0
    tail = closes[-(window + 1):]
    rets = [math.log(tail[i] / tail[i - 1]) for i in range(1, len(tail))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1) if len(rets) > 1 else 0.0
    return math.sqrt(var) * math.sqrt(252) * 100


def _vix_series_with_live(vix_series: list[float], vix_live: float | None) -> list[float]:
    """Return a VIX series that treats today's intraday print as the last close.

    Dhan's daily `chart_historical` endpoint lags by one session during market
    hours (today's bar publishes only after close). The live intraday endpoint
    gives us today's running VIX. For signals that key off the LAST element of
    the series (iv_rank, iv_percentile, term_structure EMAs), we must append
    the live print — otherwise those numbers reflect *yesterday's* close even
    though the displayed `vix` and the percentile query use today's live value.

    We deliberately do not dedup: `vix_series` returns daily closes only, so
    during market hours the two never coincide. Outside market hours, or when
    Dhan's intraday endpoint is empty, `vix_live` is None and the series is
    passed through unchanged.
    """
    if vix_live is None or not math.isfinite(vix_live):
        return list(vix_series)
    return list(vix_series) + [float(vix_live)]


def _vix_percentile(today: float, history: list[float]) -> float:
    """Empirical percentile rank — fraction of historical observations ≤ today.

    This is the true CDF, not the min/max range position. On a skewed series
    (VIX spikes are right-tailed), the two disagree materially, and the rank
    version matches what traders mean by '70th percentile'.
    """
    if not history:
        return 0.0
    return sum(1 for x in history if x <= today) / len(history)


def _vix_value_at_percentile(history: list[float], pct: float) -> float:
    """Return the historical VIX value at a given percentile (for gap math).

    Inverts the ECDF defined by `_vix_percentile`: the smallest sorted value v
    for which `#{x ≤ v} / n ≥ pct`. That is the `ceil(pct·n) − 1`th index in the
    sorted array, clamped to [0, n−1]. Using `int(pct·n)` would be off by one
    whenever `pct·n` is a positive integer — e.g. n=10, pct=0.7 would pick the
    8th-smallest value when the ECDF first reaches 0.7 at the 7th.
    """
    if not history:
        return 0.0
    sorted_h = sorted(history)
    n = len(sorted_h)
    idx = max(0, min(math.ceil(pct * n) - 1, n - 1))
    return sorted_h[idx]


def _target_monthly_expiry(expiries_iso: list[str], today: date, target_dte: int = 35) -> str | None:
    """Pick the monthly (last-of-month) expiry whose DTE is closest to target.

    Rejects DTE < 20 (too close, gamma danger) and DTE > target_dte × 2 (too far
    — we'd rather wait for the next cycle).
    """
    bucket: dict[tuple[int, int], date] = {}
    for e in expiries_iso:
        d = date.fromisoformat(e)
        dte = (d - today).days
        if dte < 20 or dte > target_dte * 2:
            continue
        ym = (d.year, d.month)
        bucket[ym] = max(bucket.get(ym, d), d)
    if not bucket:
        return None
    best = min(bucket.values(),
               key=lambda d: (abs((d - today).days - target_dte), -(d - today).days))
    return best.isoformat()


# ─── API pulls ───────────────────────────────────────────────────────────────


def _is_offline() -> bool:
    """True when `--no-parallel` (or `PARALLEL_OFFLINE=1`) was set.

    In offline mode the evaluator skips every live Dhan call and reads the
    most recent `data/nfo/index/{NIFTY,VIX}_*.parquet` instead. Intended
    for smoke-testing regime calculations without a network round trip —
    the live option chain is unavailable, so no candidate spread is built
    and chain-dependent signals (skew, ATM IV) return NaN/0.
    """
    return os.getenv("PARALLEL_OFFLINE", "").strip().lower() in ("1", "true", "yes")


def _latest_index_parquet(prefix: str) -> Path | None:
    """Pick the newest cached `data/nfo/index/{prefix}_*.parquet` by end-date.

    Filenames are `{UNDER}_{FROM}_{TO}.parquet`; we parse the trailing TO
    component to find the most recent cache rather than trusting mtime.
    Returns None if no cache exists for this prefix.
    """
    candidates: list[tuple[str, Path]] = []
    for p in DATA_DIR.glob(f"index/{prefix}_*.parquet"):
        stem = p.stem  # e.g. "NIFTY_2023-12-15_2026-01-10"
        parts = stem.rsplit("_", 1)
        if len(parts) == 2:
            candidates.append((parts[-1], p))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1]


def _load_spot(client: DhanClient | None, under, today: date) -> pd.DataFrame:
    from_d = (today - timedelta(days=120)).isoformat()
    to_d = today.isoformat()
    if _is_offline() or client is None:
        path = _latest_index_parquet(under.name)
        if path is None:
            raise RuntimeError(
                f"offline mode: no cached {under.name}_*.parquet under {DATA_DIR / 'index'}"
            )
        df = pd.read_parquet(path)
        df["date"] = pd.to_datetime(df["date"])
        cutoff = pd.Timestamp(today) - pd.Timedelta(days=120)
        df = df[(df["date"] >= cutoff) & (df["date"] <= pd.Timestamp(today))]
        if df.empty:
            raise RuntimeError(
                f"offline mode: cached {under.name} parquet has no rows in "
                f"[{cutoff.date()}, {today}] — refresh via refresh_vix_cache.py"
            )
        return df[["date", "open", "high", "low", "close"]].reset_index(drop=True)
    hist = client.chart_historical(
        exchange_segment=under.underlying_seg, instrument="INDEX",
        security_id=under.security_id, from_date=from_d, to_date=to_d, oi=False,
    )
    if not hist.get("close"):
        raise RuntimeError("empty NIFTY history — market holiday?")
    ts = pd.to_datetime(hist["timestamp"], unit="s", utc=True).tz_convert(IST_TZ)
    return pd.DataFrame({
        "date": ts.normalize().tz_localize(None),
        "open": hist["open"], "high": hist["high"],
        "low": hist["low"], "close": hist["close"],
    })


def _load_vix(client: DhanClient | None, today: date) -> list[float]:
    from_d = (today - timedelta(days=120)).isoformat()
    to_d = today.isoformat()
    if _is_offline() or client is None:
        path = _latest_index_parquet("VIX")
        if path is None:
            raise RuntimeError(
                f"offline mode: no cached VIX_*.parquet under {DATA_DIR / 'index'}"
            )
        df = pd.read_parquet(path)
        df["date"] = pd.to_datetime(df["date"])
        cutoff = pd.Timestamp(today) - pd.Timedelta(days=120)
        df = df[(df["date"] >= cutoff) & (df["date"] <= pd.Timestamp(today))]
        return [float(v) for v in df["close"].tolist()]
    hist = client.chart_historical(
        exchange_segment="IDX_I", instrument="INDEX", security_id=21,
        from_date=from_d, to_date=to_d, oi=False,
    )
    return hist.get("close", [])


def _load_today_intraday(client: DhanClient, under, today: date) -> dict | None:
    """Return today's 5-min intraday bars for the underlying index.

    Dhan's daily `chart_historical` endpoint lags by one session during market
    hours — today's bar isn't published until after close. When the market is
    live we still want today's true running high/low and the latest print, so
    we fetch 5-min intraday bars separately.

    Returns `{"highs": [...], "lows": [...], "closes": [...]}` or None if
    empty (weekend, pre-market, or Dhan gap).
    """
    try:
        resp = client.chart_intraday(
            exchange_segment=under.underlying_seg,
            instrument="INDEX",
            security_id=under.security_id,
            interval=5,
            from_date=f"{today.isoformat()} 09:15:00",
            to_date=f"{today.isoformat()} 23:59:00",
            oi=False,
        )
    except Exception:
        return None
    if not resp or not resp.get("close"):
        return None
    return {"highs": resp["high"], "lows": resp["low"], "closes": resp["close"]}


def _load_vix_intraday_today(client: DhanClient, today: date) -> float | None:
    """Return today's latest intraday India VIX print, or None if unavailable."""
    try:
        resp = client.chart_intraday(
            exchange_segment="IDX_I",
            instrument="INDEX",
            security_id=21,
            interval=5,
            from_date=f"{today.isoformat()} 09:15:00",
            to_date=f"{today.isoformat()} 23:59:00",
            oi=False,
        )
    except Exception:
        return None
    closes = (resp or {}).get("close") or []
    if not closes:
        return None
    return float(closes[-1])


def _load_chain(
    client: DhanClient, under, expiry: str
) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """Live option chain with Greeks. Returns (puts_df, calls_df, live_spot).

    Dhan's `optionchain` response carries both puts (`pe`) and calls (`ce`)
    per strike in a single payload, so we build both DataFrames from the
    same response — no extra API call needed. This gives the skew signal
    (25Δ put IV − 25Δ call IV) real data to work with and keeps puts-only
    consumers (candidate builder, ATM-IV lookup) unchanged.

    Live spot comes from the response's `data.last_price` — the freshest
    NIFTY value, which avoids mixing stale daily-close spot with an intraday
    chain snapshot (would misalign ATM selection).
    """
    resp = client.option_chain(under.security_id, under.underlying_seg, expiry)
    data = resp.get("data") or {}
    live_spot = float(data.get("last_price") or 0.0)
    oc = data.get("oc") or {}
    put_rows: list[dict] = []
    call_rows: list[dict] = []
    for k_str, legs in oc.items():
        strike = float(k_str)
        for leg_key, dst in (("pe", put_rows), ("ce", call_rows)):
            leg = legs.get(leg_key) or {}
            if not leg:
                continue
            gr = leg.get("greeks") or {}
            dst.append({
                "strike": strike,
                "bid": leg.get("top_bid_price"),
                "ask": leg.get("top_ask_price"),
                "last": leg.get("last_price"),
                "iv": leg.get("implied_volatility"),
                "delta": gr.get("delta"),
                "oi": leg.get("oi"),
            })

    def _finalise(rows: list[dict]) -> pd.DataFrame:
        # Guard against empty payloads (pre-market, expiry roll, Dhan gaps);
        # return a typed-empty frame so callers can degrade rather than
        # crash on sort/indexing. `close` is aliased from `mid` so downstream
        # helpers (signals.skew_25d / strike_iv) that expect `close` find it.
        cols = ["strike", "bid", "ask", "last", "iv", "delta", "oi", "mid", "close"]
        if not rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)
        for c in ("bid", "ask", "last", "iv", "delta"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        have_quote = (df["bid"] > 0) & (df["ask"] > 0)
        df["mid"] = (df["bid"] + df["ask"]) / 2.0
        df["mid"] = df["mid"].where(have_quote, df["last"])
        df["close"] = df["mid"]
        return df

    return _finalise(put_rows), _finalise(call_rows), live_spot


# ─── Signal computation ──────────────────────────────────────────────────────


def _compute_signals(snap_vals: dict) -> list[Signal]:
    vix = snap_vals["vix"]
    pct = snap_vals["vix_pct_3mo"]
    iv = snap_vals["atm_iv"]
    rv = snap_vals["rv_30d"]
    pullback = snap_vals["pullback_pct"]
    vix_lo = snap_vals["vix_range_lo"]
    vix_hi = snap_vals["vix_range_hi"]
    # The VIX we'd need to hit the 70th percentile — computed from the empirical
    # distribution, not min/max range position, so it matches the signal we grade.
    needed_vix_for_pct = snap_vals["vix_70pct_value"]

    s1 = Signal(
        name="VIX absolute level",
        current=vix, target=VIX_RICH, compare=">", passed=vix > VIX_RICH,
        unit="",
        why=(f"VIX above {VIX_RICH:.0f} signals elevated fear in India options. "
             "India VIX sits 11–18 most days, so the 70th-percentile anchor is "
             "~14–15 (not CBOE's 22). Each ₹ of capital at risk is better "
             "compensated when traders bid puts aggressively; below the anchor "
             "you're selling cheap insurance."),
        gap_explainer=(f"need VIX +{VIX_RICH - vix:.2f} from here"
                       if vix <= VIX_RICH else "✓ clears absolute threshold"),
    )
    s2 = Signal(
        name="VIX 3-month percentile",
        current=pct * 100, target=VIX_PCT_RICH * 100, compare=">=",
        passed=pct >= VIX_PCT_RICH, unit="%",
        why=("VIX being high VS its own recent history catches regime shifts the "
             "absolute threshold misses. A VIX of 20 looks benign, but if the "
             "3-month range is 10–21, a 20 print is a regime change screaming "
             "'vol is waking up'. 70th+ percentile is the historical sweet-spot "
             "for premium sellers."),
        gap_explainer=(f"need VIX ≥ {needed_vix_for_pct:.2f} to cross {VIX_PCT_RICH:.0%}-ile "
                       f"(3-mo range: {vix_lo:.2f} lo ↔ {vix_hi:.2f} hi)"
                       if pct < VIX_PCT_RICH else "✓ in upper tier of 3-mo range"),
    )
    s3 = Signal(
        name="Implied vs realized vol",
        current=iv - rv, target=IV_RV_SPREAD_RICH, compare=">=",
        passed=(iv - rv) >= IV_RV_SPREAD_RICH, unit="pp",
        why=("The single most important sanity check. IV is what you're PAID for "
             "(premium sold); RV is what the market is ACTUALLY delivering (the "
             "chop you'll have to survive). If IV < RV the market is paying you "
             "less than the risk you're taking — historically this is a "
             "negative-expected-value setup for premium sellers regardless of "
             "delta or strike."),
        gap_explainer=(f"IV at {iv:.1f}% vs RV at {rv:.1f}% → gap {iv - rv:+.1f}pp "
                       f"(need IV up {rv - iv:.1f}pp or RV down {rv - iv:.1f}pp)"
                       if (iv - rv) < IV_RV_SPREAD_RICH
                       else "✓ premium priced richer than realized chop"),
    )
    s4 = Signal(
        name="Recent pullback (% off 10d high)",
        current=pullback, target=PULLBACK_PCT, compare=">=",
        passed=pullback >= PULLBACK_PCT, unit="%",
        why=("Entering right after a rally chases momentum — the tape tends to "
             "mean-revert 2–4% off short-term tops before resuming. Waiting for a "
             "≥ 2% pullback means you're buying into a dip, not the peak, which "
             "adds margin of safety below your short strike."),
        gap_explainer=(f"spot is {pullback:.2f}% off 10d high; need {PULLBACK_PCT - pullback:.2f}pp more"
                       if pullback < PULLBACK_PCT else "✓ entering after a pullback"),
    )

    # v2 — four new signals (NaN-tolerant; if the input isn't computable yet,
    # the signal auto-fails rather than blowing up the grade).
    iv_rank = snap_vals.get("iv_rank_12mo", float("nan"))
    s5 = Signal(
        name="IV Rank 12-mo",
        current=(iv_rank * 100 if math.isfinite(iv_rank) else float("nan")),
        target=IV_RANK_RICH * 100, compare=">=",
        passed=(math.isfinite(iv_rank) and iv_rank >= IV_RANK_RICH),
        unit="%",
        why=("IV Rank ranks today's IV against its own 12-month [min,max] range. "
             "'Rich' by 3-mo VIX percentile doesn't imply rich by 12-mo range. "
             "60th+ on the 12-mo rank keeps us from selling into structural "
             "compression."),
        gap_explainer=(f"IV Rank {iv_rank*100:.0f}% (need ≥ {IV_RANK_RICH*100:.0f}%)"
                       if math.isfinite(iv_rank) and iv_rank < IV_RANK_RICH
                       else ("✓ upper portion of 12-mo range" if math.isfinite(iv_rank)
                             else "no history yet")),
    )
    trend_score = snap_vals.get("trend_score", 0)
    s6 = Signal(
        name="Trend filter (not in downtrend)",
        current=float(trend_score), target=float(TREND_MIN_SCORE), compare=">=",
        passed=trend_score >= TREND_MIN_SCORE,
        unit="/3",
        why=("Three-vote filter on EMA20/50 cross, ADX-14 > 20, and RSI-14 > 40. "
             "Short puts into a confirmed downtrend is how books blow up — this "
             "signal refuses entries during active breakdowns."),
        gap_explainer=(f"trend {trend_score}/3 (need {TREND_MIN_SCORE})"
                       if trend_score < TREND_MIN_SCORE else "✓ uptrend / sideways"),
    )
    skew = snap_vals.get("skew_25d", float("nan"))
    s7 = Signal(
        name="25Δ skew (put-call, vol-pts)",
        current=skew if math.isfinite(skew) else float("nan"),
        target=SKEW_RICH_MAX, compare="<=",
        passed=(math.isfinite(skew) and skew <= SKEW_RICH_MAX),
        unit="vp",
        why=("Risk-reversal — 25Δ put IV minus 25Δ call IV. When put wings spike "
             "(skew > 6 vp in NIFTY), institutions are paying up for crash "
             "insurance. Historically a terrible time to *sell* those same puts. "
             "Low skew (< 6 vp) means calm waters."),
        gap_explainer=(f"skew {skew:.1f} vp (need ≤ {SKEW_RICH_MAX:.1f})"
                       if math.isfinite(skew) and skew > SKEW_RICH_MAX
                       else ("✓ tame wings" if math.isfinite(skew) else "no chain data")),
    )
    ev_sev = snap_vals.get("event_severity", "low")
    ev_ok = ev_sev != "high"
    s8 = Signal(
        name="Event-risk (no macro event in DTE)",
        current=float({"low": 0.0, "medium": 1.0, "high": 2.0}.get(ev_sev, 0.0)),
        target=1.0, compare="<=",
        passed=ev_ok,
        unit="",
        why=("RBI MPC / Budget / FOMC / US CPI within the DTE window historically "
             "produces our biggest gap losses. Skip the cycle — you miss a few "
             "trades per year but dodge the worst max-losses."),
        gap_explainer=(f"event severity {ev_sev}" if not ev_ok else "✓ no high-severity event"),
    )
    return [s1, s2, s3, s4, s5, s6, s7, s8]


def _compute_v3_gate(
    *,
    entry_date: date,
    dte: int,
    atm_iv: float,
    rv_30: float,
    trend_score: int,
    vix: float,
    vix_pct_3mo: float,
    iv_rank_12mo: float,
    short_strike_iv: float = float("nan"),
) -> tuple[bool, str, list[str]]:
    """Optimal filter gate from 2026-04 iterative redesign backtest (V3).

    This is a STRUCTURAL rule — not a score threshold. Pass requires ALL of:

      1. IV - RV ≥ -2pp                 (rich-enough premium)
      2. Trend filter ≥ 2/3             (not in confirmed downtrend)
      3. V3 event check == "low"/"medium" only
         (no RBI/FOMC/Budget in first 10 days of cycle; CPI demoted)
      4. ≥ 1 of:
         VIX > 20          OR
         VIX 3-mo %ile ≥ 0.80  OR
         IV Rank 12-mo ≥ 0.60

    Returns (passed, v3_event_severity, reasoning_lines). This is DISPLAY-ONLY
    — the 8-signal grade still drives the existing UI. V3 shows up as an
    advisory badge next to the grade so the user can build intuition before
    paper-trading and promoting to live.
    """
    reasoning: list[str] = []

    # (1) IV-RV. Prefer strike-specific IV (short-leg IV) when available —
    # that's what actually prices the short-put exposure. Fall back to ATM
    # IV if the spread wasn't constructible yet.
    iv_for_signal = (short_strike_iv
                     if math.isfinite(short_strike_iv) and short_strike_iv > 0
                     else atm_iv)
    iv_source = ("short-strike" if math.isfinite(short_strike_iv) and short_strike_iv > 0
                 else "ATM")
    iv_rv_pass = False
    if math.isfinite(iv_for_signal) and math.isfinite(rv_30):
        iv_rv = iv_for_signal - rv_30
        iv_rv_pass = iv_rv >= IV_RV_SPREAD_RICH
        reasoning.append(
            f"IV-RV {iv_rv:+.1f}pp ≥ {IV_RV_SPREAD_RICH:+.1f} "
            f"({iv_source} IV={iv_for_signal:.1f}%): {'✓' if iv_rv_pass else '✗'}"
        )
    else:
        reasoning.append("IV-RV: unavailable ✗")

    # (2) Trend.
    trend_pass = trend_score >= 2
    reasoning.append(f"Trend {trend_score}/3 ≥ 2: {'✓' if trend_pass else '✗'}")

    # (3) V3 event rule — first 10 days, RBI/FOMC/Budget only.
    v3_sev = "low"
    event_pass = True
    if _HAS_PARALLEL and events is not None:
        try:
            flag = events.v3_event_risk_flag(entry_date, dte)
            v3_sev = flag.severity
            event_pass = v3_sev != "high"
            reasoning.append(
                f"V3-event[{v3_sev}, first 10d, RBI/FOMC/Budget only]: "
                f"{'✓' if event_pass else '✗'}"
            )
        except Exception as exc:
            reasoning.append(f"V3-event: error ({type(exc).__name__}) ✗")
            event_pass = False
    else:
        reasoning.append("V3-event: parallel module unavailable ✗")
        event_pass = False

    # (4) ≥ 1 of VIX / VIX-%ile / IV-rank.
    any_vol = (
        (math.isfinite(vix) and vix > VIX_RICH) or
        (math.isfinite(vix_pct_3mo) and vix_pct_3mo >= VIX_PCT_RICH) or
        (math.isfinite(iv_rank_12mo) and iv_rank_12mo >= IV_RANK_RICH)
    )
    reasoning.append(
        f"Any-vol (VIX>{VIX_RICH:.0f} or pct≥{VIX_PCT_RICH:.0%} or "
        f"IVr≥{IV_RANK_RICH:.0%}): {'✓' if any_vol else '✗'}"
    )

    passed = iv_rv_pass and trend_pass and event_pass and any_vol
    return passed, v3_sev, reasoning


# ─── Spread picker ───────────────────────────────────────────────────────────


def _build_candidate(chain: pd.DataFrame, spot: float, expiry: date, dte: int, rv_30: float) -> SpreadCandidate | None:
    # OTM strikes with a usable mid price AND a non-null delta.
    otm = chain[
        (chain.strike < spot)
        & chain.mid.notna() & (chain.mid > 0)
        & chain.delta.notna()
    ].copy()
    if otm.empty:
        return None
    otm["delta_err"] = (otm.delta.abs() - TARGET_DELTA).abs()
    short = otm.sort_values("delta_err").iloc[0]
    long_strike = short.strike - SPREAD_WIDTH
    long_row = chain[(chain.strike == long_strike) & chain.mid.notna() & (chain.mid > 0)]
    if long_row.empty:
        return None
    long = long_row.iloc[0]
    short_mid = float(short.mid); long_mid = float(long.mid)
    if not (math.isfinite(short_mid) and math.isfinite(long_mid)):
        return None
    credit = short_mid - long_mid
    if credit <= 0 or not math.isfinite(credit):
        return None
    max_loss = SPREAD_WIDTH - credit
    bp = max_loss * LOT_NIFTY * MARGIN_MULT
    break_even = float(short.strike) - credit
    # Short-strike IV sizes the market-implied POP (N(d₂) under σ = σ_IV). We
    # prefer the SHORT-STRIKE IV over ATM because deep-OTM puts trade richer
    # due to skew — ATM understates the vol the short leg is actually exposed
    # to. Falls back to rv_30 if the chain didn't supply an IV for the strike
    # we picked (rare, but possible on illiquid far-OTM legs).
    short_iv = float(short.iv) if pd.notna(short.iv) else 0.0
    sigma_iv = (short_iv if short_iv > 0 else rv_30) / 100.0
    years_to_expiry = dte / 365.0
    pop_iv = bsm.put_prob_otm(spot, float(short.strike), years_to_expiry, sigma_iv)

    # RV-based POP intentionally uses σ = rv_30 (the realized-vol regime over
    # the last 30 sessions), so the pop_iv-vs-pop_rv delta below means
    # "market-implied POP rich vs realized chop" — the whole point of the
    # comparison narrative downstream.
    sigma_rv = rv_30 / 100.0
    pop_rv = bsm.put_prob_otm(spot, float(short.strike), years_to_expiry, sigma_rv)
    # Empirical POP from the 2.5y bucket table — NaN if no table yet.
    try:
        emp = calibrate.lookup_empirical_pop(delta=float(short.delta), dte=int(dte))
    except Exception:
        emp = {"win_rate": float("nan"), "n": 0}
    return SpreadCandidate(
        expiry=expiry, dte=dte,
        short_strike=float(short.strike), short_mid=short_mid,
        short_delta=float(short.delta),
        short_iv=short_iv,
        long_strike=float(long_strike), long_mid=long_mid,
        credit=credit, max_loss=max_loss, break_even=break_even,
        credit_lot=credit * LOT_NIFTY, max_loss_lot=max_loss * LOT_NIFTY, bp=bp,
        roi_if_held=credit / (max_loss * MARGIN_MULT) * 100,
        pop_rv=pop_rv, pop_iv=pop_iv,
        short_strike_iv=short_iv,
        empirical_pop=float(emp.get("win_rate", float("nan"))),
        empirical_n=int(emp.get("n", 0)),
    )


# ─── Core evaluator ──────────────────────────────────────────────────────────


def evaluate() -> RegimeSnap:
    nifty = universe.get("NIFTY")
    today = _today_ist()

    if _is_offline():
        # No live Dhan calls; cached daily bars only. The cache may lag real
        # today (especially after hours or if refresh_vix_cache.py hasn't
        # run in a while), so treat the last cached bar as "today" rather
        # than wall-clock today — otherwise the expiry calendar can't snap
        # to a trading day that actually exists in the data.
        spot_df = _load_spot(None, nifty, today)
        vix_series = _load_vix(None, today)
        cached_today = spot_df["date"].iloc[-1].date()
        if cached_today < today:
            print(f"offline: wall-clock today is {today}, latest cached bar is "
                  f"{cached_today} — using cached date as effective today",
                  file=sys.stderr, flush=True)
            today = cached_today
        today_intra = None
        vix_live = None
        # Walk forward month-by-month for a target expiry strictly after
        # effective "today". Use the raw last-Tuesday/Thursday rule rather
        # than `calendar_nfo.monthly_expiry`, because the latter snaps the
        # expiry into spot_daily and in offline mode the cache typically
        # won't contain any future expiry date.
        expiry_cand = None
        y, m = today.year, today.month
        post_reform = date(2025, 4, 1)
        for _ in range(6):
            weekday = 1 if date(y, m, 1) >= post_reform else 3   # Tue post-reform, Thu before
            cand = calendar_nfo._last_weekday_of_month(y, m, weekday=weekday)
            if cand > today:
                expiry_cand = cand
                break
            m += 1
            if m > 12:
                m, y = 1, y + 1
        if expiry_cand is None:
            raise RuntimeError("offline mode: could not derive a monthly expiry from calendar")
        target_expiry = expiry_cand.isoformat()
        chain = pd.DataFrame(columns=["strike", "iv", "close", "mid", "delta"])
        call_chain = pd.DataFrame(columns=["strike", "iv", "close"])
        live_spot = 0.0
    else:
        with DhanClient() as client:
            spot_df = _load_spot(client, nifty, today)
            vix_series = _load_vix(client, today)
            # Intraday enrichment: today's 5-min bars for NIFTY and VIX so we
            # don't rely on stale daily-close data during market hours. Both
            # helpers safely return None outside market hours.
            today_intra = _load_today_intraday(client, nifty, today)
            vix_live = _load_vix_intraday_today(client, today)
            expiries = client.optionchain_expiry_list(nifty.security_id, nifty.underlying_seg)
            target_expiry = _target_monthly_expiry(expiries, today)
            if not target_expiry:
                raise RuntimeError("no monthly expiry in the 20–70 DTE window")
            chain, call_chain, live_spot = _load_chain(client, nifty, target_expiry)

    # Prefer the live chain's spot (data.last_price) — it's the freshest print
    # during market hours. Fall back to last daily close if the chain endpoint
    # returned 0 (pre-market or data gap).
    last_close = float(spot_df["close"].iloc[-1])
    spot = live_spot if live_spot > 0 else last_close

    # VIX: live intraday print when available; historical daily closes as the
    # reference distribution for percentile. Ranking today's live VIX against
    # the historical-daily distribution is the correct regime question.
    vix = vix_live if vix_live is not None else vix_series[-1]
    vix_lo, vix_hi = min(vix_series), max(vix_series)
    vix_pct = _vix_percentile(vix, vix_series)
    vix_70pct_value = _vix_value_at_percentile(vix_series, VIX_PCT_RICH)

    closes = spot_df["close"].tolist()
    highs = spot_df["high"].tolist()
    lows = spot_df["low"].tolist()
    rv_30 = _realized_vol(closes, 30)
    rv_60 = _realized_vol(closes, 60)
    # 10-day high/low must reflect today's intraday extremes when market is
    # open. Combine the last 9 complete daily bars with today's intraday range
    # so a new intraday high doesn't produce a negative pullback.
    if today_intra is not None:
        hi_10d = max(max(highs[-9:]) if len(highs) >= 9 else max(highs), max(today_intra["highs"]))
        lo_10d = min(min(lows[-9:]) if len(lows) >= 9 else min(lows), min(today_intra["lows"]))
    else:
        hi_10d = max(highs[-10:])
        lo_10d = min(lows[-10:])
    # Pullback floored at 0 — "new high" is a non-pullback, never negative.
    pullback = max(0.0, (hi_10d - spot) / hi_10d * 100)

    # ATM IV = strike closest to LIVE spot in the chain (not daily close),
    # so ATM selection stays coherent with the chain snapshot's own reference.
    atm_row = chain.iloc[(chain.strike - spot).abs().argsort()].head(1)
    atm_iv_raw = atm_row["iv"].iloc[0] if not atm_row.empty else None
    atm_iv = float(atm_iv_raw) if pd.notna(atm_iv_raw) else 0.0

    expiry_date = date.fromisoformat(target_expiry)
    entry_date = _next_trading_day_on_or_after(today, spot_df)
    dte = (expiry_date - entry_date).days
    spread = _build_candidate(chain, spot, expiry_date, dte, rv_30)

    # ── v2 enrichment ───────────────────────────────────────────────────
    # IV Rank / percentile over 12-mo of ATM IV proxy (VIX history is the
    # cleanest free-running ATM proxy we have). Needs ≥ 60 daily points or
    # returns NaN and the downstream signal auto-fails.
    #
    # Append today's live VIX print so s[-1] reflects *today* during market
    # hours, matching the displayed `vix` value above. Without this, iv_rank
    # and iv_pct rank yesterday's close instead, which disagrees with the
    # current VIX shown right next to them.
    vix_series_today = _vix_series_with_live(vix_series, vix_live)
    iv_rank_12mo = sig_mod.iv_rank(vix_series_today, lookback=252)
    iv_pct_12mo = sig_mod.iv_percentile(vix_series_today, lookback=252)

    # ATR + ATR-scaled pullback on the spot frame.
    atr_series = sig_mod.atr(spot_df, 14)
    atr14 = float(atr_series.iloc[-1]) if not atr_series.empty else float("nan")
    pullback_atr = sig_mod.pullback_atr_scaled(spot, hi_10d, atr14)

    # Trend filter.
    trend = sig_mod.trend_regime(spot_df)

    # Skew (25Δ put IV − 25Δ call IV). Both chains come from the same
    # option_chain response so this costs zero extra API calls. NaN only if
    # either side is empty (which flips the signal to "fail" cleanly).
    t_years_for_skew = max(dte / 365.0, 1e-4)
    skew_snap = sig_mod.skew_25d(
        chain, call_chain, spot=spot, years_to_expiry=t_years_for_skew,
    )
    skew_val = float(skew_snap.skew_vol_pts)

    # Term structure of VIX (fast-vs-slow EMA proxy). Use the live-augmented
    # series so the EMAs extend to today's print, not yesterday's close.
    ts = sig_mod.term_structure(vix_series_today, fast=5, slow=22)
    term_slope = ts.slope

    # Event calendar — non-blocking. If Parallel is down/offline, default low.
    upcoming: list[dict[str, Any]] = []
    event_severity = "low"
    if _HAS_PARALLEL and events is not None:
        try:
            ev_list = events.upcoming_events(entry_date, dte)
            upcoming = [e.model_dump(mode="json") for e in ev_list]
            event_severity = events.event_risk_flag(ev_list).severity
        except Exception:
            pass

    signals = _compute_signals({
        "vix": vix, "vix_pct_3mo": vix_pct, "atm_iv": atm_iv, "rv_30d": rv_30,
        "pullback_pct": pullback, "vix_range_lo": vix_lo, "vix_range_hi": vix_hi,
        "vix_70pct_value": vix_70pct_value,
        "iv_rank_12mo": iv_rank_12mo,
        "trend_score": trend.score,
        "skew_25d": skew_val,
        "event_severity": event_severity,
    })
    score = sum(1 for s in signals if s.passed)

    # DoW soft adjustment (Shaikh-Padhi seasonality). Collect the last two
    # monthly expiries so the Thursday-after-expiry bonus can fire.
    recent_expiries: list[date] = []
    for delta_months in (0, 1):
        y = today.year
        m = today.month - delta_months
        if m <= 0:
            m += 12
            y -= 1
        exp = calendar_nfo.monthly_expiry(nifty, y, m, spot_df)
        if exp is not None and exp <= entry_date:
            recent_expiries.append(exp)
    dow_score = sig_mod.day_of_week_score(entry_date, recent_expiries=recent_expiries)

    # Entry-timing technical indicators — Bollinger + MACD + Stochastic
    # combined into a 0-100 score. Orthogonal to the 8-signal grade: grade
    # answers "should I be in this market?", timing answers "is today the
    # day?". Pure math on the 120-day spot_df, no network calls.
    timing = sig_mod.entry_timing_score(spot_df)

    # V3 gate — optimal-filter read-only advisory from the redesign backtest.
    # Prefer the short-strike IV from the candidate when available; ATM IV
    # is a fallback only.
    short_iv = float(spread.short_strike_iv) if spread is not None else float("nan")
    v3_passed, v3_sev, v3_reasoning = _compute_v3_gate(
        entry_date=entry_date, dte=dte, atm_iv=atm_iv, rv_30=rv_30,
        trend_score=trend.score, vix=vix, vix_pct_3mo=vix_pct,
        iv_rank_12mo=iv_rank_12mo, short_strike_iv=short_iv,
    )

    # Macro brief — cached on disk; fetch only if online mode is enabled and
    # cache is stale. Never blocks the TUI on failure.
    brief_summary, brief_generated_at = "", ""
    if _HAS_PARALLEL and enrich is not None:
        try:
            latest = enrich.latest_brief()
            if latest is not None:
                brief_summary = latest.summary
                # generated_at is an ISO str (Parallel schema constraint).
                brief_generated_at = str(latest.generated_at)
        except Exception:
            pass

    return RegimeSnap(
        when=datetime.now(IST_TZ), entry_date=entry_date, expiry=expiry_date, dte=dte,
        spot=spot, vix=vix, vix_pct_3mo=vix_pct, vix_range_lo=vix_lo, vix_range_hi=vix_hi,
        atm_iv=atm_iv, rv_30d=rv_30, rv_60d=rv_60,
        high_10d=hi_10d, low_10d=lo_10d, pullback_pct=pullback,
        signals=signals, spread=spread, score=score,
        grade=_grade(score, dow_score), dow_score=dow_score,
        iv_rank_12mo=iv_rank_12mo, iv_pct_12mo=iv_pct_12mo,
        atr14=atr14, pullback_atr=pullback_atr,
        trend_score=trend.score, trend_up=trend.trending_up,
        skew_25d=skew_val, term_slope=term_slope,
        event_severity=event_severity, upcoming_events=upcoming,
        macro_brief_summary=brief_summary,
        macro_brief_generated_at=brief_generated_at,
        timing_score=timing.score, timing_grade=timing.grade,
        bb_z=timing.bb_z, bb_squeeze=timing.bb_squeeze,
        bb_bandwidth=timing.bb_bandwidth,
        macd_histogram=timing.macd_histogram, macd_state=timing.macd_state,
        stoch_k=timing.stoch_k, stoch_d=timing.stoch_d, stoch_state=timing.stoch_state,
        timing_reasoning=list(timing.reasoning),
        v3_passed=v3_passed, v3_event_severity=v3_sev, v3_reasoning=v3_reasoning,
    )


# ─── Persistence ─────────────────────────────────────────────────────────────


def _append_history(snap: RegimeSnap) -> pd.DataFrame:
    """Persist every scalar the dashboard displays so every variable can be
    diffed run-over-run. Old history rows written before this schema
    expansion will read back with NaN in the new columns — the delta
    formatter treats NaN as "first observation" and degrades gracefully.
    """
    sp = snap.spread
    by_name = {s.name: s for s in snap.signals}
    row = pd.DataFrame([{
        # Core identity
        "when": snap.when,
        "expiry": snap.expiry.isoformat(),
        "dte": snap.dte,
        # Regime numbers
        "spot": snap.spot,
        "vix": snap.vix,
        "vix_pct_3mo": snap.vix_pct_3mo,
        "vix_range_lo": snap.vix_range_lo,
        "vix_range_hi": snap.vix_range_hi,
        "atm_iv": snap.atm_iv,
        "rv_30d": snap.rv_30d,
        "rv_60d": snap.rv_60d,
        "high_10d": snap.high_10d,
        "low_10d": snap.low_10d,
        "pullback_pct": snap.pullback_pct,
        # Signal pass/fail (by name — order in `signals` is stable but we
        # dict-lookup defensively so a future rename doesn't corrupt history)
        "s_vix_high":   by_name.get("VIX absolute level").passed if "VIX absolute level" in by_name else None,
        "s_vix_pctile": by_name.get("VIX 3-month percentile").passed if "VIX 3-month percentile" in by_name else None,
        "s_iv_rich":    by_name.get("Implied vs realized vol").passed if "Implied vs realized vol" in by_name else None,
        "s_pullback":   by_name.get("Recent pullback (% off 10d high)").passed if "Recent pullback (% off 10d high)" in by_name else None,
        # v2 signals — persisted so delta/history views see their flips too.
        "s_iv_rank":    by_name.get("IV Rank 12-mo").passed if "IV Rank 12-mo" in by_name else None,
        "s_trend":      by_name.get("Trend filter (not in downtrend)").passed if "Trend filter (not in downtrend)" in by_name else None,
        "s_skew":       by_name.get("25Δ skew (put-call, vol-pts)").passed if "25Δ skew (put-call, vol-pts)" in by_name else None,
        "s_event":      by_name.get("Event-risk (no macro event in DTE)").passed if "Event-risk (no macro event in DTE)" in by_name else None,
        # v2 enrichment numerics — useful for history / diff views.
        "iv_rank_12mo": snap.iv_rank_12mo,
        "atr14":        snap.atr14,
        "pullback_atr": snap.pullback_atr,
        "trend_score":  snap.trend_score,
        "skew_25d":     snap.skew_25d,
        "term_slope":   snap.term_slope,
        "event_severity": snap.event_severity,
        # v3 entry-timing numerics.
        "timing_score": snap.timing_score,
        "timing_grade": snap.timing_grade,
        "bb_z":         snap.bb_z,
        "bb_squeeze":   snap.bb_squeeze,
        "bb_bandwidth": snap.bb_bandwidth,
        "macd_histogram": snap.macd_histogram,
        "macd_state":   snap.macd_state,
        "stoch_k":      snap.stoch_k,
        "stoch_d":      snap.stoch_d,
        "stoch_state":  snap.stoch_state,
        # V3 gate — backtest-picked optimal filter (read-only advisory).
        "v3_passed":       snap.v3_passed,
        "v3_event_severity": snap.v3_event_severity,
        # Score / grade
        "score": snap.score,
        "grade": snap.grade,
        # Spread (None when not constructible)
        "short_strike": sp.short_strike if sp else None,
        "long_strike":  sp.long_strike  if sp else None,
        "short_delta":  sp.short_delta  if sp else None,
        "short_iv":     sp.short_iv     if sp else None,
        "credit":       sp.credit       if sp else None,
        "credit_lot":   sp.credit_lot   if sp else None,
        "max_loss":     sp.max_loss     if sp else None,
        "max_loss_lot": sp.max_loss_lot if sp else None,
        "bp":           sp.bp           if sp else None,
        "break_even":   sp.break_even   if sp else None,
        # `pop_iv` replaced the legacy `pop_delta` field (see SpreadCandidate):
        # pre-fix rows stored 1 − |Δ|; post-fix rows store N(d₂) under σ = σ_IV.
        # Keep the old column readable by pandas concat but populate only the
        # new name on new rows — history analytics should key off `pop_iv`.
        "pop_iv":       sp.pop_iv       if sp else None,
        "pop_rv":       sp.pop_rv       if sp else None,
    }])
    prior = pd.read_parquet(HISTORY_PATH) if HISTORY_PATH.exists() else pd.DataFrame()
    combined = pd.concat([prior, row], ignore_index=True) if not prior.empty else row
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(HISTORY_PATH, index=False)
    return combined


def _prev_grade(history: pd.DataFrame) -> str | None:
    if len(history) < 2:
        return None
    return str(history["grade"].iloc[-2])


def _prior_row(history: pd.DataFrame) -> pd.Series | None:
    if len(history) < 2:
        return None
    return history.iloc[-2]


# ─── Delta formatting ────────────────────────────────────────────────────────


# "No change" threshold for floats (absolute). Anything smaller is rendered as
# "(no change)" to keep the diff readable — intraday runs inside market hours
# will see lots of exactly-equal daily-close fields.
_EPSILON = 1e-9


def _pull_prior(prior: pd.Series | None, key: str):
    if prior is None:
        return None
    if key not in prior.index:
        return None
    v = prior[key]
    if v is None:
        return None
    try:
        if isinstance(v, float) and math.isnan(v):
            return None
    except TypeError:
        pass
    return v


def _fmt_diff(label: str, prior, current, *, unit: str = "", precision: int = 2,
              prefix: str = "₹", use_prefix: bool = False, pad: int = 22) -> str:
    """Arrow-style diff for a single numeric field.

    Renders: '  label         : prior → current   ↑ (+diff)'

    Handles None/NaN prior as "first observation". Near-zero diffs render as
    "(no change)" to suppress noise from same-day repeat runs.
    """
    lab = f"  {label:<{pad}}"
    if current is None or (isinstance(current, float) and math.isnan(current)):
        return f"{lab}: (not available this run)"
    pfx = prefix if use_prefix else ""
    if prior is None:
        return f"{lab}: {pfx}{current:,.{precision}f}{unit}   (first observation)"
    try:
        diff = float(current) - float(prior)
    except (TypeError, ValueError):
        return f"{lab}: {prior} → {current}"
    prior_s = f"{pfx}{float(prior):,.{precision}f}{unit}"
    cur_s = f"{pfx}{float(current):,.{precision}f}{unit}"
    # Precision-aware threshold: if the diff rounds to zero at the display
    # precision, call it "no change" — otherwise we'd print ugly "+0.00" lines
    # for fields that didn't actually move at display resolution.
    display_eps = max(_EPSILON, 0.5 * (10 ** -precision))
    if abs(diff) < display_eps:
        return f"{lab}: {prior_s} → {cur_s}   (no change)"
    arrow = "↑" if diff > 0 else "↓"
    sign = "+" if diff > 0 else "−"
    return f"{lab}: {prior_s} → {cur_s}   {arrow} ({sign}{abs(diff):,.{precision}f}{unit})"


def _fmt_enum_diff(label: str, prior, current, *, pad: int = 22) -> str:
    lab = f"  {label:<{pad}}"
    if current is None:
        return f"{lab}: (not available this run)"
    if prior is None:
        return f"{lab}: {current}   (first observation)"
    if str(prior) == str(current):
        return f"{lab}: {prior} → {current}   (no change)"
    return f"{lab}: {prior} → {current}"


def _fmt_elapsed(then: datetime, now: datetime) -> str:
    delta = now - then
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h {mins % 60}m ago"
    days = hours // 24
    return f"{days}d {hours % 24}h ago"


# Maps the dashboard's human signal names (also used as Signal.name) to the
# parquet column storing the pass/fail boolean. Must stay in sync with
# `_append_history` — a missing mapping here means that signal's flips are
# invisible in the delta view, even though the score/grade moved.
_SIGNAL_COLS = {
    "VIX absolute level": "s_vix_high",
    "VIX 3-month percentile": "s_vix_pctile",
    "Implied vs realized vol": "s_iv_rich",
    "Recent pullback (% off 10d high)": "s_pullback",
    "IV Rank 12-mo": "s_iv_rank",
    "Trend filter (not in downtrend)": "s_trend",
    "25Δ skew (put-call, vol-pts)": "s_skew",
    "Event-risk (no macro event in DTE)": "s_event",
}


def _format_delta_section(snap: RegimeSnap, prior: pd.Series | None) -> str:
    """The 'Δ vs last run' block — inserted between 'what would flip' and 'trajectory'."""
    lines: list[str] = []
    a = lines.append
    if prior is None:
        a("  (first run — no prior comparison available)")
        return "\n".join(lines)

    prior_when = pd.to_datetime(prior["when"])
    # prior_when may or may not be tz-aware; normalise both to IST for the diff.
    if prior_when.tzinfo is None:
        prior_when = prior_when.tz_localize(IST_TZ)
    else:
        prior_when = prior_when.tz_convert(IST_TZ)
    elapsed = _fmt_elapsed(prior_when.to_pydatetime(), snap.when)
    a(f"  Prior run   : {prior_when.strftime('%Y-%m-%d %H:%M IST')}  ({elapsed})")

    # (a) Regime numbers
    a("\n  Regime numbers")
    a(_fmt_diff("spot",              _pull_prior(prior, "spot"),          snap.spot,
                use_prefix=True, prefix="₹", precision=2))
    a(_fmt_diff("India VIX",         _pull_prior(prior, "vix"),           snap.vix, precision=2))
    prior_pct = _pull_prior(prior, "vix_pct_3mo")
    prior_pct_pct = prior_pct * 100 if prior_pct is not None else None
    a(_fmt_diff("VIX percentile",    prior_pct_pct,                       snap.vix_pct_3mo * 100,
                unit="%", precision=1))
    a(_fmt_diff("ATM IV",            _pull_prior(prior, "atm_iv"),        snap.atm_iv, unit="%", precision=1))
    a(_fmt_diff("RV-30d",            _pull_prior(prior, "rv_30d"),        snap.rv_30d, unit="%", precision=1))
    a(_fmt_diff("RV-60d",            _pull_prior(prior, "rv_60d"),        snap.rv_60d, unit="%", precision=1))
    prior_gap = None
    pi = _pull_prior(prior, "atm_iv"); pr = _pull_prior(prior, "rv_30d")
    if pi is not None and pr is not None:
        prior_gap = float(pi) - float(pr)
    a(_fmt_diff("IV − RV (VRP)",     prior_gap,                           snap.atm_iv - snap.rv_30d,
                unit="pp", precision=1))
    a(_fmt_diff("10-day high",       _pull_prior(prior, "high_10d"),      snap.high_10d,
                use_prefix=True, prefix="₹", precision=2))
    a(_fmt_diff("Pullback % (off 10d)", _pull_prior(prior, "pullback_pct"), snap.pullback_pct,
                unit="%", precision=2))

    # (b) Signal transitions
    a("\n  Signal transitions")
    transitions: list[str] = []
    for i, sig in enumerate(snap.signals, 1):
        col = _SIGNAL_COLS.get(sig.name)
        if col is None:
            continue
        prev_passed_raw = _pull_prior(prior, col)
        if prev_passed_raw is None:
            transitions.append(f"  Signal {i} ({sig.name}): {'✅' if sig.passed else '❌'}   (first observation)")
            continue
        prev_passed = bool(prev_passed_raw)
        if prev_passed == sig.passed:
            continue
        prev_mark = "✅" if prev_passed else "❌"
        cur_mark = "✅" if sig.passed else "❌"
        direction = "upgrade" if sig.passed else "downgrade"
        transitions.append(f"  Signal {i} ({sig.name}): {prev_mark} → {cur_mark}   ({direction})")
    if transitions:
        lines.extend(transitions)
    else:
        a(f"  all {len(snap.signals)} signals unchanged")

    # (c) Grade / score transition
    a("\n  Grade / score")
    prior_grade = _pull_prior(prior, "grade")
    prior_score = _pull_prior(prior, "score")
    total = len(snap.signals)
    if prior_grade is None or prior_score is None:
        a(f"  Grade              : {snap.grade} ({snap.score}/{total})   (first observation)")
    elif str(prior_grade) == snap.grade and int(prior_score) == snap.score:
        a(f"  Grade              : {snap.grade} ({snap.score}/{total})   (no change)")
    else:
        rank_delta = snap.score - int(prior_score)
        tag = "upgrade" if rank_delta > 0 else ("downgrade" if rank_delta < 0 else "reclassified")
        a(f"  Grade              : {prior_grade} ({int(prior_score)}/{total}) → {snap.grade} ({snap.score}/{total})   ({tag}, {rank_delta:+d})")

    # (d) Spread changes
    a("\n  Spread changes")
    prior_short = _pull_prior(prior, "short_strike")
    if snap.spread is None and prior_short is None:
        a("  (no spread in either run)")
    elif snap.spread is None:
        a("  spread no longer constructible — long-leg strike likely beyond chain depth now")
    elif prior_short is None:
        a("  new spread is now reachable in the chain (prior run had no constructible spread)")
    else:
        sp = snap.spread
        a(_fmt_diff("short strike",      _pull_prior(prior, "short_strike"), sp.short_strike, precision=0))
        a(_fmt_diff("long strike",       _pull_prior(prior, "long_strike"),  sp.long_strike, precision=0))
        a(_fmt_diff("short Δ",           _pull_prior(prior, "short_delta"),  sp.short_delta, precision=3))
        a(_fmt_diff("short IV",          _pull_prior(prior, "short_iv"),     sp.short_iv, unit="%", precision=1))
        a(_fmt_diff("net credit (₹/sh)", _pull_prior(prior, "credit"),       sp.credit, precision=2))
        a(_fmt_diff("credit per lot",    _pull_prior(prior, "credit_lot"),   sp.credit_lot,
                    use_prefix=True, prefix="₹", precision=0))
        a(_fmt_diff("max loss (₹/sh)",   _pull_prior(prior, "max_loss"),     sp.max_loss, precision=2))
        a(_fmt_diff("BP (SPAN proxy)",   _pull_prior(prior, "bp"),           sp.bp,
                    use_prefix=True, prefix="₹", precision=0))
        a(_fmt_diff("break-even",        _pull_prior(prior, "break_even"),   sp.break_even,
                    use_prefix=True, prefix="₹", precision=2))
        # Prior rows may carry either the legacy `pop_delta` (1 − |Δ|) or the
        # new `pop_iv` (N(d₂) under σ_IV). Prefer `pop_iv` when present; fall
        # back to `pop_delta` so the diff view still works on an older parquet.
        prior_pop_iv = _pull_prior(prior, "pop_iv")
        if prior_pop_iv is None:
            prior_pop_iv = _pull_prior(prior, "pop_delta")
        prior_pop_r = _pull_prior(prior, "pop_rv")
        a(_fmt_diff("POP (IV)",          prior_pop_iv * 100 if prior_pop_iv is not None else None,
                    sp.pop_iv * 100, unit="%", precision=0))
        a(_fmt_diff("POP (RV)",          prior_pop_r * 100 if prior_pop_r is not None else None,
                    sp.pop_rv * 100, unit="%", precision=0))

    return "\n".join(lines)


def _format_brief_delta(snap: RegimeSnap, prior: pd.Series | None) -> str:
    """One-line delta suffix for --brief mode. Returns '' if no prior."""
    if prior is None:
        return ""
    parts: list[str] = []
    prior_score = _pull_prior(prior, "score")
    prior_grade = _pull_prior(prior, "grade")
    if prior_score is not None and (int(prior_score) != snap.score or str(prior_grade) != snap.grade):
        parts.append(f"score {int(prior_score)}→{snap.score} ({prior_grade}→{snap.grade})")
    prior_vix = _pull_prior(prior, "vix")
    if prior_vix is not None:
        d = snap.vix - float(prior_vix)
        if abs(d) >= 0.01:
            parts.append(f"VIX {d:+.2f}")
    prior_spot = _pull_prior(prior, "spot")
    if prior_spot is not None:
        d = snap.spot - float(prior_spot)
        if abs(d) >= 0.5:
            parts.append(f"spot {d:+,.0f}")
    prior_pb = _pull_prior(prior, "pullback_pct")
    if prior_pb is not None:
        d = snap.pullback_pct - float(prior_pb)
        if abs(d) >= 0.05:
            parts.append(f"pullback {d:+.1f}pp")
    if not parts:
        return "  Δ: no material change"
    return "  Δ: " + ", ".join(parts[:4])


# ─── TUI (Rich live dashboard) ───────────────────────────────────────────────


_GRADE_STYLE = {
    "A+": "bold bright_green",
    "A":  "bold green",
    "B+": "yellow",
    "B":  "orange3",
    "B-": "red",
}


def _tui_delta_cell(prior_val, current_val, *, precision: int = 2, unit: str = "",
                    prefix: str = "", invert_color: bool = False) -> str:
    """Single arrow-diff formatted as Rich markup, compact for panel cells.

    invert_color: when True, 'up' is red and 'down' is green (e.g., for a VIX
    that would go bad if it dropped under the seller's feet). We use plain
    semantics (up=green, down=red) by default.
    """
    if current_val is None or (isinstance(current_val, float) and math.isnan(current_val)):
        return "[dim]—[/]"
    cur_s = f"{prefix}{float(current_val):,.{precision}f}{unit}"
    if prior_val is None or (isinstance(prior_val, float) and math.isnan(prior_val)):
        return f"{cur_s}  [dim](first)[/]"
    diff = float(current_val) - float(prior_val)
    display_eps = max(1e-9, 0.5 * (10 ** -precision))
    if abs(diff) < display_eps:
        return f"[dim]{cur_s}  (—)[/]"
    up_color, down_color = ("red", "green") if invert_color else ("green", "red")
    color = up_color if diff > 0 else down_color
    arrow = "↑" if diff > 0 else "↓"
    sign = "+" if diff > 0 else "−"
    return f"{cur_s}  [{color}]{arrow} {sign}{abs(diff):,.{precision}f}{unit}[/]"


def _tui_render(state: dict) -> "Layout":
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.align import Align

    snap = state.get("snap")
    history = state.get("history")
    error = state.get("error")
    refreshing = state.get("refreshing", False)
    last_ok_at = state.get("last_ok_at")
    refresh_secs = state.get("refresh_secs", 30)
    next_refresh_at = state.get("next_refresh_at")
    started_at = state.get("started_at")

    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=3),
    )

    # ─── Header ────────────────────────────────────────────────────────────
    header_parts = Text()
    header_parts.append(" NIFTY Credit-Spread Regime Watch ", style="bold")
    if snap is not None:
        header_parts.append("·  Grade ", style="dim")
        header_parts.append(snap.grade, style=_GRADE_STYLE.get(snap.grade, "white"))
        header_parts.append(f" ({snap.score}/{len(snap.signals)})  ", style="dim")
        # Timing score right next to grade — always visible regardless of
        # whether the enrichment panel fits vertically.
        if math.isfinite(snap.timing_score):
            timing_style = {
                "Strong": "bold bright_green", "Good": "bold green",
                "Neutral": "yellow", "Weak": "orange3", "Avoid": "bold red",
                "Unknown": "dim",
            }.get(snap.timing_grade, "white")
            header_parts.append("·  Timing ", style="dim")
            header_parts.append(f"{int(snap.timing_score)}/100 {snap.timing_grade}  ",
                                style=timing_style)
        # V3 gate — advisory badge. Colored by pass/fail so it's hard to miss.
        v3_style = "bold bright_green" if snap.v3_passed else "bold red"
        header_parts.append("·  V3 ", style="dim")
        header_parts.append(f"{'PASS' if snap.v3_passed else 'fail'}  ",
                            style=v3_style)
        header_parts.append(f"spot ₹{snap.spot:,.2f}  ", style="bold")
        header_parts.append(f"VIX {snap.vix:.2f}  ", style="cyan")
        header_parts.append(f"IV-RV {snap.atm_iv - snap.rv_30d:+.1f}pp  ", style="magenta")
        header_parts.append(f"pullback {snap.pullback_pct:.2f}%", style="blue")
    else:
        header_parts.append("·  Initialising…", style="yellow")

    right = Text()
    if last_ok_at is not None:
        right.append(f"updated {last_ok_at:%H:%M:%S IST}  ", style="dim")
    if refreshing:
        right.append("↻ refreshing", style="bold yellow")
    elif next_refresh_at is not None:
        remaining = max(0, int(next_refresh_at - time.monotonic()))
        right.append(f"next refresh in {remaining}s", style="dim")
    if error is not None:
        right.append(f"  ⚠ {error[:50]}", style="red")

    header_table = Table.grid(expand=True)
    header_table.add_column(justify="left", ratio=3)
    header_table.add_column(justify="right", ratio=1)
    header_table.add_row(header_parts, right)
    layout["header"].update(Panel(header_table, border_style="cyan", padding=(0, 1)))

    # ─── Body ──────────────────────────────────────────────────────────────
    body = layout["body"]
    body.split_row(
        Layout(name="left", ratio=1),
        Layout(name="right", ratio=1),
    )
    body["left"].split(
        Layout(name="snapshot", ratio=1),
        Layout(name="signals", ratio=1),
    )
    body["right"].split(
        Layout(name="candidate", ratio=1),
        Layout(name="enrichment", ratio=1),
        Layout(name="delta", ratio=1),
    )

    if snap is None:
        placeholder = Panel(Align.center(Text("Loading initial data — first API call can take ~5s.\nCtrl-C to quit.",
                                              style="dim", justify="center"),
                                         vertical="middle"),
                            title="Regime Watch", border_style="dim")
        body["left"]["snapshot"].update(placeholder)
        body["left"]["signals"].update(Panel(Text("", style="dim"), border_style="dim"))
        body["right"]["candidate"].update(Panel(Text("", style="dim"), border_style="dim"))
        body["right"]["enrichment"].update(Panel(Text("", style="dim"), border_style="dim"))
        body["right"]["delta"].update(Panel(Text("", style="dim"), border_style="dim"))
    else:
        body["left"]["snapshot"].update(_tui_snapshot_panel(snap))
        body["left"]["signals"].update(_tui_signals_panel(snap))
        body["right"]["candidate"].update(_tui_candidate_panel(snap))
        body["right"]["enrichment"].update(_tui_enrichment_panel(snap))
        prior = _prior_row(history) if history is not None else None
        body["right"]["delta"].update(_tui_delta_panel(snap, prior))

    # ─── Footer ────────────────────────────────────────────────────────────
    action_line = Text()
    if snap is None:
        action_line.append(" fetching first snapshot…", style="dim")
    else:
        tag = {
            "A++": (" ACTION: A++ regime — all conditions met, size fully.", "bold bright_green"),
            "A+": (" ACTION: A+ regime — size the proposed spread.",  "bold bright_green"),
            "A":  (" ACTION: A regime — consider half-size entry.",    "bold green"),
            "A-": (" ACTION: A− regime — mid-tier edge, quarter-size if taken.", "green"),
            "B+": (" ACTION: B+ regime — mixed signals, wait or paper-trade.", "yellow"),
            "B":  (" ACTION: B regime — not favourable, wait.",        "orange3"),
            "B-": (" ACTION: B- regime — no edge, monitor daily.",     "red"),
        }.get(snap.grade, (" ", "white"))
        action_line.append(tag[0], style=tag[1])
    hint = Text()
    hint.append(f"auto-refresh every {refresh_secs}s  ·  Ctrl-C to quit", style="dim")
    if started_at is not None:
        elapsed = int((datetime.now(IST_TZ) - started_at).total_seconds())
        hint.append(f"  ·  session {elapsed // 60}m{elapsed % 60:02d}s", style="dim")

    footer_table = Table.grid(expand=True)
    footer_table.add_column(justify="left", ratio=3)
    footer_table.add_column(justify="right", ratio=1)
    footer_table.add_row(action_line, hint)
    layout["footer"].update(Panel(footer_table, border_style="cyan", padding=(0, 1)))

    return layout


def _tui_snapshot_panel(snap: RegimeSnap) -> "Panel":
    from rich.panel import Panel
    from rich.table import Table

    tbl = Table.grid(padding=(0, 2), expand=True)
    tbl.add_column(style="dim", justify="right", no_wrap=True)
    tbl.add_column(justify="left")
    tbl.add_row("NIFTY spot",       f"[bold]₹{snap.spot:,.2f}[/]")
    tbl.add_row("target expiry",    f"{snap.expiry}  [dim]({snap.dte} DTE)[/]")
    tbl.add_row("India VIX",        f"[cyan]{snap.vix:.2f}[/]  [dim]({snap.vix_pct_3mo:.0%} percentile)[/]")
    tbl.add_row("ATM IV",           f"{snap.atm_iv:.1f}%")
    tbl.add_row("RV-30d / 60d",     f"{snap.rv_30d:.1f}% / {snap.rv_60d:.1f}%")
    gap = snap.atm_iv - snap.rv_30d
    gap_color = "green" if gap >= 0 else "red"
    tbl.add_row("IV − RV (VRP)",    f"[{gap_color}]{gap:+.1f}pp[/]")
    tbl.add_row("10-day high",      f"₹{snap.high_10d:,.2f}")
    tbl.add_row("10-day low",       f"₹{snap.low_10d:,.2f}")
    tbl.add_row("pullback",         f"{snap.pullback_pct:.2f}%")
    return Panel(tbl, title="Current Snapshot", border_style="blue", padding=(0, 1))


def _tui_signals_panel(snap: RegimeSnap) -> "Panel":
    from rich.panel import Panel
    from rich.table import Table

    tbl = Table.grid(padding=(0, 1), expand=True)
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=True)
    tbl.add_column(overflow="ellipsis", no_wrap=True)
    for i, sig in enumerate(snap.signals, 1):
        mark = "[green]✅[/]" if sig.passed else "[red]❌[/]"
        name = f"[bold]{i}.[/] {sig.name}"
        # Extract the headline phrase of the gap explainer (everything up to the
        # first newline or semicolon). Keeps the panel compact even on narrow
        # terminals — the full reasoning remains in non-TUI output.
        gap = sig.gap_explainer.split("\n", 1)[0]
        gap = gap.split(";", 1)[0].strip()
        style = "dim" if sig.passed else ""
        tbl.add_row(mark, name, f"[{style}]{gap}[/]" if style else gap)
    grade_line = (f"[bold]Grade[/] "
                  f"[{_GRADE_STYLE.get(snap.grade, 'white')}]{snap.grade}[/]"
                  f"  [dim]({snap.score} of {len(snap.signals)} conditions met)[/]")
    return Panel(tbl, title=grade_line, border_style="magenta", padding=(0, 1))


def _tui_candidate_panel(snap: RegimeSnap) -> "Panel":
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    if snap.spread is None:
        return Panel(Text("Could not build a Δ≈0.30, 100-wide spread — strikes unreachable.",
                          style="red"),
                     title="Candidate Spread", border_style="red", padding=(0, 1))
    sp = snap.spread
    tbl = Table.grid(padding=(0, 2), expand=True)
    tbl.add_column(style="dim", justify="right", no_wrap=True)
    tbl.add_column()
    tbl.add_row("structure",    f"[bold]NIFTY {sp.short_strike:.0f}/{sp.long_strike:.0f} PE[/]  [dim]({sp.dte} DTE)[/]")
    tbl.add_row("short leg",    f"₹{sp.short_mid:.2f}  [dim]Δ {sp.short_delta:+.3f}  IV {sp.short_iv:.1f}%[/]")
    tbl.add_row("long leg",     f"₹{sp.long_mid:.2f}")
    tbl.add_row("net credit",   f"[bold green]₹{sp.credit:.2f}[/]/sh  [dim]=  ₹{sp.credit_lot:,.0f}/lot[/]")
    tbl.add_row("max loss",     f"[red]₹{sp.max_loss:.2f}[/]/sh  [dim]=  ₹{sp.max_loss_lot:,.0f}/lot[/]")
    tbl.add_row("BP (1.5×)",    f"₹{sp.bp:,.0f}")
    tbl.add_row("break-even",   f"₹{sp.break_even:,.2f}  [dim]({(snap.spot - sp.break_even) / snap.spot * 100:.2f}% cushion)[/]")
    tbl.add_row("POP IV / RV",  f"[cyan]{sp.pop_iv:.0%}[/] / [yellow]{sp.pop_rv:.0%}[/]")
    if sp.pop_iv > sp.pop_rv + 0.05:
        tbl.add_row("",          f"[dim]⚠ RV under-prices chop by {(sp.pop_iv - sp.pop_rv) * 100:.0f}pp[/]")
    # Empirical POP from historical bucket — shown only when the calibration
    # table is populated (i.e. a backtest has been run through calibrate).
    if sp.empirical_n > 0 and math.isfinite(sp.empirical_pop):
        gap = (sp.pop_iv - sp.empirical_pop) * 100
        gap_tag = f"[red]⚠ model over-states by {gap:.0f}pp[/]" if gap > 10 else (
            f"[green]aligned[/]" if abs(gap) <= 10 else f"[dim]realised > model by {-gap:.0f}pp[/]"
        )
        tbl.add_row("POP empirical", f"[magenta]{sp.empirical_pop:.0%}[/]  [dim](n={sp.empirical_n})[/]  {gap_tag}")
    if sp.short_strike_iv and sp.short_strike_iv != snap.atm_iv:
        tbl.add_row("IV @ short", f"{sp.short_strike_iv:.1f}%  [dim](ATM {snap.atm_iv:.1f}%)[/]")
    tbl.add_row("ROI if held",  f"{sp.roi_if_held:.1f}% of BP")
    return Panel(tbl, title="Candidate Spread", border_style="green", padding=(0, 1))


def _tui_enrichment_panel(snap: RegimeSnap) -> "Panel":
    """Events + macro brief + IV-rank/trend/skew/term in one compact panel."""
    from rich.panel import Panel
    from rich.table import Table

    tbl = Table.grid(padding=(0, 2), expand=True)
    tbl.add_column(style="dim", justify="right", no_wrap=True)
    tbl.add_column()

    # ── V3 gate (most actionable single row) ─────────────────────────────
    v3_colour = "bright_green" if snap.v3_passed else "red"
    v3_label = "PASS" if snap.v3_passed else "fail"
    tbl.add_row("V3 GATE",    f"[bold {v3_colour}]{v3_label}[/]  "
                              f"[dim]event-sev: {snap.v3_event_severity}[/]")

    # ── Entry-timing (highest-value live signal) ─────────────────────────
    timing_colour = {
        "Strong": "bright_green", "Good": "green", "Neutral": "yellow",
        "Weak": "orange3", "Avoid": "red", "Unknown": "dim",
    }.get(snap.timing_grade, "white")
    if math.isfinite(snap.timing_score):
        tbl.add_row("TIMING",     f"[{timing_colour}]{int(snap.timing_score)}/100 {snap.timing_grade}[/]")
        tbl.add_row("BB z / sqz", f"{snap.bb_z:+.2f}  "
                                  f"{'[cyan]squeeze[/]' if snap.bb_squeeze else ''}")
        tbl.add_row("MACD",       f"hist {snap.macd_histogram:+.1f}  ({snap.macd_state})")
        tbl.add_row("Stoch %K/D", f"{snap.stoch_k:.0f}/{snap.stoch_d:.0f}  ({snap.stoch_state})")
    else:
        tbl.add_row("TIMING",     "[dim]— (need ≥ 30 bars)[/]")

    # ── Event risk (next priority) ────────────────────────────────────────
    sev_colour = {"high": "red", "medium": "yellow", "low": "green"}.get(snap.event_severity, "white")
    tbl.add_row("event-risk", f"[{sev_colour}]{snap.event_severity.upper()}[/]  "
                              f"[dim]{len(snap.upcoming_events)} in {snap.dte}d window[/]")
    if snap.upcoming_events:
        # Render at most 2 upcoming events so timing rows above don't get cut.
        for ev in snap.upcoming_events[:2]:
            ev_sev = (ev.get("severity") or "low").lower()
            col = {"high": "red", "medium": "yellow", "low": "green"}.get(ev_sev, "white")
            tbl.add_row("", f"[{col}]{ev.get('date')}[/]  {ev.get('kind','?')} — {ev.get('name','?')}")

    # ── Secondary numeric context ─────────────────────────────────────────
    tbl.add_row("IV Rank 12m", f"{snap.iv_rank_12mo*100:.0f}%" if math.isfinite(snap.iv_rank_12mo) else "—")
    tbl.add_row("trend",       f"{snap.trend_score}/3  "
                               f"{'[green]up[/]' if snap.trend_up else '[yellow]mixed[/]'}")
    tbl.add_row("25Δ skew",    f"{snap.skew_25d:.1f} vp" if math.isfinite(snap.skew_25d) else "[dim]— (needs call-chain)[/]")
    tbl.add_row("ATR-14 / pb", f"{snap.atr14:.1f}  ·  {snap.pullback_atr:.2f}σ"
                               if math.isfinite(snap.atr14) else "—")
    tbl.add_row("term slope",  f"{snap.term_slope:+.3f}" if math.isfinite(snap.term_slope) else "—")

    if snap.macro_brief_summary:
        tbl.add_row("macro brief", f"[italic]{snap.macro_brief_summary[:110]}{'…' if len(snap.macro_brief_summary) > 110 else ''}[/]")
    else:
        tbl.add_row("macro brief", "[dim]— (run --refresh-events)[/]")

    return Panel(tbl, title="Enrichment", border_style="cyan", padding=(0, 1))


def _tui_delta_panel(snap: RegimeSnap, prior: "pd.Series | None") -> "Panel":
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    if prior is None:
        return Panel(Text("first run — no prior comparison yet", style="dim"),
                     title="Δ vs last run", border_style="dim", padding=(0, 1))

    prior_when = pd.to_datetime(prior["when"])
    if prior_when.tzinfo is None:
        prior_when = prior_when.tz_localize(IST_TZ)
    else:
        prior_when = prior_when.tz_convert(IST_TZ)
    # Compute elapsed against live clock, not snap.when, so the "Xs ago" counter
    # ticks in real time between refreshes (render runs ~4×/sec).
    elapsed = _fmt_elapsed(prior_when.to_pydatetime(), datetime.now(IST_TZ))

    tbl = Table.grid(padding=(0, 2), expand=True)
    tbl.add_column(style="dim", justify="right", no_wrap=True)
    tbl.add_column()

    tbl.add_row("spot",         _tui_delta_cell(_pull_prior(prior, "spot"), snap.spot, precision=2, prefix="₹"))
    tbl.add_row("VIX",          _tui_delta_cell(_pull_prior(prior, "vix"), snap.vix, precision=2))
    prior_pct = _pull_prior(prior, "vix_pct_3mo")
    tbl.add_row("VIX %ile",     _tui_delta_cell(prior_pct * 100 if prior_pct is not None else None,
                                                 snap.vix_pct_3mo * 100, precision=1, unit="%"))
    tbl.add_row("ATM IV",       _tui_delta_cell(_pull_prior(prior, "atm_iv"), snap.atm_iv, precision=1, unit="%"))
    pi = _pull_prior(prior, "atm_iv"); pr = _pull_prior(prior, "rv_30d")
    prior_gap = float(pi) - float(pr) if pi is not None and pr is not None else None
    tbl.add_row("IV − RV",      _tui_delta_cell(prior_gap, snap.atm_iv - snap.rv_30d, precision=1, unit="pp"))
    tbl.add_row("pullback",     _tui_delta_cell(_pull_prior(prior, "pullback_pct"), snap.pullback_pct,
                                                 precision=2, unit="%"))
    tbl.add_row("10d high",     _tui_delta_cell(_pull_prior(prior, "high_10d"), snap.high_10d,
                                                 precision=2, prefix="₹"))

    # Signal transitions
    flips: list[str] = []
    for i, sig in enumerate(snap.signals, 1):
        col = _SIGNAL_COLS.get(sig.name)
        if col is None:
            continue
        prev_raw = _pull_prior(prior, col)
        if prev_raw is None:
            continue
        if bool(prev_raw) != sig.passed:
            prev_mark = "[green]✅[/]" if prev_raw else "[red]❌[/]"
            cur_mark = "[green]✅[/]" if sig.passed else "[red]❌[/]"
            flips.append(f"S{i} {prev_mark}→{cur_mark}")
    if flips:
        tbl.add_row("signals",  "  ".join(flips))

    # Grade transition
    prior_grade = _pull_prior(prior, "grade")
    prior_score = _pull_prior(prior, "score")
    if prior_grade is not None and prior_score is not None and (
        str(prior_grade) != snap.grade or int(prior_score) != snap.score
    ):
        tag = "bold green" if snap.score > int(prior_score) else "bold red"
        total = len(snap.signals)
        tbl.add_row("grade",
                    f"[dim]{prior_grade} ({int(prior_score)}/{total})[/] → "
                    f"[{tag}]{snap.grade} ({snap.score}/{total})[/]")

    # Spread credit delta (if both runs have a spread)
    if snap.spread is not None and _pull_prior(prior, "credit") is not None:
        tbl.add_row("credit/lot", _tui_delta_cell(_pull_prior(prior, "credit_lot"),
                                                   snap.spread.credit_lot,
                                                   precision=0, prefix="₹"))

    title = f"Δ vs last run  [dim]({elapsed})[/]"
    return Panel(tbl, title=title, border_style="yellow", padding=(0, 1))


def _tui_refresh_loop(state: dict, state_lock: threading.Lock,
                      stop_event: threading.Event, refresh_secs: int) -> None:
    while not stop_event.is_set():
        with state_lock:
            state["refreshing"] = True
        try:
            snap = evaluate()
            history = _append_history(snap)
            with state_lock:
                state["snap"] = snap
                state["history"] = history
                state["error"] = None
                state["last_ok_at"] = datetime.now(IST_TZ)
        except Exception as exc:
            # Keep prior snap on screen; surface the error in the header.
            with state_lock:
                state["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            with state_lock:
                state["refreshing"] = False
                state["next_refresh_at"] = time.monotonic() + refresh_secs
        # Wait the refresh period (interruptible).
        stop_event.wait(refresh_secs)


def _run_tui(refresh_secs: int) -> int:
    """Launch the live Rich dashboard. Ctrl-C exits cleanly."""
    try:
        from rich.console import Console
        from rich.live import Live
    except ImportError:
        print("regime_watch: 'rich' is required for --tui mode. Install with: pip install rich",
              file=sys.stderr)
        return 2

    refresh_secs = max(5, int(refresh_secs))   # floor: 5s (/optionchain has 3s cap)

    # One-shot warm-up: populate the Parallel caches before the live loop
    # starts, so the Enrichment panel is already filled on first render.
    _warmup_parallel_caches()

    state: dict = {
        "snap": None, "history": None, "error": None,
        "refreshing": False, "last_ok_at": None,
        "next_refresh_at": time.monotonic() + refresh_secs,
        "refresh_secs": refresh_secs,
        "started_at": datetime.now(IST_TZ),
    }
    state_lock = threading.Lock()
    stop_event = threading.Event()

    thread = threading.Thread(
        target=_tui_refresh_loop,
        args=(state, state_lock, stop_event, refresh_secs),
        daemon=True,
    )
    thread.start()

    console = Console()
    try:
        with Live(_tui_render(state), console=console,
                  refresh_per_second=4, screen=True, redirect_stderr=False) as live:
            while not stop_event.is_set():
                with state_lock:
                    snapshot_state = dict(state)
                live.update(_tui_render(snapshot_state))
                time.sleep(0.25)
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        stop_event.set()
        thread.join(timeout=2.0)
        console.print("[dim]regime_watch: TUI closed.[/]")
    return 0


# ─── Notifications ───────────────────────────────────────────────────────────


def _notify_mac(title: str, body: str) -> None:
    if sys.platform != "darwin":
        return
    safe_title = title.replace('"', '\\"')
    safe_body = body.replace('"', '\\"')
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_body}" with title "{safe_title}" sound name "Glass"'],
            check=False, timeout=5,
        )
    except Exception:
        pass


# ─── Output formatting ───────────────────────────────────────────────────────


def _fmt_dashboard(snap: RegimeSnap, prev: str | None, history: pd.DataFrame) -> str:
    lines: list[str] = []
    a = lines.append

    # Header
    a("═" * 78)
    a(f"  NIFTY Credit-Spread Regime Watch — {snap.when:%Y-%m-%d %H:%M IST}")
    a("═" * 78)

    # Current snapshot
    a("\nCURRENT SNAPSHOT")
    a(f"  NIFTY spot            : ₹{snap.spot:,.2f}  (last bar close)")
    a(f"  Entry date (if opened): {snap.entry_date}  (next NSE trading day)")
    a(f"  Target monthly expiry : {snap.expiry}  ({snap.dte} DTE)  ← closest to 35-DTE rule")
    a(f"  India VIX             : {snap.vix:.2f}")
    a(f"  Realized vol 30d      : {snap.rv_30d:.1f}%")
    a(f"  Realized vol 60d      : {snap.rv_60d:.1f}%")
    a(f"  ATM IV (expiry chain) : {snap.atm_iv:.1f}%")
    a(f"  10-day high           : ₹{snap.high_10d:,.2f}")
    a(f"  10-day low            : ₹{snap.low_10d:,.2f}")

    # Signals
    a("\n" + "─" * 78)
    a(f"  REGIME GRADING — Grade {snap.grade}   ({snap.score} of {len(snap.signals)} conditions met)")
    a("─" * 78)
    for i, sig in enumerate(snap.signals, 1):
        mark = "✅" if sig.passed else "❌"
        a(f"\n  {mark}  SIGNAL {i} — {sig.name}")
        a(f"      Current   : {sig.current:{'+.2f' if sig.unit == 'pp' else '.2f'}}{sig.unit}")
        a(f"      Target    : {sig.compare} {sig.target:.2f}{sig.unit}")
        a(f"      Status    : {'PASS ✓' if sig.passed else 'FAIL ✗'}  ({sig.gap_explainer})")
        a(f"      Why it    : {sig.why}")

    # V3 gate — read-only advisory from the 2026-04 redesign backtest.
    a("\n" + "─" * 78)
    v3_label = "PASS ✓" if snap.v3_passed else "FAIL ✗"
    a(f"  V3 GATE (advisory) — {v3_label}")
    a("─" * 78)
    a(f"  Rule: IV-RV + trend + V3-event (first 10d, RBI/FOMC/Budget only) "
      f"+ ≥1 of (VIX>20, VIX pct≥0.80, IVR 12m≥0.60).")
    a(f"  V3 event severity: {snap.v3_event_severity}")
    for line in snap.v3_reasoning:
        a(f"    • {line}")
    a(f"  Backtest: 11 fires/yr, 90% win-rate, Sharpe +1.75, 0% max-loss.  "
      f"Validate via paper-trading before live sizing.")

    # Entry timing — orthogonal to the grade; answers "is NOW a good entry day?"
    a("\n" + "─" * 78)
    if math.isfinite(snap.timing_score):
        a(f"  ENTRY TIMING — Score {int(snap.timing_score)}/100 ({snap.timing_grade})")
        a("─" * 78)
        bb_note = ("SQUEEZE — vol compression loading" if snap.bb_squeeze
                   else "normal bandwidth")
        a(f"  Bollinger:  z={snap.bb_z:+.2f}  bandwidth {snap.bb_bandwidth:.1f}%  "
          f"({bb_note})")
        a(f"  MACD:       histogram {snap.macd_histogram:+.2f}  state={snap.macd_state}")
        a(f"  Stochastic: %K={snap.stoch_k:.0f}  %D={snap.stoch_d:.0f}  "
          f"state={snap.stoch_state}")
        if snap.timing_reasoning:
            a("  Reasoning:")
            for r in snap.timing_reasoning:
                a(f"    • {r}")
    else:
        a("  ENTRY TIMING — insufficient history for indicators (<30 daily bars)")
        a("─" * 78)

    # Candidate spread
    a("\n" + "─" * 78)
    a("  CANDIDATE SPREAD (what you'd trade today, matches backtest winner config)")
    a("─" * 78)
    if snap.spread:
        sp = snap.spread
        a(f"\n  Structure     : NIFTY {sp.short_strike:.0f}/{sp.long_strike:.0f} PE  ({SPREAD_WIDTH}-wide put credit spread)")
        a(f"  Expiry        : {sp.expiry}  ({sp.dte} DTE)")
        a(f"  Short leg     : {sp.short_strike:.0f} PE  @ ₹{sp.short_mid:.2f}  (Δ {sp.short_delta:+.3f}, IV {sp.short_iv:.1f}%)")
        a(f"  Long  leg     : {sp.long_strike:.0f} PE  @ ₹{sp.long_mid:.2f}")
        a(f"  Net credit    : ₹{sp.credit:.2f}/sh × {LOT_NIFTY} lot = ₹{sp.credit_lot:,.0f} per lot (collected upfront)")
        a(f"  Max loss      : ₹{sp.max_loss:.2f}/sh × {LOT_NIFTY} = ₹{sp.max_loss_lot:,.0f} per lot")
        a(f"  Buying power  : ₹{sp.bp:,.0f}  ({MARGIN_MULT}× max-loss proxy for SPAN+exposure)")
        a(f"  Break-even    : NIFTY ≥ ₹{sp.break_even:,.2f} at expiry  "
          f"({(snap.spot - sp.break_even) / snap.spot * 100:.2f}% cushion)")
        a(f"  Max profit at : NIFTY ≥ ₹{sp.short_strike:,.2f} at expiry "
          f"({(snap.spot - sp.short_strike) / snap.spot * 100:.2f}% cushion)")
        a(f"  ROI if held   : {sp.roi_if_held:.1f}% of BP (max profit)")
        a(f"  POP (IV)      : {sp.pop_iv:.0%}  — BS N(d₂) with σ = short-strike IV (market-implied)")
        a(f"  POP (RV)      : {sp.pop_rv:.0%}  — BS N(d₂) with σ = 30-day realized vol")
        if sp.pop_iv > sp.pop_rv + 0.05:
            a(f"                  ⚠ RV-POP is {(sp.pop_iv - sp.pop_rv) * 100:.0f}pp lower than IV-POP "
              f"— market is under-pricing the chop the index is actually delivering")
    else:
        a("\n  ✗ could not build a Δ≈0.30, 100-wide spread — likely strikes unreachable in chain")

    # What-would-trigger-A+
    a("\n" + "─" * 78)
    a("  WHAT WOULD FLIP THIS TO A+")
    a("─" * 78)
    need_by_signal = [(s.name, s.gap_explainer, s.passed) for s in snap.signals]
    for name, gap, passed in need_by_signal:
        pointer = "✓" if passed else "→"
        a(f"  {pointer} {name}: {gap}")
    missing = [s for s in snap.signals if not s.passed]
    if not missing:
        a("\n  All four conditions already satisfied — this IS A+.")
    else:
        a(f"\n  Currently {len(missing)} condition{'s' if len(missing) != 1 else ''} away from A+. "
          "Signals 3 and 4 (IV-RV, pullback) respond fastest to tape action; signals 1 and 2 (VIX) "
          "typically lag but move together on macro shocks.")

    # Δ vs last run (migration view)
    a("\n" + "─" * 78)
    a("  Δ VS LAST RUN (migration view — prior → current)")
    a("─" * 78)
    a(_format_delta_section(snap, _prior_row(history)))

    # Trajectory (newest first)
    a("\n" + "─" * 78)
    a("  TRAJECTORY (last 8 runs, newest first)")
    a("─" * 78)
    tail = history.tail(8).iloc[::-1]
    # Old snapshots predate the 8-signal upgrade — their max score was 4.
    # Infer the denominator per row so the display stays consistent instead
    # of mixing "2/4" and "2/8" for the same numeric score.
    snap_total = len(snap.signals)
    legacy_cols = {"s_iv_rank", "s_trend", "s_skew", "s_event"}
    has_new_cols = any(c in tail.columns for c in legacy_cols)
    for _, r in tail.iterrows():
        when_s = pd.to_datetime(r["when"]).strftime("%Y-%m-%d %H:%M")
        arrow = ""
        if pd.notna(r.get("score")):
            arrow = "  " + "★" * int(r["score"])
        row_total = snap_total if (has_new_cols and pd.notna(r.get("s_iv_rank"))) else 4
        a(f"  {when_s}  {r['grade']:>2}  ({int(r['score'])}/{row_total})   "
          f"spot ₹{r['spot']:,.0f}   VIX {r['vix']:.2f}   "
          f"IV-RV {r['atm_iv'] - r['rv_30d']:+.1f}pp   pb {r['pullback_pct']:.2f}%{arrow}")

    # Commentary
    a("\n" + "─" * 78)
    a("  COMMENTARY")
    a("─" * 78)
    a(_commentary(snap))

    # Action — must cover the full 7-level ladder (A++/A+/A/A−/B+/B/B−).
    a("\n" + "═" * 78)
    total = len(snap.signals)
    action_map = {
        "A++": f"A++ regime. All {total} conditions green — size the proposed spread fully.",
        "A+":  f"A+ regime. Size the proposed spread. {snap.score}/{total} conditions green.",
        "A":   f"A regime. Strong setup — consider half-size entry.",
        "A-":  f"A− regime. Mid-tier edge — quarter-size if taken, or wait for A.",
        "B+":  f"B+ regime. Mixed signals — paper-trade to practice management or wait.",
        "B":   f"B regime. Few conditions met — not favourable; wait.",
        "B-":  f"B- regime. No premium-selling advantage. Monitor daily — fire when ≥ A.",
    }
    a(f"  ACTION: {action_map.get(snap.grade, f'{snap.grade} regime. Score {snap.score}/{total}.')}")
    a("═" * 78 + "\n")

    return "\n".join(lines)


def _commentary(snap: RegimeSnap) -> str:
    parts: list[str] = []
    # IV-RV regime
    gap = snap.atm_iv - snap.rv_30d
    if gap > 2:
        parts.append(f"IV is {gap:.1f}pp ABOVE RV — premium is being priced richly relative "
                     "to what the index is actually delivering. This is the classic "
                     "premium-seller's edge.")
    elif gap < -3:
        parts.append(f"IV is {abs(gap):.1f}pp BELOW RV — you are being paid less than the "
                     "chop justifies. Historically this is a losing game for systematic "
                     "premium sellers.")
    else:
        parts.append(f"IV-RV gap is close to zero ({gap:+.1f}pp) — premium is fairly priced.")

    # Trend / positioning
    if snap.pullback_pct < 0.5:
        parts.append(f"Spot is near the 10-day high ({snap.pullback_pct:.2f}% off) — you'd be "
                     "entering at the top of the recent range, which historically precedes "
                     "shallow mean-reverting pullbacks. The short strike is more exposed.")
    elif snap.pullback_pct > 3:
        parts.append(f"Spot is {snap.pullback_pct:.2f}% off the 10-day high — you're entering "
                     "into weakness, not strength, which is the preferred asymmetric entry.")

    # VIX regime
    if snap.vix > 20:
        parts.append(f"India VIX at {snap.vix:.2f} is elevated — macro uncertainty is visible.")
    elif snap.vix < 14:
        parts.append(f"India VIX at {snap.vix:.2f} is in a complacency zone — options are "
                     "cheap because nobody's worried. Selling cheap premium is rarely profitable.")

    return "  " + "\n  ".join(parts)


# ─── CLI ─────────────────────────────────────────────────────────────────────


def _is_market_hours(now: datetime | None = None) -> bool:
    now = now or datetime.now(IST_TZ)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dtime(9, 15) <= t <= dtime(15, 30)


def _run_once(brief: bool = False, alert_on_low_grade: bool = False) -> int:
    """Run one evaluation + print.

    Exit code semantics:
      0 — evaluation succeeded (default; suitable for smoke / health checks).
      2 — evaluation raised an exception (missing cache, network error, etc).
      1 — evaluation succeeded *and* grade is B+ or lower (score < 5).
          Only returned when `alert_on_low_grade=True` (`--alert-on-low-grade`
          CLI flag). Lets cron callers who want "nonzero means go look" keep
          that behaviour without conflating it with process health.
    """
    try:
        snap = evaluate()
    except Exception as exc:
        print(f"regime_watch: error — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    history = _append_history(snap)
    prev = _prev_grade(history)

    if brief:
        gap = snap.atm_iv - snap.rv_30d
        delta_suffix = _format_brief_delta(snap, _prior_row(history))
        ivr_str = f"ivr {snap.iv_rank_12mo*100:.0f}%" if math.isfinite(snap.iv_rank_12mo) else "ivr —"
        ev_str = f"ev={snap.event_severity}"
        timing_str = (
            f"timing {int(snap.timing_score)}/100 {snap.timing_grade}"
            if math.isfinite(snap.timing_score) else "timing —"
        )
        v3_str = f"V3={'PASS' if snap.v3_passed else 'fail'}"
        print(f"[{snap.when:%Y-%m-%d %H:%M IST}]  {snap.grade} ({snap.score}/8)  "
              f"spot ₹{snap.spot:,.0f}  VIX {snap.vix:.2f}  IV-RV {gap:+.1f}pp  "
              f"pullback {snap.pullback_pct:.2f}%  {ivr_str}  trend {snap.trend_score}/3  "
              f"{ev_str}  {timing_str}  {v3_str}{delta_suffix}", flush=True)
    else:
        print(_fmt_dashboard(snap, prev, history), flush=True)

    upgraded = snap.grade in ("A", "A+", "A++") and (
        prev is None or GRADE_RANK.get(prev, -1) < GRADE_RANK.get(snap.grade, -1)
    )
    if upgraded:
        title = f"NIFTY regime {snap.grade} — credit-spread setup"
        body = (f"VIX {snap.vix:.1f}  IV-RV {snap.atm_iv - snap.rv_30d:+.1f}pp  "
                f"pullback {snap.pullback_pct:.1f}%  spot ₹{snap.spot:,.0f}  "
                f"events={snap.event_severity}")
        _notify_mac(title, body)
        print(f"🔔 UPGRADE FIRED → {title}\n   {body}", flush=True)

    # Evaluation succeeded. Default: always return 0 (process-health signal).
    # When `alert_on_low_grade` is set, exit 1 for B+-or-lower grades so cron
    # callers can trigger only on interesting upgrades.
    if alert_on_low_grade and snap.score < 5:
        return 1
    return 0


def _refresh_via_parallel() -> int:
    """Delegate to `scripts/nfo/refresh_events.py` without re-implementing.

    Returns 0 only if BOTH refreshes succeed, 2 if the SDK isn't installed,
    and 1 if one or both Parallel calls failed. Cron / health-check callers
    can then alert on non-zero exits instead of silently trusting a stale
    events cache.
    """
    if not _HAS_PARALLEL:
        print("parallel-web not installed — can't refresh events.", file=sys.stderr)
        return 2
    failures: list[str] = []
    try:
        ev_df = events.refresh_all(horizon_days=90)
        print(f"events.parquet: {len(ev_df)} rows")
    except Exception as exc:
        failures.append(f"events:{type(exc).__name__}")
        print(f"events refresh failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    try:
        brief = enrich.macro_brief()
        print(f"macro_brief refreshed: {brief.summary[:120]}")
    except Exception as exc:
        failures.append(f"brief:{type(exc).__name__}")
        print(f"brief refresh failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    if failures:
        print(f"refresh completed with failures: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


def _warmup_parallel_caches(max_age_events_h: int = 24, max_age_brief_min: int = 60) -> None:
    """Refresh events.parquet and macro_brief.json if missing/stale — called
    at the start of --tui so the first render has enriched data.

    Silently no-ops when PARALLEL_OFFLINE=1, when the SDK is unavailable, or
    when the API raises — the live loop must never be blocked on enrichment.
    """
    import os
    if not _HAS_PARALLEL:
        return
    if os.environ.get("PARALLEL_OFFLINE") == "1":
        return
    ev_path = DATA_DIR / "events.parquet"
    br_path = DATA_DIR / "macro_brief.json"

    need_events = (not ev_path.exists()) or (
        time.time() - ev_path.stat().st_mtime > max_age_events_h * 3600
    )
    need_brief = (not br_path.exists()) or (
        time.time() - br_path.stat().st_mtime > max_age_brief_min * 60
    )
    if not (need_events or need_brief):
        return

    # Print a visible progress line; the TUI alt-screen kicks in after this,
    # so the message only shows until the first live render.
    print("regime_watch: warming up Parallel caches (events + macro brief)…", flush=True)
    if need_events:
        try:
            df = events.refresh_all(horizon_days=90)
            print(f"  events: {len(df)} rows", flush=True)
        except Exception as exc:
            print(f"  events refresh skipped: {type(exc).__name__}: {exc}", flush=True)
    if need_brief:
        try:
            enrich.macro_brief()
            print("  macro brief: refreshed", flush=True)
        except Exception as exc:
            print(f"  brief refresh skipped: {type(exc).__name__}: {exc}", flush=True)


def _show_history(last_n: int) -> int:
    if not HISTORY_PATH.exists():
        print("No history yet. Run once first.")
        return 1
    df = pd.read_parquet(HISTORY_PATH).tail(last_n).copy()
    df["when"] = pd.to_datetime(df["when"]).dt.strftime("%Y-%m-%d %H:%M")
    df["vix_pct_3mo"] = (df["vix_pct_3mo"] * 100).round(0).astype(int).astype(str) + "%"
    for c in ("spot", "vix", "atm_iv", "rv_30d", "pullback_pct"):
        if c in df.columns:
            df[c] = df[c].round(2)
    cols = [c for c in ["when", "grade", "score", "spot", "vix", "vix_pct_3mo",
                        "atm_iv", "rv_30d", "pullback_pct", "expiry", "dte"] if c in df.columns]
    print(df[cols].to_string(index=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loop", type=int, metavar="MIN",
                    help="Poll every MIN minutes during NSE market hours (09:15–15:30 IST Mon-Fri)")
    ap.add_argument("--history", type=int, nargs="?", const=20, metavar="N",
                    help="Show last N regime snapshots and exit (default 20)")
    ap.add_argument("--brief", action="store_true",
                    help="One-line summary output (for cron / logs).")
    ap.add_argument("--tui", action="store_true",
                    help="Launch the live responsive TUI dashboard (auto-refreshes).")
    ap.add_argument("--refresh", type=int, default=30, metavar="SECS",
                    help="TUI refresh interval in seconds (default 30, min 5).")
    ap.add_argument("--refresh-events", action="store_true",
                    help="Refresh events.parquet + macro_brief.json via Parallel and exit.")
    ap.add_argument("--no-parallel", action="store_true",
                    help="Force offline mode — skip Parallel calls, use cached data only.")
    ap.add_argument("--deep-brief", action="store_true",
                    help="When grade is A+/A, call the ultra-tier deep research brief.")
    ap.add_argument("--alert-on-low-grade", action="store_true",
                    help="Exit non-zero when evaluated grade is B+ or lower. Use for "
                         "cron callers that want 'nonzero means check it' semantics. "
                         "Default: always exit 0 on successful evaluation (smoke-safe).")
    args = ap.parse_args()

    if args.no_parallel:
        import os
        os.environ["PARALLEL_OFFLINE"] = "1"

    if args.refresh_events:
        return _refresh_via_parallel()

    if args.history is not None:
        return _show_history(args.history)

    if args.tui:
        return _run_tui(refresh_secs=args.refresh)

    if not args.loop:
        return _run_once(brief=args.brief, alert_on_low_grade=args.alert_on_low_grade)

    interval = max(args.loop * 60, 60)
    print(f"regime_watch: loop mode, every {args.loop} min during NSE hours. Ctrl-C to exit.", flush=True)
    while True:
        if _is_market_hours():
            try:
                _run_once(brief=args.brief, alert_on_low_grade=args.alert_on_low_grade)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"regime_watch: iter error — {exc}", file=sys.stderr, flush=True)
        else:
            now = datetime.now(IST_TZ)
            print(f"[{now:%Y-%m-%d %H:%M} IST] market closed, sleeping {args.loop}m", flush=True)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nregime_watch: stopped.", flush=True)
            return 0


if __name__ == "__main__":
    sys.exit(main())
