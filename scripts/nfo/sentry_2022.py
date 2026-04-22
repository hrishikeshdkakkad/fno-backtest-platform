"""2022 NIFTY V3 sentry — narrow ingest + fire-count gate.

Purpose: answer the one question that gates the full 2020-08 → 2023-12
backfill: did V3 fire materially more, materially less, or about the same in
the 2022 sustained-vol regime as in the 2024-2026 calibration window?

Operationally the script:
  1. Fetches NIFTY spot + VIX daily bars for 2021-01-01 .. 2023-02-15 (covers
     the 2022 window + 12-mo lookback + DTE overhead).
  2. Invokes historical_backtest.run_backtest(2022-01-01, 2022-12-31), which
     will lazily fetch rolling-option data for each 2022 monthly cycle.
  3. Applies the canonical V3 trigger gate to count fires.
  4. Writes the signals parquet + a one-page report to
     ``results/nfo/audits/`` — deliberately NOT to ``results/nfo/`` so the
     canonical historical_signals.parquet and historical_summary.md are left
     untouched.

The V3 gate here is deliberately a thin, inspectable re-implementation rather
than the engine.triggers.TriggerEvaluator path, because the sentry operates
on the raw per-day features frame emitted by historical_backtest (pre-engine
integration). Unit-tested in tests/nfo/test_sentry_2022.py.

Usage:
  .venv/bin/python scripts/nfo/sentry_2022.py
"""
from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from datetime import timedelta

from nfo import universe
from nfo.client import DhanClient
from nfo.config import DATA_DIR, IST, RESULTS_DIR
from nfo.data import load_underlying_daily

import historical_backtest as hb  # type: ignore[import-not-found]  # scripts/nfo on sys.path via conftest

log = logging.getLogger("sentry_2022")

SENTRY_START = date(2022, 1, 1)
SENTRY_END = date(2022, 12, 31)
WARMUP_START = date(2021, 1, 1)     # 12-mo lookback for VIX 3-mo pct + IV rank
POSTROLL_END = date(2023, 2, 15)    # DTE overhead for Dec-2022 expiry chain

# V3 canonical thresholds — mirror scripts/nfo/historical_backtest.py
# and the V3 variant in scripts/nfo/redesign_variants.py:165.
_V3_MIN_SCORE = 4
_V3_IV_RV_MIN = -2.0
# V3's own event gate: {RBI, FOMC, BUDGET} within 10 days of entry. CPI
# is demoted to medium and does NOT block V3. This is narrower than the
# features parquet's s8_event column (which uses full-DTE + includes CPI).
_V3_EVENT_HIGH_KINDS = frozenset({"RBI", "FOMC", "BUDGET"})
_V3_EVENT_WINDOW_DAYS = 10


def _ensure_nifty_spot_cached(client: DhanClient | None) -> None:
    """Fetch and cache NIFTY daily bars covering the sentry window if not already present."""
    cache_path = DATA_DIR / "index" / f"NIFTY_{WARMUP_START.isoformat()}_{POSTROLL_END.isoformat()}.parquet"
    if cache_path.exists():
        log.info("NIFTY spot cache already covers sentry window: %s", cache_path.name)
        return
    if client is None:
        raise RuntimeError(
            "NIFTY spot cache missing for 2022 window and no Dhan client available. "
            "Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN to enable fetch."
        )
    log.info("Fetching NIFTY daily %s → %s …", WARMUP_START, POSTROLL_END)
    df = load_underlying_daily(
        client, universe.get("NIFTY"),
        from_date=WARMUP_START.isoformat(),
        to_date=POSTROLL_END.isoformat(),
    )
    # load_underlying_daily already persists via the cache module; defensive
    # explicit write so _load_nifty_daily sees a parquet under index/.
    if not cache_path.exists():
        df.to_parquet(cache_path, index=False)
    log.info("NIFTY spot cache: %d rows in %s", len(df), cache_path.name)


