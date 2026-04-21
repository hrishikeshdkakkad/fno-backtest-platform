"""V3 robustness test suite — slippage sweep + leave-one-out + block bootstrap.

Context
-------
After extending the backtest through 2026-03-30 and deducting real
transaction costs, V3's full-window Sharpe collapsed from +1.75 to
-0.35 with a 70 % win rate on only 10 matched trades. The
train/test split diverged (2024 train: 88 % win; 2025+ test: 0 %
win on 2 trades). Before sizing this live, we want quantitative
evidence on:

    1. how sensitive V3 is to additional slippage (break-even drag),
    2. how much of the edge hangs on any single historical cycle, and
    3. what the distribution of outcomes looks like under resampling.

The trio below answers those questions on both PT50 (50 % profit-take
at DTE=21) and HTE (hold-to-expiry) exit variants.

Usage
-----

    .venv/bin/python scripts/nfo/v3_robustness.py
    .venv/bin/python scripts/nfo/v3_robustness.py \\
        --capital 1000000 \\
        --bootstrap-iterations 10000 \\
        --slippage-grid 0,100,250,500,750,1000 \\
        --seed 42

Writes
------

    results/nfo/robustness_report.md
    results/nfo/robustness_slippage.csv
    results/nfo/robustness_loo.csv
    results/nfo/robustness_bootstrap.csv
"""
from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from nfo import calibrate
from nfo.config import RESULTS_DIR, ROOT
from nfo.robustness import (
    apply_slippage,
    compute_equity_curves,
    load_trades_with_gaps,
)
from nfo.specs.loader import load_strategy
from nfo.studies.robustness import RobustnessResult, run_robustness

log = logging.getLogger("v3_robustness")

SIGNALS_PATH = RESULTS_DIR / "historical_signals.parquet"
VARIANTS = ("pt50", "hte")
_HERE = Path(__file__).resolve().parent


def _load_rv_module(alias: str = "_legacy_rv_robustness"):
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


# ── 1. Slippage sweep ────────────────────────────────────────────────────────


def run_slippage_sweep(
    matched_by_variant: dict[str, pd.DataFrame],
    *,
    slippage_grid: list[float],
    capital: float,
    years: float,
) -> pd.DataFrame:
    rows = []
    for slippage in slippage_grid:
        row: dict[str, object] = {"slippage_rupees_per_lot": slippage}
        survives_all = True
        for variant, trades in matched_by_variant.items():
            adjusted = apply_slippage(trades, slippage)
            stats = calibrate.summary_stats(adjusted)
            equity = compute_equity_curves(adjusted, capital=capital, years=years)
            row[f"{variant}_win_rate"] = stats.win_rate
            row[f"{variant}_avg_pnl"] = stats.avg_pnl_contract
            # Two Sharpes, both reported: `sharpe_capital` is the ₹10L-deployed
            # Sharpe from `compute_equity_curves` (monthly-cycle annualisation)
            # and is the headline for the report. `sharpe_per_lot` is the
            # per-trade Sharpe from `summary_stats` (√252-day annualisation);
            # kept for audit comparison with legacy reports.
            row[f"{variant}_sharpe_capital"] = equity.sharpe
            row[f"{variant}_sharpe_per_lot"] = stats.sharpe
            row[f"{variant}_total_pnl_fixed"] = equity.total_pnl_fixed
            row[f"{variant}_total_pnl_compound"] = equity.total_pnl_compound
            row[f"{variant}_final_equity"] = equity.final_equity_compound
            row[f"{variant}_max_drawdown_pct"] = equity.max_drawdown_pct
            row[f"{variant}_positive"] = equity.total_pnl_compound > 0
            survives_all = survives_all and equity.total_pnl_compound > 0
        row["both_variants_positive"] = survives_all
        rows.append(row)
    return pd.DataFrame(rows)


def _slippage_break_even(df: pd.DataFrame, variant: str) -> float | None:
    """Linear interpolation of the slippage where compound P&L hits zero.

    Returns None if the series stays positive (or negative) throughout the
    grid — we cannot extrapolate without risking nonsense numbers.
    """
    col = f"{variant}_total_pnl_compound"
    s = df[col].astype(float)
    x = df["slippage_rupees_per_lot"].astype(float)
    if (s > 0).all() or (s < 0).all():
        return None
    for i in range(1, len(s)):
        if s.iloc[i - 1] >= 0 > s.iloc[i] or s.iloc[i - 1] < 0 <= s.iloc[i]:
            x0, x1 = x.iloc[i - 1], x.iloc[i]
            y0, y1 = s.iloc[i - 1], s.iloc[i]
            return float(x0 + (0 - y0) * (x1 - x0) / (y1 - y0))
    return None


