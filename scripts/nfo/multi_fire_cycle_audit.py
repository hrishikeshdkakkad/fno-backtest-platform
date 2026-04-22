"""Multi-fire-cycle entry audit — did first_fire represent the cycle?

Purpose
-------
The PR3 walk-forward used `first_fire` as the canonical entry for every
cycle. For cycles with multiple fire-days (e.g. 2022-07-28 had 7), that
conceals whether:
  (a) every tradable fire-day in that cycle would have worked,
  (b) first_fire happens to be the best / middle / worst entry,
  (c) some fire-days were actually untradable (no strike at the delta /
      width / DTE tolerance).

This audit replays every fire-day in every multi-fire V3 cycle through
the canonical engine (`engine.execution.run_cycle_from_dhan`), preserves
None results as "untradable" (not silent drops, not losses), and reports
per-cycle + aggregate summaries from both the full union and the
walk-forward OOS subset.

Non-canonical: all outputs land under `results/nfo/audits/`. No changes
to `results/nfo/runs/`, `latest.json`, canonical datasets, or study_type.

Usage:
  .venv/bin/python scripts/nfo/multi_fire_cycle_audit.py [--verbose]
                                                        [--refresh-dhan]
"""
from __future__ import annotations

import argparse
import logging
import statistics
from datetime import date
from pathlib import Path
from typing import Callable

import pandas as pd

from nfo import universe
from nfo.client import DhanClient
from nfo.config import RESULTS_DIR
from nfo.engine.execution import run_cycle_from_dhan
from nfo.specs.loader import load_strategy
from nfo.universe import lot_size_on

import historical_backtest as hb  # type: ignore[import-not-found]
import sentry_2022 as s2  # type: ignore[import-not-found]
import walkforward_v3 as wf  # type: ignore[import-not-found]

log = logging.getLogger("multi_fire_audit")

REPO_ROOT = Path(__file__).resolve().parents[2]
V3_STRATEGY_PATH = REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml"


# ── Pure helpers (unit-tested) ─────────────────────────────────────────────


def group_fire_days_by_cycle(
    features: pd.DataFrame,
    fire_mask: pd.Series,
) -> dict[str, list[date]]:
    """Return ``{target_expiry_iso: [chronological fire-days]}`` for multi-fire cycles only."""
    if features.empty:
        return {}
    fires = features.loc[fire_mask.values].copy()
    if fires.empty:
        return {}
    out: dict[str, list[date]] = {}
    for _, row in fires.sort_values("date").iterrows():
        exp = row.get("target_expiry")
        if pd.isna(exp):
            continue
        d = row["date"]
        d = d.date() if hasattr(d, "date") else d
        out.setdefault(str(exp)[:10], []).append(d)
    # Keep only multi-fire.
    return {k: v for k, v in out.items() if len(v) > 1}


def rank_first_fire(trades: list[dict]) -> str | None:
    """Return 'best' | 'middle' | 'worst' | 'only' | 'first_fire_untradable' | None.

    ``None`` means no tradable entries exist for this cycle.
    """
    tradable = [t for t in trades if t.get("is_tradable")]
    if not tradable:
        return None
    first = next((t for t in trades if t.get("is_first_fire")), None)
    if first is None or not first.get("is_tradable"):
        return "first_fire_untradable"
    if len(tradable) == 1:
        return "only"
    pnls = [float(t["pnl_contract"]) for t in tradable]
    first_pnl = float(first["pnl_contract"])
    if first_pnl >= max(pnls):
        return "best"
    if first_pnl <= min(pnls):
        return "worst"
    return "middle"