def _v3_event_ok(entry_date: date, events: list[tuple[date, str, str]]) -> bool:
    """V3's own event gate: True iff no high-severity event ({RBI, FOMC, BUDGET})
    falls in [entry_date, entry_date + 10 days]. CPI is explicitly ignored.
    Narrower than the features parquet's s8_event (which is full-DTE and includes CPI).
    """
    horizon = entry_date + timedelta(days=_V3_EVENT_WINDOW_DAYS)
    for d, _name, kind in events:
        if entry_date <= d <= horizon and kind in _V3_EVENT_HIGH_KINDS:
            return False
    return True


def v3_fire_mask(
    frame: pd.DataFrame,
    events: list[tuple[date, str, str]] | None = None,
) -> pd.Series:
    """Return a boolean Series indicating which rows would have fired V3.

    Frozen V3 rules (configs/nfo/strategies/v3_frozen.yaml +
    scripts/nfo/redesign_variants.py:165):
      - score (sum of s1..s7 bools treated as 0/1) >= 4
      - s3_iv_rv is True
      - s6_trend is True
      - event gate: no {RBI, FOMC, BUDGET} event in [entry, entry+10 days]
      - iv_minus_rv is NaN OR >= -2.0
      - at least one of s1_vix_abs, s2_vix_pct, s5_iv_rank is True

    Note: the parquet's `s8_event` column is NOT used because it covers a
    different window (full DTE) and a different kind set (includes CPI).

    `events` defaults to ``historical_backtest.HARD_EVENTS``. Tests pass an
    empty list to isolate the non-event logic.
    """
    if frame.empty:
        return pd.Series([], dtype=bool)

    if events is None:
        events = hb.HARD_EVENTS

    def _bool(col: str, default_if_missing: bool = False) -> pd.Series:
        if col not in frame.columns:
            return pd.Series(default_if_missing, index=frame.index)
        return frame[col].fillna(default_if_missing).astype(bool)

    s1 = _bool("s1_vix_abs")
    s2 = _bool("s2_vix_pct")
    s3 = _bool("s3_iv_rv")
    s4 = _bool("s4_pullback")
    s5 = _bool("s5_iv_rank")
    s6 = _bool("s6_trend")
    # s7_skew is NOT in the V3 score — see scripts/nfo/redesign_variants.py:258

    # Compute V3 event check per-row (requires date).
    if "date" in frame.columns:
        dates = pd.to_datetime(frame["date"]).dt.date
        event_ok = pd.Series(
            [_v3_event_ok(d, events) for d in dates],
            index=frame.index,
            dtype=bool,
        )
    else:
        event_ok = pd.Series(True, index=frame.index)

    # V3's canonical score: sum of {s1, s2, s3, s4, s5, s6, event_ok}. s7 is
    # excluded. See scripts/nfo/redesign_variants.py:258-259 — "passes"
    # includes s8 (= event_ok) but not s7_skew.
    score = (s1.astype(int) + s2.astype(int) + s3.astype(int) + s4.astype(int)
             + s5.astype(int) + s6.astype(int) + event_ok.astype(int))

    if "iv_minus_rv" in frame.columns:
        iv_rv = frame["iv_minus_rv"]
        iv_rv_ok = iv_rv.isna() | (iv_rv >= _V3_IV_RV_MIN)
    else:
        iv_rv_ok = pd.Series(True, index=frame.index)

    vol_any = s1 | s2 | s5

    return (score >= _V3_MIN_SCORE) & s3 & s6 & event_ok & vol_any & iv_rv_ok


def count_fire_cycles(frame: pd.DataFrame, fires: pd.Series) -> int:
    """Distinct monthly-expiry cycles in which V3 fired at least once.

    Fire-days that cluster on the same target expiry collapse to one cycle —
    because in cycle-matched/live-rule selection modes, multiple fire-days
    in the same expiry produce a single canonical trade. This is the right
    unit for comparison against the calibration's `filtered_trades`
    (redesign_winner.json), not the raw fire-day count.
    """
    if frame.empty or "target_expiry" not in frame.columns:
        return 0
    fire_rows = frame.loc[fires.values]
    expiries = fire_rows["target_expiry"].dropna().unique()
    return int(len(expiries))


