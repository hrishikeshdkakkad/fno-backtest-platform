"""Engine-backed V3 falsification orchestration (master design §3, §9.3).

Orchestrates tail-loss Monte-Carlo + allocation sweep + walk-forward using
engine primitives + `src/nfo/robustness.py` tail-loss helpers. The heavy math
lives in those modules; this study is orchestration only.

Pipeline
--------
  1. TriggerEvaluator(spec) -> fire dates
  2. group_fires_by_cycle   -> cycle index
  3. select_cycle_matched   -> one trade per firing cycle at pt_variant
  4. Tail-loss injection    -> Monte-Carlo over synthetic max-loss rows
  5. Allocation sweep       -> deterministic equity at N deployment fractions
  6. Walk-forward           -> contiguous-fold train/test on the matched set

Callers (e.g. scripts/nfo/v3_falsification.py) handle legacy-file shaping and
report formatting; this module stays a pure composition over engine +
robustness primitives and returns a structured `FalsificationResult`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from nfo.engine.capital import EquityResult, compute_equity_curves
from nfo.engine.cycles import group_fires_by_cycle
from nfo.engine.metrics import SummaryStats, summary_stats
from nfo.engine.selection import select_cycle_matched
from nfo.engine.triggers import TriggerEvaluator
from nfo.robustness import inject_tail_losses
from nfo.specs.strategy import CapitalSpec, StrategySpec


@dataclass
class FalsificationResult:
    """Composite falsification-study output.

    Attributes
    ----------
    matched_trades
        One-row-per-cycle V3-matched trade frame.
    baseline_stats
        `summary_stats(matched_trades)` — per-trade headline.
    baseline_equity
        `compute_equity_curves(matched_trades, ...)` at `capital_inr`.
    tail_loss
        Long-form Monte-Carlo rows: one per (n_injections, iteration)
        with columns `n_injections`, `iteration`, `total_pnl_fixed`,
        `final_equity_compound`, `max_dd_pct`.
    allocation_sweep
        One row per deployment fraction with columns
        `allocation_fraction`, `total_pnl_fixed`, `total_pnl_compound`,
        `final_equity_compound`, `max_dd_pct`, `sharpe`.
    walkforward
        Contiguous-fold train/test rows (fold 0 skipped — no training data)
        with columns `fold`, `train_n`, `train_win_rate`, `test_n`,
        `test_win_rate`, `test_sharpe`.
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
    tail_loss: pd.DataFrame
    allocation_sweep: pd.DataFrame
    walkforward: pd.DataFrame
    years: float
    capital_inr: float
    pt_variant: str


def _select_matched_trades(
    *,
    spec: StrategySpec,
    features_df: pd.DataFrame,
    atr_series: pd.Series,
    trades_df: pd.DataFrame,
    pt_variant: str,
    event_resolver: Callable | None,
) -> pd.DataFrame:
    """Trigger -> cycle -> cycle_matched selection (engine path)."""
    if features_df.empty or trades_df.empty:
        return pd.DataFrame()
    ev = TriggerEvaluator(spec, event_resolver=event_resolver)
    fires = ev.fire_dates(features_df, atr_series)
    cycles = group_fires_by_cycle(
        fires, features_df,
        underlying=spec.universe.underlyings[0],
        strategy_version=spec.strategy_version,
    )
    return select_cycle_matched(trades_df, cycles, spec, pt_variant=pt_variant)


def _tail_loss_sweep(
    matched: pd.DataFrame,
    *,
    capital_spec: CapitalSpec,
    years: float,
    injections: list[int],
    iterations: int,
    seed: int,
) -> pd.DataFrame:
    """Monte-Carlo loop: for each (n_inj, iteration) row, inject k max-loss
    cycles with a per-iteration RNG seeded as `seed + iteration` (so repeated
    runs with the same seed are bit-identical) and compute equity stats.
    """
    if matched.empty:
        return pd.DataFrame(columns=[
            "n_injections", "iteration", "total_pnl_fixed",
            "final_equity_compound", "max_dd_pct",
        ])
    rows: list[dict] = []
    for n_inj in injections:
        for i in range(iterations):
            rng = np.random.default_rng(seed + i)
            injected = inject_tail_losses(
                matched, n_injections=n_inj, rng=rng, width=100.0,
            )
            eq = compute_equity_curves(
                injected, capital_spec=capital_spec, years=years,
            )
            rows.append({
                "n_injections": n_inj,
                "iteration": i,
                "total_pnl_fixed": float(eq.total_pnl_fixed),
                "final_equity_compound": float(eq.final_equity_compound),
                "max_dd_pct": float(eq.max_drawdown_pct),
            })
    return pd.DataFrame(rows)


def _allocation_sweep(
    matched: pd.DataFrame,
    *,
    capital_inr: float,
    years: float,
    fractions: list[float],
) -> pd.DataFrame:
    """Deterministic equity walk at each deployment fraction.

    Always emits one row per fraction (even when `matched` is empty — the
    row is zero-filled at the starting capital so downstream code can rely on
    `len(allocation_sweep) == len(fractions)`).
    """
    rows: list[dict] = []
    for frac in fractions:
        cs = CapitalSpec(
            fixed_capital_inr=capital_inr, deployment_fraction=frac,
        )
        if matched.empty:
            rows.append({
                "allocation_fraction": float(frac),
                "total_pnl_fixed": 0.0,
                "total_pnl_compound": 0.0,
                "final_equity_compound": float(capital_inr),
                "max_dd_pct": 0.0,
                "sharpe": 0.0,
            })
            continue
        eq = compute_equity_curves(
            matched, capital_spec=cs, years=years,
        )
        rows.append({
            "allocation_fraction": float(frac),
            "total_pnl_fixed": float(eq.total_pnl_fixed),
            "total_pnl_compound": float(eq.total_pnl_compound),
            "final_equity_compound": float(eq.final_equity_compound),
            "max_dd_pct": float(eq.max_drawdown_pct),
            "sharpe": float(eq.sharpe),
        })
    return pd.DataFrame(rows)