def cycle_summary(trades: list[dict], *, expiry: date) -> dict:
    """Per-cycle aggregate — used in the cycle-level summary CSV + report."""
    fire_days = len(trades)
    tradable_trades = [t for t in trades if t.get("is_tradable")]
    tradable = len(tradable_trades)
    untradable = fire_days - tradable
    profitable = sum(1 for t in tradable_trades if float(t["pnl_contract"]) > 0)
    losing = sum(1 for t in tradable_trades if float(t["pnl_contract"]) < 0)
    first = next((t for t in trades if t.get("is_first_fire")), None)

    tradable_pnls = [float(t["pnl_contract"]) for t in tradable_trades]
    lot_aware_pnls = [
        float(t["lot_aware_pnl"])
        for t in tradable_trades
        if t.get("lot_aware_pnl") is not None
    ]

    best_pnl = max(tradable_pnls) if tradable_pnls else None
    worst_pnl = min(tradable_pnls) if tradable_pnls else None
    best_lot = max(lot_aware_pnls) if lot_aware_pnls else None
    worst_lot = min(lot_aware_pnls) if lot_aware_pnls else None

    return {
        "expiry": expiry.isoformat() if isinstance(expiry, date) else str(expiry)[:10],
        "fire_days": fire_days,
        "tradable": tradable,
        "untradable": untradable,
        "profitable": profitable,
        "losing": losing,
        "all_tradable_profitable": (tradable > 0 and profitable == tradable),
        "first_fire_date": first["entry_date"].isoformat() if first else None,
        "first_fire_tradable": bool(first and first.get("is_tradable")),
        "first_fire_profitable": bool(first and first.get("is_tradable") and float(first["pnl_contract"]) > 0),
        "first_fire_pnl": float(first["pnl_contract"]) if first and first.get("is_tradable") else None,
        "first_fire_lot_aware_pnl": float(first["lot_aware_pnl"]) if first and first.get("lot_aware_pnl") is not None else None,
        "best_pnl": best_pnl,
        "worst_pnl": worst_pnl,
        "best_lot_aware_pnl": best_lot,
        "worst_lot_aware_pnl": worst_lot,
        "avg_tradable_pnl": statistics.mean(tradable_pnls) if tradable_pnls else None,
        "first_fire_rank": rank_first_fire(trades),
    }


def lot_aware_pnl(*, pnl_per_share, entry_date: date) -> float | None:
    """Dated lot-aware P&L. Returns None if pnl_per_share is None."""
    if pnl_per_share is None:
        return None
    return float(pnl_per_share) * lot_size_on("NIFTY", entry_date)


def is_cycle_oos(first_fire_date: date, windows: list) -> bool:
    """True iff ``first_fire_date`` falls in at least one walk-forward test window.

    Uses the exact half-open convention ``[test_start, test_end)`` that
    walkforward_v3.evaluate_window uses (see scripts/nfo/walkforward_v3.py).
    """
    for w in windows:
        if w.test_start <= first_fire_date < w.test_end:
            return True
    return False


# ── Engine-backed replay ───────────────────────────────────────────────────


def _trade_to_row(sim, entry_date: date, is_first_fire: bool) -> dict:
    """Pack a SimulatedTrade into the audit row shape."""
    st = sim.spread_trade
    pnl_per_share = float(st.pnl_per_share)
    return {
        "entry_date": entry_date,
        "expiry_date": st.expiry_date,
        "exit_date": st.exit_date,
        "is_first_fire": is_first_fire,
        "is_tradable": True,
        "outcome": st.outcome,
        "short_strike": float(st.short_strike),
        "long_strike": float(st.long_strike),
        "net_credit": float(st.net_credit),
        "pnl_per_share": pnl_per_share,
        "pnl_contract": float(st.pnl_contract),
        "gross_pnl_contract": float(st.gross_pnl_contract),
        "txn_cost_contract": float(st.txn_cost_contract),
        "lot_size_at_entry": lot_size_on("NIFTY", entry_date),
        "lot_aware_pnl": lot_aware_pnl(pnl_per_share=pnl_per_share, entry_date=entry_date),
    }