def _summarise(frame: pd.DataFrame, fires: pd.Series) -> str:
    n = len(frame)
    n_fires = int(fires.sum())
    n_cycles = count_fire_cycles(frame, fires)
    years = max((frame["date"].max() - frame["date"].min()).days / 365.25, 1/365.25)
    lines = [
        "# 2022 NIFTY V3 Sentry Report",
        "",
        f"Window: {SENTRY_START} → {SENTRY_END}",
        f"Trading days evaluated: **{n}**",
        f"V3 fire-days: {n_fires} ({n_fires / n * 100:.1f}%, ≈ {n_fires / years:.1f}/yr)",
        f"V3 fire-**cycles** (distinct expiries): **{n_cycles}** "
        f"(≈ {n_cycles / years:.1f}/yr) — this is the decision unit.",
        "",
        "## Why cycles, not days",
        "",
        "V3 runs in `cycle_matched` or `live_rule` mode — multiple fire-days "
        "within the same monthly expiry collapse to one canonical trade. "
        "`results/nfo/redesign_winner.json` shows `filtered_trades: 10` over "
        "~1.96 calibration years = **~5.1 cycles/yr**. That is the prior to "
        "compare against, not the `firing_per_year: 11.71` figure (which is "
        "fire-days).",
        "",
        "## Decision framework (cycle units)",
        "",
        "- **Materially more** (>8 cycles/yr): 2022 regime was richer for V3 than calibration → "
        "expansion is worthwhile and may produce a larger-than-projected sample.",
        "- **About the same** (3-7 cycles/yr): research-only verdict stands; expansion still worthwhile.",
        "- **Materially less** (<3 cycles/yr): V3 may be overfit or mis-specified for high-event regimes → "
        "consider kill or redesign before spending on full backfill.",
        "",
        "## Per-signal pass counts",
        "",
        "| Signal | Pass | Fail | Unknown |",
        "|---|---:|---:|---:|",
    ]
    for sig in ("s1_vix_abs", "s2_vix_pct", "s3_iv_rv", "s4_pullback",
                "s5_iv_rank", "s6_trend", "s7_skew", "s8_event"):
        if sig not in frame.columns:
            continue
        p = int((frame[sig] == True).sum())  # noqa: E712
        f = int((frame[sig] == False).sum())  # noqa: E712
        u = int(frame[sig].isna().sum())
        lines.append(f"| {sig} | {p} | {f} | {u} |")

    lines += ["", "## V3 fire days (full list)", ""]
    if n_fires == 0:
        lines.append("_No V3 fires in 2022._")
    else:
        fire_rows = frame[fires.values].sort_values("date")
        lines.append("| Date | Spot | VIX | IV-RV | Trend | Events |")
        lines.append("|---|---:|---:|---:|:---:|---|")
        for _, r in fire_rows.iterrows():
            ev = (r.get("events_in_window") or "")[:50]
            lines.append(
                f"| {r['date']} "
                f"| ₹{r.get('spot', float('nan')):,.0f} "
                f"| {r.get('vix', float('nan')):.1f} "
                f"| {r.get('iv_minus_rv', float('nan')):+.1f}pp "
                f"| {int(r.get('trend_score', 0))} "
                f"| {ev} |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Primary-sourced event backfill merges into HARD_EVENTS at import time,
    # so 2022 V3 s8_event is computed against the full sourced calendar.
    log.info("HARD_EVENTS has %d entries covering %s .. %s",
             len(hb.HARD_EVENTS),
             min(d for d, _, _ in hb.HARD_EVENTS).isoformat(),
             max(d for d, _, _ in hb.HARD_EVENTS).isoformat())

    client: DhanClient | None = None
    try:
        client = DhanClient()
    except Exception as exc:
        log.warning("Dhan client unavailable (%s); proceeding cache-only", exc)

    _ensure_nifty_spot_cached(client)

    log.info("Running backtest over %s → %s", SENTRY_START, SENTRY_END)
    frame = hb.run_backtest(SENTRY_START, SENTRY_END, pull_calls=False)

    fires = v3_fire_mask(frame)

    out_dir = RESULTS_DIR / "audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = out_dir / "sentry_2022_signals.parquet"
    report_path = out_dir / "sentry_2022_report.md"
    frame.to_parquet(parquet_path, index=False)
    report = _summarise(frame, fires)
    report_path.write_text(report, encoding="utf-8")

    print(report)
    log.info("Wrote %s and %s", parquet_path, report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