def _walkforward(matched: pd.DataFrame, *, folds: int) -> pd.DataFrame:
    """Split `matched` into `folds` contiguous row-index folds.

    Fold 0 has no training data and is skipped; folds 1..N-1 each train on
    rows [0, edge_k) and test on rows [edge_k, edge_{k+1}). Empty test
    folds are skipped so downstream row counts always reflect evaluable
    windows.
    """
    if matched.empty or folds < 2:
        return pd.DataFrame(columns=[
            "fold", "train_n", "train_win_rate",
            "test_n", "test_win_rate", "test_sharpe",
        ])
    n = len(matched)
    edges = [int(round(i * n / folds)) for i in range(folds + 1)]
    rows: list[dict] = []
    for k in range(1, folds):
        train = matched.iloc[:edges[k]]
        test = matched.iloc[edges[k]:edges[k + 1]]
        if test.empty:
            continue
        train_stats = summary_stats(train)
        test_stats = summary_stats(test)
        rows.append({
            "fold": int(k),
            "train_n": int(train_stats.n),
            "train_win_rate": float(train_stats.win_rate),
            "test_n": int(test_stats.n),
            "test_win_rate": float(test_stats.win_rate),
            "test_sharpe": float(test_stats.sharpe),
        })
    return pd.DataFrame(rows)


def run_falsification(
    *,
    spec: StrategySpec,
    features_df: pd.DataFrame,
    atr_series: pd.Series,
    trades_df: pd.DataFrame,
    pt_variant: str,
    capital_inr: float,
    years: float | None = None,
    tail_loss_injections: list[int] | None = None,
    tail_loss_iterations: int = 1000,
    allocation_fractions: list[float] | None = None,
    walkforward_folds: int = 4,
    seed: int = 42,
    event_resolver: Callable | None = None,
) -> FalsificationResult:
    """Engine-backed V3 falsification orchestration (master design §3, §9.3).

    Parameters
    ----------
    spec
        Cycle-matched StrategySpec (e.g. v3_frozen).
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
        Window length for annualisation. When None, derived from
        `features_df["date"]` span.
    tail_loss_injections
        List of n_injections counts to sweep. Defaults to `[1, 2, 3]`.
    tail_loss_iterations
        Monte-Carlo iterations per injection level. Defaults to 1000.
    allocation_fractions
        Deployment fractions to sweep. Defaults to `[0.25, 0.5, 1.0]`.
    walkforward_folds
        Contiguous folds to split matched trades into (default 4).
        Fold 0 is skipped (no training data), so folds 1..N-1 are evaluated.
    seed
        Base RNG seed; each tail-loss iteration uses `default_rng(seed + i)`
        for deterministic reruns.
    event_resolver
        Optional callable `(entry_date, dte) -> severity_str` used by the
        trigger evaluator when the features dataset's event-risk column
        doesn't reflect the target spec's semantics.
    """
    if tail_loss_injections is None:
        tail_loss_injections = [1, 2, 3]
    if allocation_fractions is None:
        allocation_fractions = [0.25, 0.5, 1.0]

    # Years derivation from features window when not supplied.
    if years is None:
        if features_df.empty:
            years_value = 0.0
        else:
            dates = pd.to_datetime(features_df["date"])
            span_days = (dates.max() - dates.min()).days
            years_value = max(span_days / 365.25, 1e-9)
    else:
        years_value = float(years)

    matched = _select_matched_trades(
        spec=spec, features_df=features_df, atr_series=atr_series,
        trades_df=trades_df, pt_variant=pt_variant,
        event_resolver=event_resolver,
    )

    baseline_stats = summary_stats(matched) if not matched.empty else summary_stats(pd.DataFrame())
    baseline_capital = CapitalSpec(fixed_capital_inr=capital_inr)
    baseline_equity = compute_equity_curves(
        matched if not matched.empty else pd.DataFrame(),
        capital_spec=baseline_capital, years=years_value,
    )

    tail_loss = _tail_loss_sweep(
        matched, capital_spec=baseline_capital, years=years_value,
        injections=list(tail_loss_injections),
        iterations=int(tail_loss_iterations),
        seed=int(seed),
    )
    allocation = _allocation_sweep(
        matched, capital_inr=float(capital_inr), years=years_value,
        fractions=list(allocation_fractions),
    )
    walkforward = _walkforward(matched, folds=int(walkforward_folds))

    return FalsificationResult(
        matched_trades=matched,
        baseline_stats=baseline_stats,
        baseline_equity=baseline_equity,
        tail_loss=tail_loss,
        allocation_sweep=allocation,
        walkforward=walkforward,
        years=float(years_value),
        capital_inr=float(capital_inr),
        pt_variant=pt_variant,
    )