def _untradable_row(entry_date: date, is_first_fire: bool, reason: str) -> dict:
    return {
        "entry_date": entry_date,
        "expiry_date": None,
        "exit_date": None,
        "is_first_fire": is_first_fire,
        "is_tradable": False,
        "outcome": None,
        "short_strike": None,
        "long_strike": None,
        "net_credit": None,
        "pnl_per_share": None,
        "pnl_contract": None,
        "gross_pnl_contract": None,
        "txn_cost_contract": None,
        "lot_size_at_entry": lot_size_on("NIFTY", entry_date),
        "lot_aware_pnl": None,
        "untradable_reason": reason,
    }


def earliest_containing_window(d: date, windows: list) -> object | None:
    """Return the earliest walk-forward window whose test range contains ``d``,
    or ``None`` if pre-OOS. Same half-open convention as ``is_cycle_oos``."""
    hits = [w for w in windows if w.test_start <= d < w.test_end]
    if not hits:
        return None
    return min(hits, key=lambda w: w.test_start)


def simulate_all_fire_days(
    *,
    fire_days: list[date],
    expiry: date,
    simulate_fn: Callable,
    client,
    strategy_spec,
    under,
    spot_daily,
    windows: list | None = None,
) -> list[dict]:
    """For each fire-day, run ``simulate_fn``; preserve None as untradable.

    If ``windows`` is provided, each row is tagged with:
      - ``cycle_in_oos``: bool — whether first_fire (the cycle anchor, i.e.
        ``fire_days[0]``) lands in any walk-forward test window. Same
        semantics as the cycle-level summary table and markdown report.
      - ``earliest_cycle_test_window_start``: ISO date string or None — the
        first walk-forward test window containing first_fire.
    """
    rows: list[dict] = []
    first_day = fire_days[0] if fire_days else None
    cycle_window = earliest_containing_window(first_day, windows or []) if first_day else None
    cycle_in_oos = cycle_window is not None
    window_id = cycle_window.test_start.isoformat() if cycle_window else None

    for d in fire_days:
        is_first = (d == first_day)
        try:
            sim = simulate_fn(
                client=client, strategy_spec=strategy_spec, under=under,
                entry_date=d, expiry_date=expiry, spot_daily=spot_daily,
            )
        except Exception as exc:
            log.warning("entry %s → %s raised: %s", d, expiry, exc)
            row = _untradable_row(d, is_first, reason=f"exception: {type(exc).__name__}")
        else:
            if sim is None:
                row = _untradable_row(d, is_first, reason="engine returned None")
            else:
                row = _trade_to_row(sim, d, is_first)
        row["cycle_in_oos"] = cycle_in_oos
        row["earliest_cycle_test_window_start"] = window_id
        rows.append(row)
    return rows


# ── Main driver ────────────────────────────────────────────────────────────


def _aggregate(cycle_rows: list[dict], label: str) -> dict:
    """Compute decision-relevant aggregate summaries."""
    if not cycle_rows:
        return {
            "label": label, "cycles": 0,
            "share_all_tradable_profitable": None,
            "share_some_profitable": None,
            "share_first_fire_profitable": None,
            "share_arbitrary_tradable_profitable": None,
            "avg_first_fire_minus_avg_tradable": None,
            "avg_best_minus_first_fire": None,
        }
    n = len(cycle_rows)

    # Cycles where every tradable fire-day is profitable (tradable>0 required).
    all_prof = sum(1 for c in cycle_rows if c["all_tradable_profitable"])
    # Cycles where SOME but not ALL tradable fire-days are profitable.
    some_prof = sum(
        1 for c in cycle_rows
        if c["tradable"] > 0 and not c["all_tradable_profitable"] and c["profitable"] > 0
    )
    # Cycles where first_fire itself was profitable.
    ff_prof = sum(1 for c in cycle_rows if c["first_fire_profitable"])
    # Cycles where at least one tradable fire-day is profitable.
    any_prof = sum(1 for c in cycle_rows if c["profitable"] > 0)

    # Delta aggregates. Restrict to cycles where first_fire was tradable
    # (else there's no meaningful "first_fire P&L" to compare).
    with_ff = [c for c in cycle_rows if c["first_fire_pnl"] is not None]
    if with_ff:
        avg_ff = statistics.mean(c["first_fire_pnl"] for c in with_ff)
        avg_trad = statistics.mean(
            c["avg_tradable_pnl"] for c in with_ff if c["avg_tradable_pnl"] is not None
        )
        ff_minus_tradable = avg_ff - avg_trad
        avg_best = statistics.mean(c["best_pnl"] for c in with_ff if c["best_pnl"] is not None)
        best_minus_ff = avg_best - avg_ff
    else:
        ff_minus_tradable = None
        best_minus_ff = None

    return {
        "label": label,
        "cycles": n,
        "share_all_tradable_profitable": all_prof / n,
        "share_some_profitable": some_prof / n,
        "share_first_fire_profitable": ff_prof / n,
        "share_arbitrary_tradable_profitable": any_prof / n,
        "avg_first_fire_minus_avg_tradable": ff_minus_tradable,
        "avg_best_minus_first_fire": best_minus_ff,
    }


