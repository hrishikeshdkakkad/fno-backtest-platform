"""2-year historical backtest — how often do all 8 regime signals tick?

Walks every NIFTY trading day in 2024-01-01 → 2026-04-10 and evaluates all
eight regime_watch signals as they would have read on that day, using only
data available up to that day (no look-ahead).

Outputs:
  - `results/nfo/historical_signals.parquet`  — daily pass/fail per signal.
  - `results/nfo/historical_summary.md`       — counts by score, grade
    distribution, list of 8/8 days, list of 7/8 days.

Usage:
  .venv/bin/python scripts/nfo/historical_backtest.py
  .venv/bin/python scripts/nfo/historical_backtest.py --start 2024-01-01 --end 2026-04-10

Data requirements:
  - NIFTY daily bars cached (we have these)
  - VIX daily bars cached OR fetchable via Dhan (fetches on first run)
  - NIFTY monthly-expiry PUT rolling parquets for each cycle (mostly cached)
  - NIFTY monthly-expiry CALL rolling parquets (stage E — optional for skew)
  - Hardcoded macro event calendar (RBI / FOMC / CPI / Budget)

Signal handling when data is missing for a given day:
  - The signal is recorded as `None` in the per-day frame.
  - The day's "total_passed" count excludes unknowns (shows lower score).
  - Reports list how many days had unknowns in each signal.
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from nfo import bsm, calendar_nfo, data as ndata, signals as sig_mod, universe
from nfo.client import DhanClient
from nfo.config import DATA_DIR, RESULTS_DIR, IST

log = logging.getLogger("historical_backtest")


# ── Hardcoded macro event calendar (2024-01 → 2026-06) ─────────────────────
# Best-effort assembly from public sources. Covers RBI MPC decisions, FOMC
# decisions, US CPI releases, and Indian Union Budget days. Missing days
# are treated as no event. Event-risk signal goes to "high" if any of these
# falls within the current cycle's DTE window.

_RBI_MPC = [
    date(2024, 2, 8),  date(2024, 4, 5),  date(2024, 6, 7),
    date(2024, 8, 8),  date(2024, 10, 9), date(2024, 12, 6),
    date(2025, 2, 7),  date(2025, 4, 9),  date(2025, 6, 6),
    date(2025, 8, 6),  date(2025, 10, 1), date(2025, 12, 5),
    date(2026, 2, 6),  date(2026, 4, 8),  date(2026, 6, 5),
]
_FOMC = [
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1),  date(2024, 6, 12),
    date(2024, 7, 31), date(2024, 9, 18), date(2024, 11, 7), date(2024, 12, 18),
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7),  date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29),date(2025, 12, 10),
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
]
# US CPI — roughly 2nd Tuesday / Wednesday each month.
_US_CPI = [
    date(2024, 1, 11), date(2024, 2, 13), date(2024, 3, 12), date(2024, 4, 10),
    date(2024, 5, 15), date(2024, 6, 12), date(2024, 7, 11), date(2024, 8, 14),
    date(2024, 9, 11), date(2024, 10, 10),date(2024, 11, 13),date(2024, 12, 11),
    date(2025, 1, 15), date(2025, 2, 12), date(2025, 3, 12), date(2025, 4, 10),
    date(2025, 5, 13), date(2025, 6, 11), date(2025, 7, 15), date(2025, 8, 12),
    date(2025, 9, 11), date(2025, 10, 15),date(2025, 11, 13),date(2025, 12, 10),
    # 2026 CPI dates from BLS official schedule (reviewer-verified).
    date(2026, 1, 13), date(2026, 2, 13), date(2026, 3, 11), date(2026, 4, 10),
    date(2026, 5, 12), date(2026, 6, 10),
]
_BUDGET = [
    date(2024, 2, 1),  date(2024, 7, 23),   # Interim + Full-Year
    date(2025, 2, 1),
    date(2026, 2, 1),
]
HARD_EVENTS: list[tuple[date, str, str]] = (
    [(d, "RBI MPC", "RBI") for d in _RBI_MPC] +
    [(d, "FOMC", "FOMC") for d in _FOMC] +
    [(d, "US CPI", "CPI") for d in _US_CPI] +
    [(d, "Union Budget", "BUDGET") for d in _BUDGET]
)


# Merge in the primary-sourced 2020-08 → 2023-12 backfill if present. The
# backfill file is committed under configs/nfo/events/; a missing file is
# tolerated (callers that only need forward-looking events will not load it).
# De-duplicates on (date, kind) so re-runs don't double-count.
def _merge_sourced_backfill() -> None:
    global HARD_EVENTS
    from pathlib import Path as _P
    from nfo.events import load_sourced_backfill as _load

    backfill_path = _P(__file__).resolve().parents[2] / "configs" / "nfo" / "events" / "backfill_2020_2023.yaml"
    if not backfill_path.exists():
        return
    seen = {(d, k) for d, _, k in HARD_EVENTS}
    for tup in _load(backfill_path):
        if (tup[0], tup[2]) not in seen:
            HARD_EVENTS.append(tup)
            seen.add((tup[0], tup[2]))
    HARD_EVENTS.sort(key=lambda t: (t[0], t[2]))


_merge_sourced_backfill()


# ── Thresholds (mirror regime_watch defaults post-tuning) ───────────────────
VIX_RICH = 20.0
VIX_PCT_RICH = 0.80
IV_RV_SPREAD_RICH = -2.0
PULLBACK_PCT = 2.0
IV_RANK_RICH = 0.60
SKEW_RICH_MAX = 6.0
TREND_MIN_SCORE = 2


@dataclass(slots=True)
class CycleChain:
    """Cached option-chain view of a single monthly cycle across its lifetime."""
    entry_date: date
    expiry_date: date
    put_bars: dict[int, pd.DataFrame] = field(default_factory=dict)    # offset → hourly bars
    call_bars: dict[int, pd.DataFrame] = field(default_factory=dict)


def _load_nifty_daily() -> pd.DataFrame:
    """Concat all cached NIFTY daily parquets into one deduped frame."""
    index_dir = DATA_DIR / "index"
    frames = [pd.read_parquet(p) for p in index_dir.glob("NIFTY_*.parquet")]
    if not frames:
        raise FileNotFoundError(f"No NIFTY daily parquets under {index_dir}")
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    return df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)


def _load_vix_daily(client: DhanClient | None, start: date, end: date) -> pd.DataFrame:
    """Pull VIX daily bars or load from cache. One Dhan call on first run.

    VIX is security_id=21 on IDX_I in Dhan's master.
    """
    cache_path = DATA_DIR / "index" / f"VIX_{start.isoformat()}_{end.isoformat()}.parquet"
    existing = [p for p in (DATA_DIR / "index").glob("VIX_*.parquet")]
    cached_df: pd.DataFrame | None = None
    if existing:
        frames = [pd.read_parquet(p) for p in existing]
        cached_df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["date"])
        cached_df["date"] = pd.to_datetime(cached_df["date"])
        cached_df = cached_df.sort_values("date").reset_index(drop=True)
        if not cached_df.empty:
            cmax = cached_df["date"].max().date()
            cmin = cached_df["date"].min().date()
            # Accept cache with a small tail gap (≤ 7 calendar days). Dhan
            # lags by 1-2 sessions during market hours, and a 1-day shortfall
            # is not worth a network call for offline reproducibility.
            tail_gap = (end - cmax).days
            if cmin <= start and tail_gap <= 7:
                if tail_gap > 0:
                    log.info("Using cached VIX (ends %s, requested %s, "
                             "tail gap %d days acceptable).", cmax, end, tail_gap)
                # IMPORTANT: do NOT filter to [start, end] — downstream
                # signals (vix_pct_3mo: 63-day lookback, iv_rank_12mo:
                # 252-day lookback proxied via VIX) need history BEFORE
                # `start`. evaluate_day slices per-day via `vix_df[...<= on_date]`,
                # so returning the full cached range is the correct
                # "provide as much warmup as we have" behavior.
                return cached_df.reset_index(drop=True)

    # Cache insufficient and we need the network.
    if client is None:
        if cached_df is not None and not cached_df.empty:
            log.warning("VIX cache is partial and no Dhan client provided; "
                        "returning %d cached rows instead of raising.", len(cached_df))
            return cached_df.reset_index(drop=True)
        raise RuntimeError("VIX not cached and no Dhan client to fetch it")

    log.info("Pulling VIX daily bars %s → %s", start, end)
    try:
        resp = client.chart_historical(
            exchange_segment="IDX_I", instrument="INDEX", security_id=21,
            from_date=start.isoformat(), to_date=end.isoformat(), oi=False,
        )
    except Exception as exc:
        # Offline / connection-refused path — fall back to whatever cache we
        # have. This preserves reproducibility when Dhan is unavailable and
        # the cached VIX covers most of the requested window.
        if cached_df is not None and not cached_df.empty:
            log.warning("Dhan VIX fetch failed (%s: %s); using cached %d rows "
                        "covering %s → %s.",
                        type(exc).__name__, str(exc)[:80], len(cached_df),
                        cached_df['date'].min().date(),
                        cached_df['date'].max().date())
            return cached_df.reset_index(drop=True)
        raise
    if not resp.get("close"):
        raise RuntimeError("empty VIX history — Dhan gap?")
    ts = pd.to_datetime(resp["timestamp"], unit="s", utc=True).tz_convert(IST)
    df = pd.DataFrame({
        "date": ts.normalize().tz_localize(None),
        "open": resp["open"], "high": resp["high"],
        "low": resp["low"], "close": resp["close"],
    })
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, index=False)
    log.info("VIX cached (%d rows): %s", len(df), cache_path.name)
    return df


def _load_cycle_chain(
    client: DhanClient | None,
    cycle: calendar_nfo.MonthlyCycle,
    pull_calls: bool = False,
) -> CycleChain:
    """Load all offsets for a cycle from the rolling cache.

    If `pull_calls=True` and calls aren't cached, fetch them. Puts are assumed
    cached (we verified 18/18 coverage).
    """
    nifty = universe.get("NIFTY")
    start = cycle.entry_target_date.isoformat()
    end = cycle.expiry_date.isoformat()

    chain = CycleChain(entry_date=cycle.entry_target_date, expiry_date=cycle.expiry_date)
    # Load puts from cache.
    for offset in range(-15, 11):
        bars = ndata.fetch_rolling_offset(
            client, nifty, expiry_code=1, expiry_flag="MONTH",
            option_type="PUT", offset=offset,
            from_date=start, to_date=end,
            refresh=False,
        ) if client else _try_load_cached("PUT", offset, start, end)
        if bars is not None and not bars.empty:
            chain.put_bars[offset] = bars

    if pull_calls:
        for offset in range(-5, 16):   # call OTM is above spot, use 0..+15
            bars = ndata.fetch_rolling_offset(
                client, nifty, expiry_code=1, expiry_flag="MONTH",
                option_type="CALL", offset=offset,
                from_date=start, to_date=end,
                refresh=False,
            )
            if bars is not None and not bars.empty:
                chain.call_bars[offset] = bars
    return chain


def _try_load_cached(option_type: str, offset: int, start: str, end: str) -> pd.DataFrame | None:
    """Try to load from cache without a Dhan client (for offline runs)."""
    from nfo import cache as _cache
    from nfo.data import RollingKey, _cache_key
    key = RollingKey("NIFTY", 1, "MONTH", option_type, offset)
    return _cache.load("rolling", _cache_key(key, start, end))


# Session-scoped counter for dropped IV rows in _daily_snapshot_for_cycle.
# Exposed so callers (expand_history.py) can log per-run anomaly counts
# without re-scanning the rolling cache.
IV_FILTER_COUNTS: dict[str, int] = {
    "dropped_zero_or_negative": 0,
    "dropped_above_ceiling": 0,
    "total_dropped": 0,
}


def _reset_iv_filter_counts() -> None:
    for k in IV_FILTER_COUNTS:
        IV_FILTER_COUNTS[k] = 0


def _daily_snapshot_for_cycle(chain: CycleChain, on_date: date) -> pd.DataFrame:
    """Return a DataFrame[strike, iv, close, delta] for puts+calls on `on_date`.

    Uses the last hourly bar of `on_date` from each cached offset and labels
    the rows by option_type so downstream consumers can filter.

    Applies the IV anomaly filter (``nfo.data.drop_iv_anomalies``) at the
    per-contract level. Rows with IV ≤ 0 or IV > 100% annualized are
    dropped (not clamped) and counted in the module-level IV_FILTER_COUNTS.
    The raw rolling-option cache is unchanged — forensics remain possible.
    """
    from nfo.data import drop_iv_anomalies

    rows: list[dict] = []
    for opt_type, by_offset in (("PUT", chain.put_bars), ("CALL", chain.call_bars)):
        for offset, bars in by_offset.items():
            if bars.empty or "t" not in bars.columns:
                continue
            ts = pd.to_datetime(bars["t"], unit="s", utc=True).dt.tz_convert(IST)
            day = ts.dt.normalize().dt.tz_localize(None)
            mask = day == pd.Timestamp(on_date)
            subset = bars[mask]
            if subset.empty:
                prior = bars[day <= pd.Timestamp(on_date)].tail(1)
                if prior.empty:
                    continue
                subset = prior
            row = subset.iloc[-1]
            rows.append({
                "option_type": opt_type,
                "offset": offset,
                "strike": float(row["strike"]),
                "close": float(row["close"]),
                "iv": float(row["iv"]) if pd.notna(row["iv"]) else np.nan,
                "spot": float(row["spot"]) if pd.notna(row["spot"]) else np.nan,
            })
    snap = pd.DataFrame(rows)
    if not snap.empty:
        snap, counts = drop_iv_anomalies(snap)
        for k, v in counts.items():
            IV_FILTER_COUNTS[k] += v
    return snap


def _event_severity(on_date: date, dte: int) -> tuple[str, list[str]]:
    """Return (severity, names) for events in [on_date, on_date+dte]."""
    horizon_end = on_date + timedelta(days=dte)
    hits = [(d, name, kind) for d, name, kind in HARD_EVENTS
            if on_date <= d <= horizon_end]
    if not hits:
        return "low", []
    high_kinds = {"RBI", "FOMC", "CPI", "BUDGET"}
    if any(k in high_kinds for _, _, k in hits):
        return "high", [f"{d.isoformat()} {n}" for d, n, _ in hits[:3]]
    return "medium", [f"{d.isoformat()} {n}" for d, n, _ in hits[:3]]


def _pick_target_cycle(on_date: date, cycles: list[calendar_nfo.MonthlyCycle]) -> calendar_nfo.MonthlyCycle | None:
    """Return the monthly cycle whose expiry is closest to `on_date + 35d`
    within a 20-70 DTE window — same logic as regime_watch._target_monthly_expiry."""
    candidates = [(c, (c.expiry_date - on_date).days) for c in cycles]
    candidates = [(c, dte) for c, dte in candidates if 20 <= dte <= 70]
    if not candidates:
        return None
    candidates.sort(key=lambda x: (abs(x[1] - 35), -x[1]))
    return candidates[0][0]


def evaluate_day(
    on_date: date,
    spot_df: pd.DataFrame,
    vix_df: pd.DataFrame,
    cycles: list[calendar_nfo.MonthlyCycle],
    chains_by_expiry: dict[date, CycleChain],
) -> dict:
    """Compute all 8 signals as-of `on_date`. Returns a flat dict for parquet."""
    out: dict = {"date": on_date}

    # Slice data to history strictly ≤ on_date.
    sp = spot_df[spot_df["date"] <= pd.Timestamp(on_date)]
    vx = vix_df[vix_df["date"] <= pd.Timestamp(on_date)]
    if sp.empty or vx.empty:
        out["error"] = "no spot/vix history"
        return out
    spot = float(sp["close"].iloc[-1])
    vix = float(vx["close"].iloc[-1])
    out["spot"] = spot
    out["vix"] = vix

    # Signal 4 — pullback %.
    last_10 = sp.tail(10)
    hi10 = float(last_10["high"].max())
    pullback = max(0.0, (hi10 - spot) / hi10 * 100.0) if hi10 > 0 else 0.0
    out["pullback_pct"] = pullback
    out["s4_pullback"] = pullback >= PULLBACK_PCT

    # RV-30d.
    closes = sp["close"].astype(float).to_numpy()
    if len(closes) >= 31:
        rets = np.log(closes[1:] / closes[:-1])[-30:]
        rv30 = float(np.std(rets, ddof=1) * np.sqrt(252) * 100.0)
    else:
        rv30 = float("nan")
    out["rv_30d"] = rv30

    # Signal 1 & 2 — VIX absolute + 3-month percentile.
    out["s1_vix_abs"] = vix > VIX_RICH
    vix_3mo = vx["close"].tail(63).astype(float).to_numpy()
    if len(vix_3mo) >= 2:
        vix_pct = float(np.mean(vix_3mo <= vix))
    else:
        vix_pct = float("nan")
    out["vix_pct_3mo"] = vix_pct
    out["s2_vix_pct"] = np.isfinite(vix_pct) and vix_pct >= VIX_PCT_RICH

    # Signal 5 — IV Rank 12-mo (using VIX as ATM-IV proxy — same as live).
    iv_rank = sig_mod.iv_rank(vx["close"].astype(float), lookback=252)
    out["iv_rank_12mo"] = iv_rank
    out["s5_iv_rank"] = np.isfinite(iv_rank) and iv_rank >= IV_RANK_RICH

    # Signal 6 — Trend filter.
    trend = sig_mod.trend_regime(sp)
    out["trend_score"] = trend.score
    out["s6_trend"] = trend.score >= TREND_MIN_SCORE

    # Pick the target monthly cycle for this day.
    cycle = _pick_target_cycle(on_date, cycles)
    if cycle is None:
        out["error"] = "no target cycle in 20-70 DTE window"
        out["s3_iv_rv"] = None
        out["s7_skew"] = None
        out["s8_event"] = None
        return out
    out["target_expiry"] = cycle.expiry_date.isoformat()
    dte = (cycle.expiry_date - on_date).days
    out["dte"] = dte

    # Signal 8 — event risk (hardcoded calendar).
    severity, ev_names = _event_severity(on_date, dte)
    out["event_severity"] = severity
    out["events_in_window"] = "; ".join(ev_names) if ev_names else ""
    out["s8_event"] = severity != "high"

    # Signal 3 — IV − RV. ATM IV = strike closest to spot in chain for this day.
    chain = chains_by_expiry.get(cycle.expiry_date)
    if chain is None:
        out["s3_iv_rv"] = None
        out["s7_skew"] = None
        return out
    snap = _daily_snapshot_for_cycle(chain, on_date)
    if snap.empty:
        out["s3_iv_rv"] = None
        out["s7_skew"] = None
        return out

    puts = snap[snap["option_type"] == "PUT"]
    calls = snap[snap["option_type"] == "CALL"]

    atm_iv = float("nan")
    short_strike_iv = float("nan")
    if not puts.empty:
        atm_row = puts.iloc[(puts["strike"] - spot).abs().argsort()].head(1)
        atm_iv = float(atm_row["iv"].iloc[0]) if pd.notna(atm_row["iv"].iloc[0]) else float("nan")
        # Short-strike IV: pick the put whose |Δ| is closest to 0.30 using
        # per-strike IV (same logic as live pick_short_leg). This makes
        # signal 3 consistent with the live V3 gate (reviewer F2-backtest).
        t_years = max(dte / 365.0, 1e-4)
        valid = puts[(puts["iv"] > 0) & (puts["close"] > 0)].copy()
        if not valid.empty:
            valid["delta"] = valid.apply(
                lambda r: bsm.put_delta(spot, float(r["strike"]),
                                        t_years, float(r["iv"]) / 100.0),
                axis=1,
            )
            valid["delta_err"] = (valid["delta"].abs() - 0.30).abs()
            best = valid.sort_values("delta_err").iloc[0]
            short_strike_iv = float(best["iv"]) if pd.notna(best["iv"]) else float("nan")
    out["atm_iv"] = atm_iv
    out["short_strike_iv"] = short_strike_iv
    # Signal 3 uses short-strike IV when available (matches live V3);
    # falls back to ATM IV for partial-data days.
    iv_for_signal3 = short_strike_iv if np.isfinite(short_strike_iv) else atm_iv
    if np.isfinite(iv_for_signal3) and np.isfinite(rv30):
        out["iv_minus_rv"] = iv_for_signal3 - rv30
        out["s3_iv_rv"] = (iv_for_signal3 - rv30) >= IV_RV_SPREAD_RICH
    else:
        out["iv_minus_rv"] = float("nan")
        out["s3_iv_rv"] = None

    # Signal 7 — 25Δ skew. Needs call data.
    if calls.empty:
        out["s7_skew"] = None
        out["skew_25d"] = float("nan")
    else:
        t_years = max(dte / 365.0, 1e-4)
        skew = sig_mod.skew_25d(puts, calls, spot=spot, years_to_expiry=t_years)
        out["skew_25d"] = skew.skew_vol_pts
        out["s7_skew"] = np.isfinite(skew.skew_vol_pts) and skew.skew_vol_pts <= SKEW_RICH_MAX

    return out


def run_backtest(
    start: date, end: date,
    pull_calls: bool = False,
    client_factory=DhanClient,
) -> pd.DataFrame:
    """Main loop. Returns a DataFrame of per-day signal rows."""
    spot_df = _load_nifty_daily()
    log.info("NIFTY daily: %d bars (%s → %s)",
             len(spot_df), spot_df['date'].min().date(), spot_df['date'].max().date())

    client: DhanClient | None = None
    try:
        client = client_factory()
    except Exception as e:
        log.warning("Dhan client unavailable (%s); using cache-only", e)

    vix_df = _load_vix_daily(client, start, end)
    log.info("VIX daily: %d bars (%s → %s)",
             len(vix_df), vix_df['date'].min().date(), vix_df['date'].max().date())

    # Build monthly cycles covering the window.
    nifty = universe.get("NIFTY")
    cycles = calendar_nfo.build_cycles(nifty, spot_df,
                                       start - timedelta(days=35),
                                       end + timedelta(days=35),
                                       target_dte=35)
    log.info("Built %d monthly cycles.", len(cycles))

    # Prefetch option chains for each cycle (uses cache; only pulls calls if pull_calls=True).
    chains_by_expiry: dict[date, CycleChain] = {}
    for cyc in cycles:
        if not ((cyc.entry_target_date - timedelta(days=5)) <= end
                and cyc.expiry_date >= start):
            continue
        chain = _load_cycle_chain(client, cyc, pull_calls=pull_calls)
        if chain.put_bars or chain.call_bars:
            chains_by_expiry[cyc.expiry_date] = chain
            log.debug("Cached cycle %s (puts=%d, calls=%d)",
                      cyc.expiry_date, len(chain.put_bars), len(chain.call_bars))

    log.info("Cycles with data: %d / %d", len(chains_by_expiry), len(cycles))

    # Walk every trading day.
    trading_days = spot_df[
        (spot_df["date"] >= pd.Timestamp(start)) &
        (spot_df["date"] <= pd.Timestamp(end))
    ]["date"].dt.date.tolist()
    log.info("Evaluating %d trading days.", len(trading_days))

    rows: list[dict] = []
    for i, d in enumerate(trading_days):
        try:
            row = evaluate_day(d, spot_df, vix_df, cycles, chains_by_expiry)
        except Exception as exc:
            log.warning("day %s: %s", d, exc)
            continue
        rows.append(row)
        if (i + 1) % 50 == 0:
            log.info("  processed %d / %d days", i + 1, len(trading_days))

    return pd.DataFrame(rows)


def summarise(frame: pd.DataFrame) -> str:
    """Text report of counts, grade distribution, A++ dates."""
    n = len(frame)
    if n == 0:
        return "No trading days evaluated."

    sig_cols = [f"s{i}_{name}" for i, name in enumerate(
        ["vix_abs", "vix_pct", "iv_rv", "pullback", "iv_rank", "trend", "skew", "event"],
        start=1,
    )]
    # Count of True (pass) per row, ignoring None.
    def score(row):
        return sum(1 for c in sig_cols if row.get(c) is True)
    frame = frame.copy()
    frame["score"] = frame.apply(score, axis=1)

    # Per-signal pass / fail / unknown counts.
    per_signal = {}
    for c in sig_cols:
        pass_n = int((frame[c] == True).sum())
        fail_n = int((frame[c] == False).sum())
        unk_n  = int(frame[c].isna().sum())
        per_signal[c] = (pass_n, fail_n, unk_n)

    score_dist = Counter(frame["score"])

    lines = [
        f"# Historical 2-year backtest",
        "",
        f"Window: {frame['date'].min()} → {frame['date'].max()}",
        f"Trading days evaluated: **{n}**",
        "",
        "## Per-signal pass / fail / unknown",
        "",
        "| Signal | Pass | Fail | Unknown |",
        "|---|---:|---:|---:|",
    ]
    for c, (p, f, u) in per_signal.items():
        lines.append(f"| {c} | {p} | {f} | {u} |")

    lines += [
        "",
        "## Score distribution (signals passing per day)",
        "",
        "| Score | Days | % |",
        "|---:|---:|---:|",
    ]
    for s in sorted(score_dist.keys(), reverse=True):
        lines.append(f"| {s}/8 | {score_dist[s]} | {score_dist[s]/n*100:.1f}% |")

    # A+ and A++ days (≥ 7/8 and 8/8).
    top_days = frame[frame["score"] >= 7].sort_values("date")
    lines += ["", "## Days with score ≥ 7/8", ""]
    if top_days.empty:
        lines.append("_None._")
    else:
        lines.append("| Date | Score | Spot | VIX | IV-RV | Events |")
        lines.append("|---|---:|---:|---:|---:|---|")
        for _, r in top_days.iterrows():
            ev = (r.get("events_in_window") or "")[:60]
            lines.append(
                f"| {r['date']} | {int(r['score'])}/8 | ₹{r.get('spot', float('nan')):,.0f} |"
                f" {r.get('vix', float('nan')):.1f} | {r.get('iv_minus_rv', float('nan')):+.1f}pp |"
                f" {ev} |"
            )

    lines += ["", f"**Total ≥ 7/8 days: {len(top_days)}** "
              f"({len(top_days)/n*100:.1f}% of trading days)."]
    lines.append(f"**Total 8/8 days: {(frame['score'] == 8).sum()}**")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default="2024-01-15")
    p.add_argument("--end", default="2026-04-10")
    p.add_argument("--pull-calls", action="store_true",
                   help="Fetch call-side rolling data (enables 25Δ skew, signal 7).")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    frame = run_backtest(start, end, pull_calls=args.pull_calls)

    out_parquet = RESULTS_DIR / "historical_signals.parquet"
    out_md = RESULTS_DIR / "historical_summary.md"
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out_parquet, index=False)
    report = summarise(frame)
    out_md.write_text(report, encoding="utf-8")
    print(report)
    log.info("Wrote %s and %s", out_parquet, out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
