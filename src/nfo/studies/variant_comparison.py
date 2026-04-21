"""Variant-comparison study (master design §3, §9.3).

Engine-backed V3 evaluation. Legacy variants (V0-V2, V4-V6) remain in
`scripts/nfo/redesign_variants.py` until P2b/P3 migrations. This module ships
only the V3 path, which is the frozen-spec winner per `docs/v3-spec-frozen.md`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from nfo.engine.cycles import group_fires_by_cycle
from nfo.engine.selection import select_cycle_matched
from nfo.engine.triggers import TriggerEvaluator
from nfo.specs.strategy import StrategySpec


@dataclass
class VariantResult:
    name: str
    n_fires: int
    n_matched_trades: int
    win_rate: float
    sharpe: float
    max_loss_rate: float
    firing_rate_per_year: float


def run_variant_comparison_v3(
    *,
    spec: StrategySpec,
    features_df: pd.DataFrame,
    atr_series: pd.Series,
    trades_df: pd.DataFrame,
    event_resolver: Callable | None = None,
) -> VariantResult:
    """Engine-backed V3 evaluation.

    Pipeline:
      1. TriggerEvaluator(spec) -> fire dates
      2. group_fires_by_cycle -> cycle index
      3. select_cycle_matched -> one trade per cycle at spec.preferred_exit_variant
      4. nfo.calibrate.summary_stats -> win_rate, sharpe, max_loss_rate
    """
    ev = TriggerEvaluator(spec, event_resolver=event_resolver)
    fires = ev.fire_dates(features_df, atr_series)

    cycles = group_fires_by_cycle(
        fires,
        features_df,
        underlying=spec.universe.underlyings[0],
        strategy_version=spec.strategy_version,
    )

    selected = select_cycle_matched(
        trades_df,
        cycles,
        spec,
        pt_variant=spec.selection_rule.preferred_exit_variant,
    )

    from nfo import calibrate

    if selected.empty:
        win_rate = 0.0
        sharpe = 0.0
        max_loss_rate = 0.0
        n_matched = 0
    else:
        stats = calibrate.summary_stats(selected)
        win_rate = stats.win_rate
        sharpe = stats.sharpe
        max_loss_rate = stats.max_loss_rate
        n_matched = stats.n

    dates = pd.to_datetime(features_df["date"])
    start = dates.min()
    end = dates.max()
    years = max((end - start).days / 365.25, 1e-9)
    firing_rate = len(fires) / years

    return VariantResult(
        name=spec.strategy_id.upper(),
        n_fires=len(fires),
        n_matched_trades=n_matched,
        win_rate=win_rate,
        sharpe=sharpe,
        max_loss_rate=max_loss_rate,
        firing_rate_per_year=firing_rate,
    )