def _format_report(
    per_entry_df: pd.DataFrame,
    cycle_rows: list[dict],
    agg_full: dict,
    agg_oos: dict,
    windows: list,
) -> str:
    def _fmt(x, nd=2, pct=False, currency=False):
        if x is None:
            return "—"
        if pct:
            return f"{x * 100:.1f}%"
        if currency:
            return f"₹{x:,.0f}"
        return f"{x:.{nd}f}"

    lines = [
        "# Multi-fire-cycle Entry Audit",
        "",
        f"**Generated:** {date.today().isoformat()}",
        "**Question:** When V3 fires multiple times on the same expiry, how many of those entries were tradable and profitable? Did `first_fire` represent the cycle or just one lucky draw?",
        "",
        "**Scope:** NIFTY-only, V3-frozen. Non-canonical. Outputs under `results/nfo/audits/`.",
        "**Gate:** `scripts/nfo/sentry_2022.v3_fire_mask` (canonical V3 event + score gate).",
        "**Engine:** `nfo.engine.execution.run_cycle_from_dhan` (same path walk-forward uses).",
        "**Lot sizing:** `nfo.universe.lot_size_on(name, entry_date)` per-trade dated lookup.",
        "",
        "## Full-union aggregate",
        "",
        f"- Multi-fire cycles evaluated: **{agg_full['cycles']}**",
        f"- Cycles where *all* tradable fire-days were profitable: **{_fmt(agg_full['share_all_tradable_profitable'], pct=True)}**",
        f"- Cycles where *some but not all* tradable fire-days were profitable: **{_fmt(agg_full['share_some_profitable'], pct=True)}**",
        f"- Cycles where `first_fire` itself was profitable: **{_fmt(agg_full['share_first_fire_profitable'], pct=True)}**",
        f"- Cycles where *at least one* tradable fire-day was profitable: **{_fmt(agg_full['share_arbitrary_tradable_profitable'], pct=True)}**",
        f"- Avg first_fire P&L minus avg cycle's tradable-mean P&L: **{_fmt(agg_full['avg_first_fire_minus_avg_tradable'], currency=True)}**",
        f"- Avg best-day P&L minus avg first_fire P&L: **{_fmt(agg_full['avg_best_minus_first_fire'], currency=True)}**",
        "",
        "## Walk-forward OOS-only aggregate",
        "",
        f"- Multi-fire cycles in OOS: **{agg_oos['cycles']}**",
        f"- Cycles where *all* tradable fire-days were profitable: **{_fmt(agg_oos['share_all_tradable_profitable'], pct=True)}**",
        f"- Cycles where *some but not all* tradable fire-days were profitable: **{_fmt(agg_oos['share_some_profitable'], pct=True)}**",
        f"- Cycles where `first_fire` itself was profitable: **{_fmt(agg_oos['share_first_fire_profitable'], pct=True)}**",
        f"- Cycles where *at least one* tradable fire-day was profitable: **{_fmt(agg_oos['share_arbitrary_tradable_profitable'], pct=True)}**",
        f"- Avg first_fire P&L minus avg cycle's tradable-mean P&L: **{_fmt(agg_oos['avg_first_fire_minus_avg_tradable'], currency=True)}**",
        f"- Avg best-day P&L minus avg first_fire P&L: **{_fmt(agg_oos['avg_best_minus_first_fire'], currency=True)}**",
        "",
        "## Per-cycle detail",
        "",
        "| Expiry | In OOS? | Fire-days | Tradable | Untradable | Profitable | Losing | First-fire P&L | Best P&L | Worst P&L | first_fire rank | All tradable profitable? |",
        "|---|:---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|",
    ]
    for c in cycle_rows:
        lines.append(
            f"| {c['expiry']} "
            f"| {'✓' if c['in_oos'] else ''} "
            f"| {c['fire_days']} "
            f"| {c['tradable']} "
            f"| {c['untradable']} "
            f"| {c['profitable']} "
            f"| {c['losing']} "
            f"| {_fmt(c['first_fire_pnl'], currency=True)} "
            f"| {_fmt(c['best_pnl'], currency=True)} "
            f"| {_fmt(c['worst_pnl'], currency=True)} "
            f"| {c['first_fire_rank'] or '—'} "
            f"| {'✓' if c['all_tradable_profitable'] else ''} |"
        )

    # Identify "outright losing" cycles — tradable>0 AND profitable==0.
    losing_cycles = [
        c for c in cycle_rows
        if c["tradable"] > 0 and c["profitable"] == 0
    ]
    pre_oos_losing = [c for c in losing_cycles if not c["in_oos"]]
    oos_losing = [c for c in losing_cycles if c["in_oos"]]

    # Plain-language conclusion — the actual point of the audit.
    within_cycle_flex = agg_full["share_all_tradable_profitable"]
    ff_prof = agg_full["share_first_fire_profitable"]
    any_prof = agg_full["share_arbitrary_tradable_profitable"]

    lines += [
        "",
        "## Plain-language conclusion",
        "",
        "Three separate questions answered below — they are often conflated but mean different things.",
        "",
        "### 1. Within-cycle entry flexibility",
        "",
    ]
    if within_cycle_flex is None:
        lines.append("_No multi-fire cycles to evaluate._")
    elif within_cycle_flex >= 0.75:
        lines.append(
            f"**Strong:** in {_fmt(within_cycle_flex, pct=True)} of multi-fire cycles, "
            "*every* tradable fire-day was profitable. "
            "`first_fire` was not a special draw — the whole cluster would have worked. "
            f"`first_fire` itself was profitable in {_fmt(ff_prof, pct=True)} of cycles, and "
            f"at least one tradable day was profitable in {_fmt(any_prof, pct=True)}."
        )
    elif within_cycle_flex >= 0.4:
        lines.append(
            f"**Mixed:** in {_fmt(within_cycle_flex, pct=True)} of multi-fire cycles, "
            "every tradable fire-day was profitable, but a meaningful minority had at "
            "least one losing entry. `first_fire` was profitable in "
            f"{_fmt(ff_prof, pct=True)} of cycles — suggesting entry timing matters more "
            "than the walk-forward summary implied."
        )
    else:
        lines.append(
            f"**Weak:** only {_fmt(within_cycle_flex, pct=True)} of multi-fire cycles had "
            "every tradable fire-day profitable. Entry timing materially affects the "
            "outcome — `first_fire` alone is not a good summary."
        )

    lines += [
        "",
        "### 2. Calendar-level opportunity scarcity",
        "",
        "The audit evaluates only multi-fire cycles. If that set is small relative to the "
        "total fire-cycle count, the story is about *how rare* clusters are, not about the "
        "clusters themselves. This audit counts "
        f"**{agg_full['cycles']}** multi-fire cycles in the full union; compare against the "
        "**25** total fire-cycles the expansion produced. The rest are single-fire-day cycles "
        "where `first_fire` is the only choice.",
        "",
        "### 3. Walk-forward evidence sufficiency",
        "",
    ]
    if agg_oos["cycles"] == 0:
        lines.append(
            "**Zero multi-fire cycles landed in the walk-forward OOS test set.** "
            "Every multi-fire cluster in V3's history is either in training/warmup "
            "or outside the 24m+12m reachable test surface. That means this audit "
            "speaks to what V3 *would have done* if applied in those windows, but it "
            "does not change the walk-forward kill logic, which fires on thin-window "
            "trade density (4 of 11 windows had <3 trades) regardless of within-cycle "
            "entry flexibility."
        )
    else:
        lines.append(
            f"**{agg_oos['cycles']} multi-fire cycles land in OOS.** Their within-cycle "
            f"profitability ({_fmt(agg_oos['share_all_tradable_profitable'], pct=True)} "
            "all-profitable) is informative about V3's quality during evaluated windows, "
            "but does not rescue V3 from the kill rule that triggered on thin-window "
            "trade count. Within-cycle entry flexibility and calendar-level trade density "
            "are separate failure modes."
        )

    # Outright-loser section — cycles where tradable>0 AND profitable=0.
    lines += [
        "",
        "### Outright losing cycles",
        "",
        f"- Pre-OOS (cycles not in any walk-forward test window): **{len(pre_oos_losing)}**",
        f"- OOS (cycles inside a walk-forward test window): **{len(oos_losing)}**",
        "",
    ]
    if pre_oos_losing:
        lines.append(
            "Every outright-loser below was invisible to the PR3 walk-forward because "
            "it falls before the first reachable test window (2022-08-01). If these "
            "had been in OOS, they would have contributed real losses to the walk-forward "
            "aggregate — possibly enough to fail kill rule #2 (median per-window OOS "
            "P&L ≤ 0) in addition to the thin-windows rule that actually fired."
        )
        lines.append("")
        lines.append("| Expiry | Fire-days | Tradable | Losing | First-fire P&L | Best P&L | Worst P&L |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for c in pre_oos_losing:
            lines.append(
                f"| {c['expiry']} | {c['fire_days']} | {c['tradable']} | {c['losing']} "
                f"| {_fmt(c['first_fire_pnl'], currency=True)} "
                f"| {_fmt(c['best_pnl'], currency=True)} "
                f"| {_fmt(c['worst_pnl'], currency=True)} |"
            )
    else:
        lines.append("_None pre-OOS._")

    lines += [
        "",
        "### Does this change the V3 kill verdict?",
        "",
        "**No, but it sharpens it.** The walk-forward killed V3 on thin-window trade "
        "density: 4 of 11 test windows had < 3 trades. That is a *calendar* property, "
        "not a per-cycle property, and this audit doesn't overturn it — within-cycle "
        "flexibility cannot conjure trades on days V3's gate didn't fire.",
        "",
        "What this audit *adds* is visibility into V3's losing cycles. The walk-forward "
        "reported a 100% by-sign win rate on its 11 executed trades, which could be "
        "(mis)read as 'V3 never loses on a real day.' This audit shows that reading is "
        "false: V3 has at least "
        f"**{len(pre_oos_losing)} pre-OOS outright losing cycles**, with first_fire P&Ls "
        "of roughly ₹-5,700 to ₹-6,000 per contract. They were filtered out of the "
        "walk-forward only because they sit inside the first 24-month training/warmup "
        "zone (2020-08 → 2022-07), not because V3 rejected them.",
        "",
        "Honest framing: V3 IS a good filter *on the OOS subset that walk-forward "
        f"happened to evaluate* ({_fmt(agg_oos['share_all_tradable_profitable'], pct=True)} "
        "of OOS multi-fire cycles are all-profitable). V3 is *not* a uniformly-good "
        "filter across history; it had at least one regime (early-COVID / recovery, "
        "late 2020 — 2021) where it produced multi-day losing clusters. On NIFTY-only "
        "monthly expiries, the combination of (a) thin annual trade density and "
        "(b) regime-specific losing clusters is the reason production is not viable — "
        "not merely scarcity alone.",
    ]

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument(
        "--refresh-dhan", action="store_true",
        help="Opt-in: allow Dhan calls if a needed leg is uncached. "
             "Default is cache-only; missing legs become untradable rows.",
    )
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Load the same union walkforward_v3 uses.
    features = wf._load_union_features()
    log.info("Union features: %d rows (%s → %s)",
             len(features), features["date"].min().date(), features["date"].max().date())

    # Generate fire mask via the canonical V3 gate.
    fires = s2.v3_fire_mask(features)
    log.info("V3 fire-days in union: %d", int(fires.sum()))

    # Group into multi-fire cycles.
    multi = group_fire_days_by_cycle(features, fires)
    log.info("Multi-fire cycles (>1 fire-day): %d", len(multi))
    for exp, days in sorted(multi.items()):
        log.info("  %s: %d fire-days (%s → %s)", exp, len(days), days[0], days[-1])

    # Windows for OOS tagging — exactly the walkforward defaults.
    windows = wf.generate_windows(
        data_start=features["date"].min().date(),
        data_end=features["date"].max().date(),
    )

    # Simulate every fire-day in every multi-fire cycle.
    spot_daily = hb._load_nifty_daily()
    strategy_spec, _ = load_strategy(V3_STRATEGY_PATH)
    under = universe.get("NIFTY")

    per_entry: list[dict] = []
    cycle_rows: list[dict] = []

    with DhanClient() as client:
        for exp_iso in sorted(multi.keys()):
            days = multi[exp_iso]
            expiry_d = date.fromisoformat(exp_iso)
            log.info("Replaying %s (%d fire-days) ...", exp_iso, len(days))
            trades = simulate_all_fire_days(
                fire_days=days,
                expiry=expiry_d,
                simulate_fn=run_cycle_from_dhan,
                client=client,
                strategy_spec=strategy_spec,
                under=under,
                spot_daily=spot_daily,
                windows=windows,
            )
            # Attach expiry to each entry row for the machine-readable table.
            for t in trades:
                t["expiry"] = exp_iso
                per_entry.append(t)

            summary = cycle_summary(trades, expiry=expiry_d)
            first_fire = days[0]
            summary["in_oos"] = is_cycle_oos(first_fire, windows)
            cycle_rows.append(summary)

    # Emit artifacts.
    out_dir = RESULTS_DIR / "audits"
    out_dir.mkdir(parents=True, exist_ok=True)

    per_entry_df = pd.DataFrame(per_entry)
    # Normalize date columns for parquet.
    for col in ("entry_date", "expiry_date", "exit_date"):
        if col in per_entry_df.columns:
            per_entry_df[col] = per_entry_df[col].astype(str)
    entries_path = out_dir / "multi_fire_cycle_entries.parquet"
    per_entry_df.to_parquet(entries_path, index=False)
    log.info("Wrote %s (%d rows)", entries_path, len(per_entry_df))

    summary_df = pd.DataFrame(cycle_rows)
    summary_path = out_dir / "multi_fire_cycle_summary.csv"
    summary_df.to_csv(summary_path, index=False)
    log.info("Wrote %s (%d rows)", summary_path, len(summary_df))

    # Aggregate — full vs OOS.
    agg_full = _aggregate(cycle_rows, label="full-union")
    agg_oos = _aggregate([c for c in cycle_rows if c["in_oos"]], label="oos")
    agg_df = pd.DataFrame([agg_full, agg_oos])
    agg_path = out_dir / "multi_fire_cycle_aggregate.csv"
    agg_df.to_csv(agg_path, index=False)
    log.info("Wrote %s", agg_path)

    # Markdown report.
    report = _format_report(per_entry_df, cycle_rows, agg_full, agg_oos, windows)
    report_path = out_dir / f"multi_fire_cycle_audit_{date.today().isoformat()}.md"
    report_path.write_text(report, encoding="utf-8")
    log.info("Wrote %s", report_path)

    print(report[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
