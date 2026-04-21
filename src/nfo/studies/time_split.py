"""Time-split validation study (master design §3, §9.3).

Engine-backed train/test split validation. For a cycle-matched StrategySpec,
compose triggers -> cycles -> cycle_matched selection, split the selected
trades by entry_date into train and test windows, and compute per-subset
summary statistics plus a verdict.

Replaces the V3 branch of the legacy iteration over V0-V6 variants in
`scripts/nfo/time_split_validate.py::_legacy_main` (the multi-variant loop
remains in the script; only the V3 evaluation routes through this module).

Verdict semantics:
  - "no_fires"     — zero train trades (strategy never fired in-sample).
  - "inconclusive" — test-set below the statistical-significance threshold
    (default: < 10 trades) — cannot reject the overfitting hypothesis.
  - "holds_up"     — test Sharpe positive AND |train_win - test_win| <= 0.10.
  - "broken"       — everything else (Sharpe flip or win-rate gap > 10pp).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Literal

import pandas as pd

from nfo.engine.cycles import group_fires_by_cycle
from nfo.engine.metrics import SummaryStats, summary_stats
from nfo.engine.selection import select_cycle_matched
from nfo.engine.triggers import TriggerEvaluator
from nfo.specs.strategy import StrategySpec


Verdict = Literal["holds_up", "inconclusive", "broken", "no_fires"]


@dataclass
class TimeSplitResult:
    train_stats: SummaryStats
    test_stats: SummaryStats
    verdict: Verdict
    n_train: int
    n_test: int
    train_window: tuple[date, date]
    test_window: tuple[date, date]
    train_trades: pd.DataFrame
    test_trades: pd.DataFrame


def _empty_stats() -> SummaryStats:
    return SummaryStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _split_by_entry_date(
    selected: pd.DataFrame,
    lo: date,
    hi: date,
) -> pd.DataFrame:
    """Return the subset of `selected` whose entry_date falls in [lo, hi]."""
    if selected.empty or "entry_date" not in selected.columns:
        return pd.DataFrame(columns=selected.columns)
    entries = pd.to_datetime(selected["entry_date"])
    mask = (entries >= pd.Timestamp(lo)) & (entries <= pd.Timestamp(hi))
    return selected.loc[mask].reset_index(drop=True)


def _decide_verdict(
    train_trades: pd.DataFrame,
    test_trades: pd.DataFrame,
    train_stats: SummaryStats,
    test_stats: SummaryStats,
    *,
    inconclusive_threshold_trades: int,
) -> Verdict:
    if len(train_trades) == 0:
        return "no_fires"
    if len(test_trades) < inconclusive_threshold_trades:
        return "inconclusive"
    if (
        test_stats.sharpe > 0
        and abs(test_stats.win_rate - train_stats.win_rate) <= 0.10
    ):
        return "holds_up"
    return "broken"


def run_time_split(
    *,
    spec: StrategySpec,
    features_df: pd.DataFrame,
    atr_series: pd.Series,
    trades_df: pd.DataFrame,
    train_window: tuple[date, date],
    test_window: tuple[date, date],
    inconclusive_threshold_trades: int = 10,
    pt_variant: str | None = None,
    event_resolver: Callable | None = None,
) -> TimeSplitResult:
    """Engine-backed train/test time-split validation (master design §3, §9.3).

    Pipeline:
      1. TriggerEvaluator(spec) -> fire dates
      2. group_fires_by_cycle -> cycle index
      3. select_cycle_matched -> one trade per cycle at `pt_variant`
         (defaults to spec.selection_rule.preferred_exit_variant)
      4. Split selected trades by `entry_date` into train / test windows
         (inclusive bounds).
      5. summary_stats on each subset.
      6. Verdict derived from subset sizes + sharpe / win-rate deltas.

    Returns a `TimeSplitResult` carrying per-subset stats, the subset frames,
    both window bounds, and the verdict.
    """
    if features_df.empty:
        empty = _empty_stats()
        return TimeSplitResult(
            train_stats=empty,
            test_stats=empty,
            verdict="no_fires",
            n_train=0,
            n_test=0,
            train_window=train_window,
            test_window=test_window,
            train_trades=pd.DataFrame(),
            test_trades=pd.DataFrame(),
        )

    ev = TriggerEvaluator(spec, event_resolver=event_resolver)
    fires = ev.fire_dates(features_df, atr_series)
    cycles = group_fires_by_cycle(
        fires, features_df,
        underlying=spec.universe.underlyings[0],
        strategy_version=spec.strategy_version,
    )
    pt_variant = pt_variant or spec.selection_rule.preferred_exit_variant
    selected = select_cycle_matched(trades_df, cycles, spec, pt_variant=pt_variant)

    train_trades = _split_by_entry_date(selected, train_window[0], train_window[1])
    test_trades = _split_by_entry_date(selected, test_window[0], test_window[1])

    train_stats = summary_stats(train_trades) if not train_trades.empty else _empty_stats()
    test_stats = summary_stats(test_trades) if not test_trades.empty else _empty_stats()

    verdict = _decide_verdict(
        train_trades, test_trades, train_stats, test_stats,
        inconclusive_threshold_trades=inconclusive_threshold_trades,
    )

    return TimeSplitResult(
        train_stats=train_stats,
        test_stats=test_stats,
        verdict=verdict,
        n_train=int(len(train_trades)),
        n_test=int(len(test_trades)),
        train_window=train_window,
        test_window=test_window,
        train_trades=train_trades,
        test_trades=test_trades,
    )
