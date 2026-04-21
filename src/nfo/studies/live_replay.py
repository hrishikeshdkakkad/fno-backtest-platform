"""Live-replay study: run a live_rule strategy through the engine end-to-end
(master design §13.3).

Composes triggers → cycles → select_live_rule. Returns selected trades +
summary stats. Callers handle reporting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from nfo.engine.cycles import group_fires_by_cycle
from nfo.engine.metrics import SummaryStats, summary_stats
from nfo.engine.selection import select_live_rule
from nfo.engine.triggers import TriggerEvaluator
from nfo.specs.strategy import StrategySpec


@dataclass
class LiveReplayResult:
    selected_trades: pd.DataFrame
    stats: SummaryStats
    n_cycles: int
    n_skipped: int


def run_live_replay(
    *,
    spec: StrategySpec,
    features_df: pd.DataFrame,
    atr_series: pd.Series,
    spot_daily: pd.DataFrame,
    client,
    under,
    event_resolver: Callable | None = None,
) -> LiveReplayResult:
    """End-to-end live-rule replay for a StrategySpec.

    features_df must have `date` (datetime64) and `target_expiry` (ISO-str) columns.
    spot_daily must have `date` (datetime64) and `close`.
    client + under are passed into engine.execution.run_cycle_from_dhan.
    """
    if spec.selection_rule.mode != "live_rule":
        raise ValueError(
            f"live_replay requires selection_rule.mode='live_rule', "
            f"got {spec.selection_rule.mode!r}"
        )
    ev = TriggerEvaluator(spec, event_resolver=event_resolver)
    fires = ev.fire_dates(features_df, atr_series)
    cycles = group_fires_by_cycle(
        fires, features_df,
        underlying=spec.universe.underlyings[0],
        strategy_version=spec.strategy_version,
    )
    sessions = [d.date() for d in pd.to_datetime(spot_daily["date"]).sort_values()]

    selected = select_live_rule(
        cycles, spec, sessions,
        client=client, under=under, spot_daily=spot_daily,
    )
    stats = (
        summary_stats(selected)
        if not selected.empty
        else SummaryStats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    )
    n_skipped = len(cycles) - len(selected)
    return LiveReplayResult(
        selected_trades=selected, stats=stats,
        n_cycles=len(cycles), n_skipped=n_skipped,
    )