# ── 2. Leave-one-out and 3. Block bootstrap ─────────────────────────────────
# The legacy `run_loo` + `run_bootstrap` helpers were moved inside
# `nfo.studies.robustness.run_robustness` in P5-D1. The script-local
# `_loo_rows_to_df` + `_bootstrap_to_long_df` (defined below) now handle the
# multi-variant legacy CSV shaping from the engine's per-variant results.


# ── Report rendering ─────────────────────────────────────────────────────────


def render_report(
    *,
    capital: float,
    years: float,
    n_iter: int,
    slippage_df: pd.DataFrame,
    slippage_break_even: dict[str, float | None],
    loo_df: pd.DataFrame,
    bootstrap_pct: pd.DataFrame,
    bootstrap_prob_positive_fixed: dict[str, float],
    bootstrap_prob_positive_compound: dict[str, float],
    matched_by_variant: dict[str, pd.DataFrame],
) -> str:
    lines: list[str] = []
    lines.append("# V3 robustness report")
    lines.append("")
    lines.append(
        f"Window span: **{years:.2f}** years. Capital per trade: "
        f"**{_format_inr(capital)}**. Bootstrap iterations: **{n_iter:,}**."
    )
    lines.append("")
    for variant, trades in matched_by_variant.items():
        lines.append(
            f"- {variant.upper()}: **{len(trades)}** V3-matched trades"
        )
    lines.append("")

    # 1. Slippage sweep ─────────────────────────────────────────────────────
    lines.append("## 1. Slippage sweep — break-even analysis")
    lines.append("")
    lines.append("Extra round-trip slippage is applied as a flat ₹/lot reduction to "
                 "`pnl_contract`. Compound P&L uses the standard `v3_capital_analysis` "
                 "sizing (deploy running equity, integer lots).")
    lines.append("")
    lines.append("| ₹/lot slip | PT50 compound P&L | PT50 positive? | HTE compound P&L | HTE positive? |")
    lines.append("|---:|---:|:-:|---:|:-:|")
    for _, row in slippage_df.iterrows():
        lines.append(
            f"| {int(row['slippage_rupees_per_lot'])} | "
            f"{_format_inr(row['pt50_total_pnl_compound'])} | "
            f"{'✓' if row['pt50_positive'] else '✗'} | "
            f"{_format_inr(row['hte_total_pnl_compound'])} | "
            f"{'✓' if row['hte_positive'] else '✗'} |"
        )
    lines.append("")
    for variant in VARIANTS:
        be = slippage_break_even.get(variant)
        if be is None:
            lines.append(
                f"- {variant.upper()}: does not cross zero inside the tested grid."
            )
        else:
            lines.append(
                f"- {variant.upper()}: break-even slippage ≈ **₹{be:.0f}/lot** "
                "round-trip (linear interpolation between adjacent grid rows)."
            )
    lines.append("")

    # 2. Leave-one-out ──────────────────────────────────────────────────────
    lines.append("## 2. Leave-one-out — single-cycle dependency")
    lines.append("")
    lines.append("Each row drops one V3-matched trade, recomputes Sharpe / win-rate "
                 "/ total-P&L on the remaining set, and records the impact. The "
                 "'worst-case LOO' is the cycle whose removal hurts the headline "
                 "most.")
    lines.append("")
    for variant in VARIANTS:
        sub = loo_df[loo_df["variant"] == variant].copy()
        if sub.empty:
            continue
        # Display rows sorted by the Sharpe column (ascending) so the top row
        # IS the worst-Sharpe drop — narrative and table now agree.
        sub = sub.sort_values("remaining_sharpe").reset_index(drop=True)
        lines.append(f"### {variant.upper()}")
        lines.append("")
        lines.append("| dropped expiry | outcome | dropped P&L | remaining win% | remaining Sharpe (₹10L) | remaining total (fixed size) |")
        lines.append("|---|---|---:|---:|---:|---:|")
        for _, row in sub.iterrows():
            lines.append(
                f"| {row['dropped_expiry']} | {row['dropped_outcome']} | "
                f"{_format_inr(row['dropped_pnl_contract'])} | "
                f"{row['remaining_win_rate']*100:.0f}% | "
                f"{row['remaining_sharpe']:+.2f} | "
                f"{_format_inr(row['total_pnl_fixed'])} |"
            )
        worst_sharpe = sub.iloc[0]
        worst_total = sub.sort_values("total_pnl_fixed").iloc[0]
        lines.append("")
        lines.append(
            f"- Worst-Sharpe drop (removing **{worst_sharpe['dropped_expiry']}**): "
            f"remaining Sharpe {worst_sharpe['remaining_sharpe']:+.2f}, total "
            f"{_format_inr(worst_sharpe['total_pnl_fixed'])}."
        )
        lines.append(
            f"- Worst-total drop (removing **{worst_total['dropped_expiry']}**): "
            f"remaining Sharpe {worst_total['remaining_sharpe']:+.2f}, total "
            f"{_format_inr(worst_total['total_pnl_fixed'])}. "
            "If worst-Sharpe and worst-total disagree, the edge sits across "
            "multiple cycles — each criterion stresses a different cycle."
        )
        lines.append("")

    # 3. Block bootstrap ────────────────────────────────────────────────────
    lines.append("## 3. Block bootstrap — resampling V3 cycles")
    lines.append("")
    lines.append(f"Each iteration resamples V3's matched cycles with replacement "
                 f"(one row = one cycle), walks them through the equity simulator, "
                 f"and records total P&L / compounding CAGR / max drawdown. "
                 f"{int(n_iter):,} iterations, seed recorded in CSV.")
    lines.append("")
    for variant in VARIANTS:
        sub = bootstrap_pct[bootstrap_pct["variant"] == variant]
        if sub.empty:
            continue
        prob_pos_fixed = bootstrap_prob_positive_fixed.get(variant, float("nan"))
        prob_pos_compound = bootstrap_prob_positive_compound.get(variant, float("nan"))
        lines.append(
            f"### {variant.upper()}  "
            f"(P(compound final equity > ₹{capital/1e5:.0f}L) = **{prob_pos_compound*100:.1f}%**, "
            f"P(fixed-size total P&L > 0) = {prob_pos_fixed*100:.1f}%)"
        )
        lines.append("")
        lines.append("| percentile | total P&L (fixed) | final equity (compound) | CAGR compound | max DD |")
        lines.append("|---:|---:|---:|---:|---:|")
        for _, row in sub.iterrows():
            lines.append(
                f"| P{int(row['percentile'])} | "
                f"{_format_inr(row['total_pnl_fixed'])} | "
                f"{_format_inr(row['final_equity_compound'])} | "
                f"{row['cagr_compound_pct']:+.1f}% | "
                f"{row['max_drawdown_pct']:.1f}% |"
            )
        lines.append("")

    # Verdict table ─────────────────────────────────────────────────────────
    lines.append("## Verdict against the five trust criteria")
    lines.append("")
    pt50_500 = slippage_df[slippage_df["slippage_rupees_per_lot"] == 500]
    hte_500 = slippage_df[slippage_df["slippage_rupees_per_lot"] == 500]
    pt50_pos_500 = bool(pt50_500["pt50_positive"].iloc[0]) if not pt50_500.empty else False
    hte_pos_500 = bool(hte_500["hte_positive"].iloc[0]) if not hte_500.empty else False
    worst_pt50_sharpe = (
        loo_df[loo_df["variant"] == "pt50"]["remaining_sharpe"].min()
        if not loo_df.empty else float("nan")
    )
    worst_hte_sharpe = (
        loo_df[loo_df["variant"] == "hte"]["remaining_sharpe"].min()
        if not loo_df.empty else float("nan")
    )
    loo_positive_pt50 = worst_pt50_sharpe > 0 if pd.notna(worst_pt50_sharpe) else False
    loo_positive_hte = worst_hte_sharpe > 0 if pd.notna(worst_hte_sharpe) else False

    def _check(passed: bool) -> str:
        return "✅" if passed else "❌"

    lines.append(f"- {_check(pt50_pos_500)} PT50 positive after ₹500/lot extra slippage (compound P&L)")
    lines.append(f"- {_check(hte_pos_500)} HTE positive after ₹500/lot extra slippage (compound P&L)")
    lines.append(
        f"- {_check(loo_positive_pt50)} PT50 remains positive ₹10L Sharpe after LOO worst case "
        f"(worst Sharpe {worst_pt50_sharpe:+.2f})"
    )
    lines.append(
        f"- {_check(loo_positive_hte)} HTE remains positive ₹10L Sharpe after LOO worst case "
        f"(worst Sharpe {worst_hte_sharpe:+.2f})"
    )
    # Compounding positivity at P5 is a tighter-than-headline sanity check:
    # does the account even stay above starting balance in the bottom-5%
    # bootstrap draw?
    pt50_p5_equity = bootstrap_pct[
        (bootstrap_pct["variant"] == "pt50") & (bootstrap_pct["percentile"] == 5)
    ]["final_equity_compound"]
    hte_p5_equity = bootstrap_pct[
        (bootstrap_pct["variant"] == "hte") & (bootstrap_pct["percentile"] == 5)
    ]["final_equity_compound"]
    pt50_p5_above = bool(pt50_p5_equity.iloc[0] > capital) if not pt50_p5_equity.empty else False
    hte_p5_above = bool(hte_p5_equity.iloc[0] > capital) if not hte_p5_equity.empty else False
    lines.append(
        f"- {_check(pt50_p5_above)} PT50 bootstrap P5 final equity above starting ₹{capital/1e5:.0f}L"
    )
    lines.append(
        f"- {_check(hte_p5_above)} HTE bootstrap P5 final equity above starting ₹{capital/1e5:.0f}L"
    )
    lines.append("- ⏳ Tail-loss injection, walk-forward, regime-bucket slicing — phase 2")
    lines.append("")

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _loo_rows_to_df(results: dict[str, RobustnessResult]) -> pd.DataFrame:
    """Project per-variant LooRow lists into the legacy long-form CSV schema."""
    rows: list[dict[str, Any]] = []
    for variant, result in results.items():
        for loo in result.leave_one_out:
            rows.append({
                "variant": variant,
                "dropped_index": loo.dropped_index,
                "dropped_expiry": loo.dropped_expiry,
                "dropped_outcome": loo.dropped_outcome,
                "dropped_pnl_contract": loo.dropped_pnl_contract,
                "remaining_n": loo.summary.n,
                "remaining_win_rate": loo.summary.win_rate,
                "remaining_avg_pnl": loo.summary.avg_pnl_contract,
                "remaining_sharpe": loo.equity_sharpe,
                "remaining_sharpe_per_lot": loo.summary.sharpe,
                "total_pnl_fixed": loo.total_pnl_fixed,
                "total_pnl_compound": loo.total_pnl_compound,
                "final_equity_compound": loo.final_equity_compound,
                "max_drawdown_pct": loo.max_drawdown_pct,
            })
    return pd.DataFrame(rows)


