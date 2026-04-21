"""Capital-analysis study: ₹10L deployment simulation for cycle-matched strategies.

Engine-backed V3 capital deployment analysis. Replaces the legacy pipeline in
`scripts/nfo/v3_capital_analysis.run_analysis`, which composed
`robustness.get_v3_matched_trades` + `robustness.compute_equity_curves` and
handed the result to a bespoke report writer.

The study wires engine primitives:
  1. TriggerEvaluator(spec) → fire dates
  2. group_fires_by_cycle      → cycle index
  3. select_cycle_matched      → one trade per firing cycle at `pt_variant`
  4. engine.capital.compute_equity_curves → fixed + compounding equity series
  5. engine.metrics.summary_stats        → per-trade stats

Callers (e.g. scripts/nfo/v3_capital_analysis.py) handle report formatting and
legacy-file writes; this module stays as a pure-function composition over the
engine and returns a structured result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from nfo.engine.capital import EquityResult, compute_equity_curves
from nfo.engine.cycles import group_fires_by_cycle
from nfo.engine.metrics import SummaryStats, summary_stats
from nfo.engine.selection import select_cycle_matched
from nfo.engine.triggers import TriggerEvaluator
from nfo.specs.strategy import CapitalSpec, StrategySpec


@dataclass
class CapitalAnalysisResult:
    selected_trades: pd.DataFrame
    equity_result: EquityResult
    stats: SummaryStats
    pt_variant: str
    years: float


def run_capital_analysis(
    *,
    spec: StrategySpec,
    features_df: pd.DataFrame,
    atr_series: pd.Series,
    trades_df: pd.DataFrame,
    pt_variant: str,
    capital_inr: float,
    years: float | None = None,
    event_resolver: Callable | None = None,
) -> CapitalAnalysisResult:
    """Engine-backed V3 capital analysis.

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
        Exit variant to pick from the universe: `pt50` (50% profit-take),
        `hte` (hold-to-expiry), `pt25`, `pt75`, or `dte2`.
    capital_inr
        Starting capital in rupees.
    years
        Window length for annualisation. When None, computed from
        `features_df["date"]` span.
    event_resolver
        Optional callable `(entry_date, dte) -> severity_str` used by the
        trigger evaluator when the features dataset's `event_risk_v3` column
        doesn't reflect the target spec's semantics (e.g. V3 against the
        V0-baked P1 cached parquet).
    """
    ev = TriggerEvaluator(spec, event_resolver=event_resolver)
    fires = ev.fire_dates(features_df, atr_series)

    cycles = group_fires_by_cycle(
        fires, features_df,
        underlying=spec.universe.underlyings[0],
        strategy_version=spec.strategy_version,
    )
    selected = select_cycle_matched(trades_df, cycles, spec, pt_variant=pt_variant)

    if years is None:
        dates = pd.to_datetime(features_df["date"])
        if dates.empty:
            years_value = 0.0
        else:
            span = (dates.max() - dates.min()).days
            years_value = span / 365.25
    else:
        years_value = float(years)

    capital_spec = CapitalSpec(
        fixed_capital_inr=capital_inr,
        deployment_fraction=1.0,
        compounding=False,
    )
    equity = compute_equity_curves(selected, capital_spec=capital_spec, years=years_value)
    stats = summary_stats(selected)

    return CapitalAnalysisResult(
        selected_trades=selected,
        equity_result=equity,
        stats=stats,
        pt_variant=pt_variant,
        years=years_value,
    )
