"""V3 falsification battery — realism tests against the frozen spec.

Runs the five realism tests requested (in order):

  1. Tail-loss injection — replace N random matched cycles with synthetic
     max-loss trades; measure survival under each injection count.
  2. Capital allocation sweep — run 10 %, 20 %, 30 %, 50 %, 100 %
     per-cycle deployment and report CAGR + max DD per slice.
  3. Exit sweep — delegated to `exit_sweep_backtest.py` when fresh
     backtests are required; this script only summarises the resulting
     CSV if it exists.
  4. Walk-forward tuning — rolling train window grid-searches against
     `nfo.calibrate.grid_search_thresholds`, tests on the held-out
     window, and reports whether the tuned combo generalises.
  5. Entry perturbation — delegated to `entry_perturbation_backtest.py`
     for the same reason as (3); summarised here.

All tests honour `docs/v3-spec-frozen.md` — no thresholds, cost constants,
or match rules change inside this script.

P5-E2: matched-trade selection + walk-forward both route through
`nfo.studies.falsification.run_falsification` (engine-backed); the legacy
`run_tail_loss_injection`, `run_allocation_sweep`, `run_walk_forward`
helpers below are kept so the 4 legacy artifacts (`falsify_tail_loss.csv`,
`falsify_allocation.csv`, `falsify_walkforward.csv`, `falsification_report.md`)
preserve their wide multi-variant schemas.
"""
from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nfo import calibrate
from nfo.config import RESULTS_DIR, ROOT
from nfo.robustness import (
    compute_equity_curves,
    inject_tail_losses,
    load_trades_with_gaps,
)
from nfo.specs.loader import load_strategy
from nfo.studies.falsification import run_falsification

log = logging.getLogger("v3_falsification")

SIGNALS_PATH = RESULTS_DIR / "historical_signals.parquet"
EXIT_SWEEP_CSV = RESULTS_DIR / "exit_sweep_trades.csv"
ENTRY_PERT_CSV = RESULTS_DIR / "entry_perturbation_trades.csv"
VARIANTS = ("pt50", "hte")
_HERE = Path(__file__).resolve().parent