def _bootstrap_to_long_df(results: dict[str, RobustnessResult]) -> pd.DataFrame:
    """Stack per-variant bootstrap percentile frames into the legacy long schema."""
    frames: list[pd.DataFrame] = []
    for variant, result in results.items():
        pct = result.bootstrap.percentiles()
        pct.insert(0, "variant", variant)
        frames.append(pct)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _legacy_main(argv: list[str] | None = None) -> dict[str, Any]:
    """P5-D2: thin wrapper over `nfo.studies.robustness.run_robustness`.

    Loads the v3_frozen spec, signals, trades, ATR; runs the engine study
    once per pt-variant; shapes the per-variant RobustnessResult objects
    into the 4 legacy artifacts (slippage CSV, LOO CSV, bootstrap CSV,
    markdown report); returns the `wrap_legacy_run` contract dict.
    """
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--capital", type=float, default=10_00_000,
                    help="Starting capital in INR (default ₹10L).")
    ap.add_argument("--bootstrap-iterations", type=int, default=10_000,
                    help="Number of bootstrap resamples (default 10,000).")
    ap.add_argument("--slippage-grid", default="0,100,250,500,750,1000",
                    help="Comma-separated ₹/lot extra slippage values.")
    ap.add_argument("--seed", type=int, default=42,
                    help="RNG seed for bootstrap reproducibility.")
    ap.add_argument("--output-dir", type=Path, default=RESULTS_DIR)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    slippage_grid = [float(x) for x in args.slippage_grid.split(",") if x.strip()]

    spec, _ = load_strategy(ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml")
    signals_df = pd.read_parquet(SIGNALS_PATH)
    signals_df["date"] = pd.to_datetime(signals_df["date"])
    trades = load_trades_with_gaps()

    rv = _load_rv_module()
    atr = rv.load_nifty_atr(signals_df["date"])

    def _event_resolver(entry, dte):
        return "high" if not rv._event_pass(
            entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"},
            window_days=10,
        ) else "none"

    # Run the engine study once per pt-variant.
    results: dict[str, RobustnessResult] = {}
    for variant in VARIANTS:
        result = run_robustness(
            spec=spec, features_df=signals_df, atr_series=atr,
            trades_df=trades, pt_variant=variant,
            capital_inr=args.capital,
            bootstrap_iterations=args.bootstrap_iterations,
            seed=args.seed,
            slippage_sweep_rupees=slippage_grid,
            event_resolver=_event_resolver,
        )
        log.info("Matched %d trades for variant %s", len(result.matched_trades), variant)
        if result.matched_trades.empty:
            log.error("No V3-matched trades found for %s — cannot continue.", variant)
            return {
                "metrics": {},
                "body_markdown": "",
                "warnings": [f"No V3-matched trades for variant {variant}"],
            }
        results[variant] = result

    # Build the legacy multi-variant slippage CSV via the existing helper
    # (wide schema: `pt50_*` + `hte_*` columns, one row per slippage level).
    matched_by_variant = {v: r.matched_trades for v, r in results.items()}
    # Use the first result's years for annualisation parity across variants.
    years = next(iter(results.values())).years
    slippage_df = run_slippage_sweep(
        matched_by_variant, slippage_grid=slippage_grid,
        capital=args.capital, years=years,
    )
    break_even = {v: _slippage_break_even(slippage_df, v) for v in VARIANTS}
    log.info("Slippage sweep done (%d rows).", len(slippage_df))

    loo_df = _loo_rows_to_df(results)
    log.info("Leave-one-out done (%d rows).", len(loo_df))

    bootstrap_pct = _bootstrap_to_long_df(results)
    prob_positive_fixed = {
        v: float(r.bootstrap.prob_positive_fixed()) for v, r in results.items()
    }
    prob_positive_compound = {
        v: float(r.bootstrap.prob_positive_compound()) for v, r in results.items()
    }
    log.info("Bootstrap done (%d iterations per variant).", args.bootstrap_iterations)

    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    slippage_df.to_csv(out_dir / "robustness_slippage.csv", index=False)
    loo_df.to_csv(out_dir / "robustness_loo.csv", index=False)
    bootstrap_pct.to_csv(out_dir / "robustness_bootstrap.csv", index=False)

    report = render_report(
        capital=args.capital, years=years,
        n_iter=args.bootstrap_iterations,
        slippage_df=slippage_df, slippage_break_even=break_even,
        loo_df=loo_df,
        bootstrap_pct=bootstrap_pct,
        bootstrap_prob_positive_fixed=prob_positive_fixed,
        bootstrap_prob_positive_compound=prob_positive_compound,
        matched_by_variant=matched_by_variant,
    )
    (out_dir / "robustness_report.md").write_text(report, encoding="utf-8")
    log.info("Wrote robustness_report.md + 3 CSVs to %s", out_dir)
    print()
    print(report)
    metrics: dict[str, Any] = {
        f"{v}_prob_positive_compound": prob_positive_compound.get(v)
        for v in VARIANTS
    }
    return {
        "metrics": metrics,
        "body_markdown": (
            "See `tables/` for full outputs. Legacy artifacts mirrored from "
            "`results/nfo/`.\n"
        ),
        "warnings": [],
    }


def main(argv: list[str] | None = None) -> int:
    from datetime import date
    from nfo.reporting.wrap_legacy_run import wrap_legacy_run

    def run_logic() -> dict:
        return _legacy_main(argv)

    result = wrap_legacy_run(
        study_type="robustness",
        strategy_path=ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml",
        study_path=ROOT / "configs" / "nfo" / "studies" / "robustness_default.yaml",
        legacy_artifacts=[
            RESULTS_DIR / "robustness_slippage.csv",
            RESULTS_DIR / "robustness_loo.csv",
            RESULTS_DIR / "robustness_bootstrap.csv",
            RESULTS_DIR / "robustness_report.md",
        ],
        window=(date(2024, 2, 1), date(2026, 4, 18)),
        run_logic=run_logic,
        runs_root=RESULTS_DIR / "runs",
    )
    print(result.run_dir.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
