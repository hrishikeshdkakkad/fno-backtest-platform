"""Engine: trade selection for day_matched / cycle_matched / live_rule (master design §6).

Consumes trade_universe (a DataFrame of candidate trades with metadata + realized
outcomes — legacy name `spread_trades.csv`). For P2, cycle_matched reproduces
legacy `src/nfo/robustness.pick_trade_for_expiry` row-for-row on V3.

For P3-E1, live_rule is the capstone: it resolves entry via
`engine.entry.resolve_entry_date` and runs the per-cycle simulator in
`engine.execution.run_cycle_from_dhan`. This module stays free of a hard
Dhan import — `client` and `under` are duck-typed parameters.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Iterable, Literal

import pandas as pd

from nfo.engine.cycles import CycleFires, selection_id
from nfo.specs.strategy import StrategySpec, UniverseSpec


def select_day_matched(
    trade_universe: pd.DataFrame,
    firing_dates: Iterable[date],
    universe_spec: UniverseSpec,
) -> pd.DataFrame:
    """Return trades whose entry_date is a firing date, matching universe constraints."""
    fire_set = {d.isoformat() if hasattr(d, "isoformat") else str(d) for d in firing_dates}
    df = trade_universe.copy()
    if "underlying" in df.columns:
        df = df[df["underlying"].isin(universe_spec.underlyings)]
    # Delta filter
    df = df[
        (df["param_delta"] - universe_spec.delta_target).abs()
        <= universe_spec.delta_tolerance
    ]
    # Width filter (only when fixed)
    if universe_spec.width_rule == "fixed":
        df = df[df["param_width"] == universe_spec.width_value]
    # Entry date filter
    df["_entry_str"] = df["entry_date"].astype(str)
    df = df[df["_entry_str"].isin(fire_set)]
    return df.drop(columns=["_entry_str"]).reset_index(drop=True)


def select_cycle_matched(
    trade_universe: pd.DataFrame,
    cycles: dict[str, CycleFires],
    strategy_spec: StrategySpec,
    *,
    pt_variant: Literal["pt25", "pt50", "pt75", "hte", "dte2"] | None = None,
) -> pd.DataFrame:
    """Return one trade per cycle.

    Selection rules:
    - Filter trade_universe by universe_spec.underlyings (assumes rows have 'underlying' col
      or infer from expiry_date match).
    - Filter by delta_target (universe_spec.delta_target within delta_tolerance).
    - Filter by width (universe_spec.width_value when width_rule == 'fixed').
    - For each cycle in `cycles`, match trade whose expiry_date == cycle.target_expiry.
    - If pt_variant given, prefer trades with matching param_pt; fall back to first row.
    - Return rows enriched with 'cycle_id', 'first_fire_date', 'selection_id'.
    - Skip cycles with no matching trade (log as warning, not fail).
    """
    universe = strategy_spec.universe
    df = trade_universe.copy()
    if "underlying" in df.columns:
        df = df[df["underlying"].isin(universe.underlyings)]
    df = df[
        (df["param_delta"] - universe.delta_target).abs()
        <= universe.delta_tolerance
    ]
    if universe.width_rule == "fixed":
        df = df[df["param_width"] == universe.width_value]

    pt_variant = pt_variant or strategy_spec.selection_rule.preferred_exit_variant

    rows: list[pd.Series] = []
    for cycle in sorted(cycles.values(), key=lambda c: c.target_expiry):
        exp_str = cycle.target_expiry.isoformat()
        sub = df[df["expiry_date"] == exp_str]
        if sub.empty:
            continue
        picked = _pick_by_pt_variant(sub, pt_variant)
        if picked is None:
            continue
        enriched = picked.copy()
        enriched["cycle_id"] = cycle.cycle_id
        enriched["first_fire_date"] = cycle.first_fire_date.isoformat()
        enriched["selection_id"] = selection_id(
            cycle.cycle_id, strategy_spec.selection_rule.mode, pt_variant,
        )
        rows.append(enriched)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).reset_index(drop=True)


def select_live_rule(
    cycles: dict[str, CycleFires],
    strategy_spec: StrategySpec,
    sessions: Iterable[date],
    *,
    client,                          # DhanClient — not type-imported to keep this module
                                     # free of Dhan hard dependency
    under,                           # Underlying
    spot_daily: pd.DataFrame,
) -> pd.DataFrame:
    """Full live-rule selection.

    For each cycle:
      1. resolve_entry_date(spec, first_fire_date, sessions) -> entry_date
      2. Skip if None (no session on/after fire before expiry)
      3. engine.execution.run_cycle_from_dhan(...) -> SimulatedTrade
      4. Enrich row with cycle_id, selection_id, first_fire_date

    Returns a DataFrame with the same column schema as v3_live_trades_*.csv
    plus canonical id columns.
    """
    # Local imports: keep the module free of heavy deps at import time and
    # allow monkeypatching `nfo.engine.execution.run_cycle_from_dhan` in tests.
    from nfo.engine import execution as _execution
    from nfo.engine.entry import resolve_entry_date

    sessions_list = list(sessions)
    if not cycles:
        return pd.DataFrame()

    rows: list[dict] = []
    for cycle in sorted(cycles.values(), key=lambda c: c.target_expiry):
        entry_date = resolve_entry_date(
            spec=strategy_spec,
            first_fire_date=cycle.first_fire_date,
            sessions=sessions_list,
        )
        if entry_date is None or entry_date >= cycle.target_expiry:
            continue
        sim = _execution.run_cycle_from_dhan(
            client=client, under=under, strategy_spec=strategy_spec,
            entry_date=entry_date, expiry_date=cycle.target_expiry,
            spot_daily=spot_daily,
        )
        if sim is None:
            continue
        row = asdict(sim.spread_trade)
        row["cycle_id"] = sim.cycle_id
        row["trade_id"] = sim.trade_id
        row["first_fire_date"] = cycle.first_fire_date.isoformat()
        row["selection_id"] = selection_id(
            cycle.cycle_id, "live_rule", strategy_spec.exit_rule.variant,
        )
        rows.append(row)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _pick_by_pt_variant(sub: pd.DataFrame, pt_variant: str) -> pd.Series | None:
    if pt_variant == "pt50":
        match = sub[sub["param_pt"] == 0.50]
        return match.iloc[0] if not match.empty else sub.iloc[0]
    if pt_variant == "hte":
        match = sub[sub["param_pt"] == 1.0]
        return match.iloc[0] if not match.empty else sub.iloc[0]
    if pt_variant == "pt25":
        match = sub[sub["param_pt"] == 0.25]
        return match.iloc[0] if not match.empty else sub.iloc[0]
    if pt_variant == "pt75":
        match = sub[sub["param_pt"] == 0.75]
        return match.iloc[0] if not match.empty else sub.iloc[0]
    return sub.iloc[0]