def _load_rv_module(alias: str = "_legacy_rv_falsification"):
    """Import `scripts/nfo/redesign_variants.py` directly (scripts/ isn't a
    package). Used for the legacy event-resolver + cached-parquet ATR loader.
    """
    spec = importlib.util.spec_from_file_location(
        alias, _HERE / "redesign_variants.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


def _format_inr(x: float) -> str:
    sign = "-" if x < 0 else ""
    v = abs(float(x))
    if v >= 1_00_00_000:
        return f"{sign}₹{v / 1_00_00_000:.2f}Cr"
    if v >= 1_00_000:
        return f"{sign}₹{v / 1_00_000:.2f}L"
    return f"{sign}₹{v:,.0f}"


# ── 1. Tail-loss injection ──────────────────────────────────────────────────


def run_tail_loss_injection(
    matched_by_variant: dict[str, pd.DataFrame],
    *,
    capital: float,
    years: float,
    injection_counts: list[int],
    n_iter: int,
    seed: int,
) -> pd.DataFrame:
    """For each (variant, injection_count), resample the matched trades with
    replacement N times, replace `injection_count` rows with synthetic max
    losses on each draw, and tabulate the outcome distribution.

    The key question: how many of the 8 "wins" need to actually have been
    max-loss cycles before V3's compounding account goes negative?
    """
    rows = []
    for variant, trades in matched_by_variant.items():
        for k in injection_counts:
            rng = np.random.default_rng(seed + k)
            totals_fixed = np.empty(n_iter)
            finals_compound = np.empty(n_iter)
            dds = np.empty(n_iter)
            for i in range(n_iter):
                # Resample with replacement (same as block bootstrap) then
                # replace `k` positions with synthetic max losses. The
                # combined sample has the same cardinality and the same
                # ordering semantics as the real matched set.
                idx = rng.integers(0, len(trades), size=len(trades))
                sampled = trades.iloc[idx].reset_index(drop=True)
                injected = inject_tail_losses(sampled, n_injections=k, rng=rng)
                eq = compute_equity_curves(
                    injected, capital=capital, years=years,
                )
                totals_fixed[i] = eq.total_pnl_fixed
                finals_compound[i] = eq.final_equity_compound
                dds[i] = eq.max_drawdown_pct
            rows.append({
                "variant": variant,
                "n_injected": k,
                "n_iter": n_iter,
                "p_final_above_capital": float((finals_compound > capital).mean()),
                "p5_final_equity": float(np.percentile(finals_compound, 5)),
                "p50_final_equity": float(np.percentile(finals_compound, 50)),
                "p95_final_equity": float(np.percentile(finals_compound, 95)),
                "median_max_dd_pct": float(np.median(dds)),
                "p95_max_dd_pct": float(np.percentile(dds, 95)),
                "p50_total_fixed": float(np.percentile(totals_fixed, 50)),
            })
    return pd.DataFrame(rows)


# ── 2. Capital allocation sweep ─────────────────────────────────────────────


def run_allocation_sweep(
    matched_by_variant: dict[str, pd.DataFrame],
    *,
    capital: float,
    years: float,
    deployment_fracs: list[float],
) -> pd.DataFrame:
    """Run the deterministic equity walk (no resampling) at each deployment
    fraction. This isolates the effect of partial deployment from the
    effect of sample noise.
    """
    rows = []
    for variant, trades in matched_by_variant.items():
        for frac in deployment_fracs:
            eq = compute_equity_curves(
                trades, capital=capital, years=years, deployment_frac=frac,
            )
            rows.append({
                "variant": variant,
                "deployment_frac": frac,
                "total_pnl_fixed": eq.total_pnl_fixed,
                "total_pnl_compound": eq.total_pnl_compound,
                "final_equity_compound": eq.final_equity_compound,
                "cagr_compound_pct": eq.annualised_pct_compound,
                "max_drawdown_pct": eq.max_drawdown_pct,
                "sharpe": eq.sharpe,
            })
    return pd.DataFrame(rows)


# ── 4. Walk-forward tuning ──────────────────────────────────────────────────


def run_walk_forward(
    signals_df: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    windows: list[tuple[str, str, str, str]],
    pt_variant: str = "hte",
) -> pd.DataFrame:
    """Test the FROZEN V3 rule on rolling train/test windows.

    Routes through `nfo.studies.falsification.run_falsification` so the
    trigger + cycle + trade-matching path is the engine's. For each window
    we slice `signals_df` to the window's date range, call the study, and
    read `matched_trades` back; `summary_stats` is taken from
    `baseline_stats`. The window metadata + per-lot Sharpe convention
    (√252 annualisation via `calibrate.summary_stats`) are preserved so
    the legacy CSV + markdown tables stay byte-identical.

    For each window we compute summary stats on the train-period fires
    and on the test-period fires. If a V3 rule that worked in-sample
    also works out-of-sample, train and test Sharpe should be similar in
    sign/magnitude.
    """
    # Import via `sys.path` (not `importlib.util.spec_from_file_location`) so
    # `monkeypatch.setattr(rv, ...)` in unit tests lands on the same module
    # instance this function reads; the engine's trigger-eval + matching
    # path reads `atr_series` and `_event_pass` through the captured `rv`.
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    import redesign_variants as rv  # noqa: E402 — scripts/ dir injected above
    spec, _ = load_strategy(ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml")

    signals_df = signals_df.copy()
    signals_df["date"] = pd.to_datetime(signals_df["date"])
    atr_series = rv.load_nifty_atr(signals_df["date"])

    def _event_resolver(entry, dte):
        return "high" if not rv._event_pass(
            entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"},
            window_days=10,
        ) else "none"

    def _matched_in_window(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        slice_signals = signals_df[
            (signals_df["date"] >= start) & (signals_df["date"] <= end)
        ].copy()
        if slice_signals.empty:
            return pd.DataFrame()
        result = run_falsification(
            spec=spec, features_df=slice_signals, atr_series=atr_series,
            trades_df=trades, pt_variant=pt_variant,
            capital_inr=1_000_000, years=max((end - start).days / 365.25, 1e-9),
            # The per-window study only needs matched_trades; drop the
            # (expensive) Monte-Carlo + sweep sections by setting them empty.
            tail_loss_injections=[], tail_loss_iterations=0,
            allocation_fractions=[], walkforward_folds=0,
            event_resolver=_event_resolver, seed=42,
        )
        return result.matched_trades

    rows = []
    for train_start, train_end, test_start, test_end in windows:
        ts_train_start = pd.Timestamp(train_start)
        ts_train_end = pd.Timestamp(train_end)
        ts_test_start = pd.Timestamp(test_start)
        ts_test_end = pd.Timestamp(test_end)
        train_matched = _matched_in_window(ts_train_start, ts_train_end)
        test_matched = _matched_in_window(ts_test_start, ts_test_end)

        train_stats = calibrate.summary_stats(train_matched) if not train_matched.empty else None
        test_stats = calibrate.summary_stats(test_matched) if not test_matched.empty else None
        rows.append({
            "train_window": f"{train_start} → {train_end}",
            "test_window": f"{test_start} → {test_end}",
            "pt_variant": pt_variant,
            "train_n_matched": len(train_matched),
            "train_win_rate": train_stats.win_rate if train_stats else None,
            "train_avg_pnl": train_stats.avg_pnl_contract if train_stats else None,
            "train_sharpe": train_stats.sharpe if train_stats else None,
            "test_n_matched": len(test_matched),
            "test_win_rate": test_stats.win_rate if test_stats else None,
            "test_avg_pnl": test_stats.avg_pnl_contract if test_stats else None,
            "test_sharpe": test_stats.sharpe if test_stats else None,
        })
    return pd.DataFrame(rows)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _load_matched() -> tuple[dict[str, pd.DataFrame], float]:
    """Engine-backed matched-trade selection (one DataFrame per pt-variant).

    Replaces the legacy pair of `get_v3_matched_trades` calls with
    `nfo.studies.falsification.run_falsification`; the Monte-Carlo + sweep
    branches are disabled (zero iterations / empty fraction lists) so this
    call only exercises the trigger + cycle + trade-matching path.
    """
    signals_df = pd.read_parquet(SIGNALS_PATH)
    signals_df["date"] = pd.to_datetime(signals_df["date"])
    trades = load_trades_with_gaps()
    start = signals_df["date"].min().date()
    end = signals_df["date"].max().date()
    years = (end - start).days / 365.25

    spec, _ = load_strategy(ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml")
    rv = _load_rv_module()
    atr_series = rv.load_nifty_atr(signals_df["date"])

    def _event_resolver(entry, dte):
        return "high" if not rv._event_pass(
            entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"},
            window_days=10,
        ) else "none"

    matched: dict[str, pd.DataFrame] = {}
    for variant in VARIANTS:
        result = run_falsification(
            spec=spec, features_df=signals_df, atr_series=atr_series,
            trades_df=trades, pt_variant=variant,
            capital_inr=1_000_000, years=years,
            tail_loss_injections=[], tail_loss_iterations=0,
            allocation_fractions=[], walkforward_folds=0,
            event_resolver=_event_resolver, seed=42,
        )
        matched[variant] = result.matched_trades
    return matched, years




# ── Report rendering ────────────────────────────────────────────────────────


def render_report(
    *,
    capital: float,
    years: float,
    matched_by_variant: dict[str, pd.DataFrame],
    tail_loss_df: pd.DataFrame,
    allocation_df: pd.DataFrame,
    walk_forward_df: pd.DataFrame | None,
    exit_sweep_summary: str | None,
    entry_pert_summary: str | None,
) -> str:
    lines: list[str] = ["# V3 falsification report", ""]
    lines.append("Spec version: `v3-spec-frozen-2026-04-20` (see `docs/v3-spec-frozen.md`).")
    lines.append(
        f"Window span: **{years:.2f}** years. Capital: **{_format_inr(capital)}**."
    )
    for variant, trades in matched_by_variant.items():
        lines.append(f"- {variant.upper()}: **{len(trades)}** V3-matched trades")
    lines.append("")
    lines.append("## Scope caveat")
    lines.append("")
    lines.append(
        "Every result below is reported per exit variant (PT50, HTE) because "
        "they produce materially different robustness profiles. HTE in "
        "particular is entry-timing fragile and bankrupt-on-tail, while "
        "PT50 absorbs most of the same shocks. A conclusion stated for "
        "\"V3\" without a variant qualifier is not supported by the data in "
        "this report."
    )
    lines.append("")

    # Test 1 ────────────────────────────────────────────────────────────────
    lines.append("## 1. Tail-loss injection")
    lines.append("")
    lines.append(
        "Each draw resamples the 8-cycle V3 matched set with replacement, then "
        "replaces `n_injected` random rows with a synthetic max-loss cycle "
        "(`pnl_contract = (net_credit − width) × 65 − cost`). This tells us how "
        "many of our observed wins would have to flip into full max losses before "
        "the compound ₹10L account finishes below its starting balance."
    )
    lines.append("")
    for variant in VARIANTS:
        sub = tail_loss_df[tail_loss_df["variant"] == variant].copy()
        if sub.empty:
            continue
        lines.append(f"### {variant.upper()}")
        lines.append("")
        lines.append("| # injected | P(final ≥ ₹10L) | P5 final equity | P50 | P95 | P95 max DD |")
        lines.append("|---:|---:|---:|---:|---:|---:|")
        for _, row in sub.iterrows():
            lines.append(
                f"| {int(row['n_injected'])} | "
                f"{row['p_final_above_capital']*100:.1f}% | "
                f"{_format_inr(row['p5_final_equity'])} | "
                f"{_format_inr(row['p50_final_equity'])} | "
                f"{_format_inr(row['p95_final_equity'])} | "
                f"{row['p95_max_dd_pct']:.1f}% |"
            )
        lines.append("")

    # Test 2 ────────────────────────────────────────────────────────────────
    lines.append("## 2. Capital allocation sweep (deterministic)")
    lines.append("")
    lines.append(
        "No resampling — runs the actual observed matched trades through the "
        "equity simulator at each deployment fraction. 100 % matches the existing "
        "`v3_capital_analysis` headline; lower fractions hold reserve capital."
    )
    lines.append("")
    for variant in VARIANTS:
        sub = allocation_df[allocation_df["variant"] == variant].copy()
        if sub.empty:
            continue
        lines.append(f"### {variant.upper()}")
        lines.append("")
        lines.append("| deploy % | final equity (compound) | CAGR | max DD | Sharpe |")
        lines.append("|---:|---:|---:|---:|---:|")
        for _, row in sub.iterrows():
            lines.append(
                f"| {int(row['deployment_frac']*100)}% | "
                f"{_format_inr(row['final_equity_compound'])} | "
                f"{row['cagr_compound_pct']:+.1f}% | "
                f"{row['max_drawdown_pct']:.1f}% | "
                f"{row['sharpe']:+.2f} |"
            )
        lines.append("")

    # Test 3 ────────────────────────────────────────────────────────────────
    lines.append("## 3. Exit sweep (PT25 / PT50 / PT75 / HTE / DTE=2)")
    lines.append("")
    if exit_sweep_summary:
        lines.append(exit_sweep_summary)
    else:
        lines.append(
            "_Not yet generated. Run `scripts/nfo/exit_sweep_backtest.py` to "
            "produce `results/nfo/exit_sweep_trades.csv`; this section will "
            "summarise it on the next run._"
        )
    lines.append("")

    # Test 4 ────────────────────────────────────────────────────────────────
    lines.append("## 4. Walk-forward tuning")
    lines.append("")
    if walk_forward_df is None or walk_forward_df.empty:
        lines.append("_No windows produced results (insufficient train data)._")
    else:
        lines.append(
            "Rolling 12-month train / 6-month test. The **frozen V3 rule** is "
            "applied to each window separately — we're not tuning thresholds, "
            "we're asking whether the same specific-pass gate that fires on the "
            "train period also produces positive results in the following "
            "6 months. Train and test Sharpe use the per-lot convention from "
            "`calibrate.summary_stats` (√252 annualisation). `—` means V3 did "
            "not fire in that window."
        )
        lines.append("")
        for variant in VARIANTS:
            sub = walk_forward_df[walk_forward_df["pt_variant"] == variant]
            if sub.empty:
                continue
            lines.append(f"### {variant.upper()}")
            lines.append("")
            lines.append("| train window | test window | train n | train win% | train Sharpe | test n | test win% | test Sharpe |")
            lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
            for _, row in sub.iterrows():
                def _fmt_pct(v):
                    return f"{v*100:.0f}%" if v is not None and pd.notna(v) else "—"
                def _fmt_sharpe(v):
                    return f"{v:+.2f}" if v is not None and pd.notna(v) else "—"
                lines.append(
                    f"| {row['train_window']} | {row['test_window']} | "
                    f"{int(row['train_n_matched'])} | {_fmt_pct(row['train_win_rate'])} | "
                    f"{_fmt_sharpe(row['train_sharpe'])} | "
                    f"{int(row['test_n_matched'])} | {_fmt_pct(row['test_win_rate'])} | "
                    f"{_fmt_sharpe(row['test_sharpe'])} |"
                )
            lines.append("")
    lines.append("")

    # Test 5 ────────────────────────────────────────────────────────────────
    lines.append("## 5. Entry perturbation")
    lines.append("")
    if entry_pert_summary:
        lines.append(entry_pert_summary)
    else:
        lines.append(
            "_Not yet generated. Run `scripts/nfo/entry_perturbation_backtest.py` "
            "to produce `results/nfo/entry_perturbation_trades.csv`; this section "
            "will summarise it on the next run._"
        )
    lines.append("")
    return "\n".join(lines)


def _summarise_exit_sweep() -> str | None:
    """Load `exit_sweep_trades.csv` if present and return a markdown block."""
    if not EXIT_SWEEP_CSV.exists():
        return None
    df = pd.read_csv(EXIT_SWEEP_CSV)
    if df.empty:
        return None
    lines = ["| exit rule | trades | win% | avg PnL | Sharpe | max_loss% |",
             "|---|---:|---:|---:|---:|---:|"]
    for _, row in df.iterrows():
        lines.append(
            f"| {row['exit_rule']} | {int(row['n'])} | "
            f"{row['win_rate']*100:.0f}% | "
            f"{_format_inr(row['avg_pnl_contract'])} | "
            f"{row['sharpe']:+.2f} | "
            f"{row['max_loss_rate']*100:.1f}% |"
        )
    return "\n".join(lines)


def _summarise_entry_pert() -> str | None:
    if not ENTRY_PERT_CSV.exists():
        return None
    df = pd.read_csv(ENTRY_PERT_CSV)
    if df.empty:
        return None
    # Older runs produced a single flat table (no `variant` column) —
    # handle both shapes.
    if "variant" not in df.columns:
        df = df.assign(variant="hte")
    blocks: list[str] = []
    for variant in VARIANTS:
        sub = df[df["variant"] == variant]
        if sub.empty:
            continue
        blocks.append(f"### {variant.upper()}")
        blocks.append("")
        blocks.append("| entry timing | trades | win% | avg PnL | total | Sharpe |")
        blocks.append("|---|---:|---:|---:|---:|---:|")
        for _, row in sub.iterrows():
            blocks.append(
                f"| {row['timing']} | {int(row['n'])} | "
                f"{row['win_rate']*100:.0f}% | "
                f"{_format_inr(row['avg_pnl_contract'])} | "
                f"{_format_inr(row['total_pnl_contract'])} | "
                f"{row['sharpe']:+.2f} |"
            )
        blocks.append("")
    return "\n".join(blocks) if blocks else None


# ── CLI ─────────────────────────────────────────────────────────────────────


def _legacy_main(argv: list[str] | None = None) -> dict[str, Any]:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capital", type=float, default=10_00_000)
    ap.add_argument("--tail-loss-iterations", type=int, default=5_000)
    ap.add_argument("--tail-loss-injections", default="0,1,2,3",
                    help="Comma-separated injection counts to test.")
    ap.add_argument("--allocation-fracs", default="0.10,0.20,0.30,0.50,1.00")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--output-dir", type=Path, default=RESULTS_DIR)
    ap.add_argument("--skip-walkforward", action="store_true",
                    help="Skip the walk-forward grid search (expensive).")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    injection_counts = [int(x) for x in args.tail_loss_injections.split(",") if x.strip()]
    deployment_fracs = [float(x) for x in args.allocation_fracs.split(",") if x.strip()]

    matched_by_variant, years = _load_matched()
    for v in VARIANTS:
        log.info("%s: %d matched trades.", v, len(matched_by_variant[v]))

    # 1. Tail-loss injection
    tail_df = run_tail_loss_injection(
        matched_by_variant, capital=args.capital, years=years,
        injection_counts=injection_counts,
        n_iter=args.tail_loss_iterations, seed=args.seed,
    )
    tail_df.to_csv(args.output_dir / "falsify_tail_loss.csv", index=False)
    log.info("Tail-loss rows: %d", len(tail_df))

    # 2. Capital allocation sweep
    alloc_df = run_allocation_sweep(
        matched_by_variant, capital=args.capital, years=years,
        deployment_fracs=deployment_fracs,
    )
    alloc_df.to_csv(args.output_dir / "falsify_allocation.csv", index=False)
    log.info("Allocation sweep rows: %d", len(alloc_df))

    # 4. Walk-forward of the FROZEN V3 rule
    walk_df = pd.DataFrame()
    if not args.skip_walkforward:
        all_trades = load_trades_with_gaps()
        signals_df = pd.read_parquet(SIGNALS_PATH)
        signals_df["date"] = pd.to_datetime(signals_df["date"])
        # 12-month train / 6-month test. 6 months (~2 V3 cycles) is the
        # smallest test window that avoids systematic n=0 on a ~11-fire/yr
        # strategy; shorter windows proved uninformative.
        starts = pd.date_range("2024-01-01", "2026-01-01", freq="3MS")
        windows: list[tuple[str, str, str, str]] = []
        for s in starts:
            train_end = s + pd.DateOffset(months=12) - pd.Timedelta(days=1)
            test_start = s + pd.DateOffset(months=12)
            test_end = s + pd.DateOffset(months=18) - pd.Timedelta(days=1)
            if test_end > pd.Timestamp("2026-04-17"):
                break
            windows.append((
                s.strftime("%Y-%m-%d"),
                train_end.strftime("%Y-%m-%d"),
                test_start.strftime("%Y-%m-%d"),
                test_end.strftime("%Y-%m-%d"),
            ))
        # Walk-forward for both variants so the report can show V3 generalisation
        # separately for PT50 and HTE.
        dfs = []
        for variant in VARIANTS:
            dfs.append(run_walk_forward(
                signals_df, all_trades, windows=windows, pt_variant=variant,
            ))
        walk_df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        walk_df.to_csv(args.output_dir / "falsify_walkforward.csv", index=False)
        log.info("Walk-forward rows: %d", len(walk_df))

    # 3 & 5 summaries delegated
    exit_summary = _summarise_exit_sweep()
    entry_summary = _summarise_entry_pert()

    report = render_report(
        capital=args.capital, years=years,
        matched_by_variant=matched_by_variant,
        tail_loss_df=tail_df, allocation_df=alloc_df,
        walk_forward_df=walk_df if not walk_df.empty else None,
        exit_sweep_summary=exit_summary,
        entry_pert_summary=entry_summary,
    )
    (args.output_dir / "falsification_report.md").write_text(report, encoding="utf-8")
    log.info("Wrote falsification_report.md + 3 CSVs.")
    print()
    print(report)
    metrics: dict[str, Any] = {
        "tail_loss_rows": int(len(tail_df)),
        "allocation_rows": int(len(alloc_df)),
        "walkforward_rows": int(len(walk_df)) if isinstance(walk_df, pd.DataFrame) else 0,
    }
    body_markdown = (
        "See `tables/` for full outputs. Legacy artifacts mirrored from "
        "`results/nfo/`.\n"
    )
    warnings: list[str] = []
    return {"metrics": metrics, "body_markdown": body_markdown, "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    from datetime import date
    from nfo.config import RESULTS_DIR, ROOT
    from nfo.reporting.wrap_legacy_run import wrap_legacy_run

    def run_logic() -> dict:
        return _legacy_main(argv)

    result = wrap_legacy_run(
        study_type="falsification",
        strategy_path=ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml",
        study_path=ROOT / "configs" / "nfo" / "studies" / "falsification_default.yaml",
        legacy_artifacts=[
            RESULTS_DIR / "falsify_tail_loss.csv",
            RESULTS_DIR / "falsify_allocation.csv",
            RESULTS_DIR / "falsify_walkforward.csv",
            RESULTS_DIR / "falsification_report.md",
        ],
        window=(date(2024, 2, 1), date(2026, 4, 18)),
        run_logic=run_logic,
        runs_root=RESULTS_DIR / "runs",
    )
    print(result.run_dir.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
