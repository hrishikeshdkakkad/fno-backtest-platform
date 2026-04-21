"""Robustness study: slippage sweep + leave-one-out + block bootstrap.

Engine-backed V3 robustness orchestration. Replaces the legacy pipeline in
`scripts/nfo/v3_robustness.py::_legacy_main`, which composed
`robustness.get_v3_matched_trades` + `robustness.compute_equity_curves` +
`robustness.apply_slippage` + `robustness.leave_one_out` +
`robustness.block_bootstrap` and handed the result to a bespoke report writer.

The study wires engine primitives:
  1. TriggerEvaluator(spec) → fire dates
  2. group_fires_by_cycle       → cycle index
  3. select_cycle_matched       → one trade per firing cycle at `pt_variant`
  4. engine.capital.compute_equity_curves → fixed + compounding equity series
  5. engine.metrics.summary_stats         → per-trade baseline stats
  6. robustness.apply_slippage / leave_one_out / block_bootstrap → legacy primitives

Callers (e.g. scripts/nfo/v3_robustness.py) handle report formatting and
legacy-file writes; this module stays as a pure-function composition over the
engine and returns a structured result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from nfo import robustness as _legacy_robustness
from nfo.engine.capital import EquityResult, compute_equity_curves
from nfo.engine.cycles import group_fires_by_cycle
from nfo.engine.metrics import SummaryStats, summary_stats
from nfo.engine.selection import select_cycle_matched
from nfo.engine.triggers import TriggerEvaluator
from nfo.robustness import BootstrapResult, LooRow, apply_slippage
from nfo.specs.strategy import CapitalSpec, StrategySpec


DEFAULT_SLIPPAGE_SWEEP_RUPEES = [0, 250, 500, 750, 1000]


@dataclass
class RobustnessResult:
    """Composite robustness-study output.

    Attributes
    ----------
    matched_trades
        One-row-per-cycle V3-matched trade frame (via `select_cycle_matched`).
    baseline_stats
        `summary_stats(matched_trades)` — per-trade headline.
    baseline_equity
        `compute_equity_curves(matched_trades, ...)` — equity curves at
        `capital_inr`.
    slippage_sweep
        One row per slippage level with columns: `slippage`,
        `total_pnl_fixed`, `sharpe`, `win_rate`, `max_loss_rate`,
        `final_equity_compound`, `max_dd_pct`.
    leave_one_out
        List of `LooRow` for each matched trade in row order.
    bootstrap
        `BootstrapResult` — raw arrays + percentile helpers.
    years
        Window length used for annualisation.
    capital_inr
        Starting capital (₹).
    pt_variant
        Exit variant selected (`pt50`, `hte`, etc.).
    """

    matched_trades: pd.DataFrame
    baseline_stats: SummaryStats
    baseline_equity: EquityResult
    slippage_sweep: pd.DataFrame
    leave_one_out: list[LooRow] = field(default_factory=list)
    bootstrap: BootstrapResult | None = None
    years: float = 0.0
    capital_inr: float = 0.0
    pt_variant: str = ""


def _slippage_row(
    *,
    slippage: float,
    trades: pd.DataFrame,
    capital_spec: CapitalSpec,
    years: float,
) -> dict[str, float]:
    """Compute one row of the slippage sweep (stats + equity on slipped trades)."""
    if trades.empty:
        return {
            "slippage": float(slippage),
            "total_pnl_fixed": 0.0,
            "sharpe": 0.0,
            "win_rate": 0.0,
            "max_loss_rate": 0.0,
            "final_equity_compound": float(capital_spec.fixed_capital_inr),
            "max_dd_pct": 0.0,
        }
    slipped = apply_slippage(trades, float(slippage))
    stats = summary_stats(slipped)
    equity = compute_equity_curves(slipped, capital_spec=capital_spec, years=years)
    return {
        "slippage": float(slippage),
        "total_pnl_fixed": float(equity.total_pnl_fixed),
        "sharpe": float(equity.sharpe),
        "win_rate": float(stats.win_rate),
        "max_loss_rate": float(stats.max_loss_rate),
        "final_equity_compound": float(equity.final_equity_compound),
        "max_dd_pct": float(equity.max_drawdown_pct),
    }


def run_robustness(
    *,
    spec: StrategySpec,
    features_df: pd.DataFrame,
    atr_series: pd.Series,
    trades_df: pd.DataFrame,
    pt_variant: str,
    capital_inr: float,
    years: float | None = None,
    bootstrap_iterations: int = 10_000,
    seed: int = 42,
    slippage_sweep_rupees: list[int] | None = None,
    event_resolver: Callable | None = None,
) -> RobustnessResult:
    """Engine-backed V3 robustness orchestration (master design §3, §9.3).

    Parameters
    ----------
    spec
        Cycle-matched StrategySpec (e.g. the v3_frozen.yaml frozen winner).
    features_df
        Features frame with `date` (datetime64) and `target_expiry` columns.
    atr_series
        ATR series indexed by date. Passed through to the trigger evaluator.
    trades_df
        Trade universe (e.g. `spread_trades.csv` + `spread_trades_v3_gaps.csv`).
    pt_variant
        Exit variant to pick from the universe: `pt50`, `hte`, `pt25`, `pt75`,
        or `dte2`.
    capital_inr
        Starting capital in rupees.
    years
        Window length for annualisation. When None, computed from
        `features_df["date"]` span.
    bootstrap_iterations
        Number of bootstrap resamples. Defaults to 10 000.
    seed
        RNG seed for reproducibility.
    slippage_sweep_rupees
        Flat ₹/lot round-trip slippage values to sweep. Defaults to
        `[0, 250, 500, 750, 1000]`.
    event_resolver
        Optional callable `(entry_date, dte) -> severity_str` used by the
        trigger evaluator when the features dataset's event-risk column
        doesn't reflect the target spec's semantics.
    """
    slippage_levels = (
        [int(s) for s in slippage_sweep_rupees]
        if slippage_sweep_rupees is not None
        else list(DEFAULT_SLIPPAGE_SWEEP_RUPEES)
    )

    # Years derivation from features window when not supplied.
    if years is None:
        dates = pd.to_datetime(features_df["date"])
        if dates.empty:
            years_value = 0.0
        else:
            span = (dates.max() - dates.min()).days
            years_value = span / 365.25
    else:
        years_value = float(years)

    # Trigger → cycle → matched-trade selection (same path as capital_analysis).
    ev = TriggerEvaluator(spec, event_resolver=event_resolver)
    fires = ev.fire_dates(features_df, atr_series)
    cycles = group_fires_by_cycle(
        fires, features_df,
        underlying=spec.universe.underlyings[0],
        strategy_version=spec.strategy_version,
    )
    matched = select_cycle_matched(
        trades_df, cycles, spec, pt_variant=pt_variant,
    )

    capital_spec = CapitalSpec(
        fixed_capital_inr=capital_inr,
        deployment_fraction=1.0,
        compounding=False,
    )

    # Baseline stats + equity on the matched cycles.
    baseline_stats = summary_stats(matched)
    baseline_equity = compute_equity_curves(
        matched, capital_spec=capital_spec, years=years_value,
    )

    # Slippage sweep: one row per level.
    sweep_rows = [
        _slippage_row(
            slippage=level,
            trades=matched,
            capital_spec=capital_spec,
            years=years_value,
        )
        for level in slippage_levels
    ]
    slippage_sweep = pd.DataFrame(sweep_rows)

    # Leave-one-out + bootstrap (legacy primitives; same-seed = deterministic).
    loo = _legacy_robustness.leave_one_out(
        matched, capital=capital_inr, years=years_value,
    )
    bootstrap = _legacy_robustness.block_bootstrap(
        matched,
        capital=capital_inr,
        years=years_value,
        n_iter=int(bootstrap_iterations),
        seed=int(seed),
    )

    return RobustnessResult(
        matched_trades=matched,
        baseline_stats=baseline_stats,
        baseline_equity=baseline_equity,
        slippage_sweep=slippage_sweep,
        leave_one_out=list(loo),
        bootstrap=bootstrap,
        years=years_value,
        capital_inr=float(capital_inr),
        pt_variant=pt_variant,
    )
