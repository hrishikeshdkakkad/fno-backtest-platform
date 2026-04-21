"""Engine: cycle simulation (master design §6, §10).

Splits the legacy `backtest._run_cycle` into:
  - `simulate_cycle_pure` : pure function over pre-fetched leg series
  - `run_cycle_from_dhan` : thin wrapper that fetches via DhanClient

Exit-timing decisions delegate to `engine.exits.decide_exit`. Transaction
costs delegate to `costs.spread_roundtrip_cost`. Month-of-year sizing uses
`signals.month_of_year_size_mult`. Identifier construction uses
`engine.cycles.{cycle_id, trade_id}`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from nfo import signals as _sig
from nfo.backtest import SpreadTrade, _merge_series
from nfo.client import DhanClient
from nfo.costs import spread_roundtrip_cost
from nfo.data import load_fixed_strike_daily
from nfo.engine.cycles import cycle_id as _cycle_id, trade_id as _trade_id
from nfo.engine.exits import decide_exit
from nfo.specs.strategy import StrategySpec
from nfo.spread import SpreadConfig, pick_put_spread
from nfo.universe import Underlying


@dataclass
class SimulatedTrade:
    """Engine-produced cycle result. Superset of SpreadTrade with canonical ids."""

    spread_trade: SpreadTrade    # the legacy-shape row
    cycle_id: str
    trade_id: str


def simulate_cycle_pure(
    *,
    strategy_spec: StrategySpec,
    under: Underlying,
    spread_meta: dict,
    merged_legs: pd.DataFrame,
    entry_date: date,
    expiry_date: date,
    spot_at_expiry: float,
) -> SimulatedTrade:
    """Pure cycle simulation. No Dhan, no I/O.

    Args:
        strategy_spec: drives exit_rule, capital_rule, slippage_rule, etc.
        under: underlying metadata (lot_size, margin_multiplier).
        spread_meta: leg metadata from leg selection. Required keys:
            short_strike, long_strike, short_premium, long_premium,
            net_credit, spot_at_entry, short_delta, short_iv, max_loss.
            Optional: width (overrides computed width when the caller needs
            to match a legacy value verbatim).
        merged_legs: daily net_close series (may be empty when no bar data).
            Columns [date, short_close, long_close, net_close, dte].
        entry_date: cycle entry.
        expiry_date: cycle expiry.
        spot_at_expiry: underlying close on expiry_date (for intrinsic settlement).

    Returns:
        SimulatedTrade with .spread_trade matching the legacy SpreadTrade
        byte-compatibly (when `width` is supplied in spread_meta).
    """
    decision = decide_exit(
        merged_legs,
        exit_spec=strategy_spec.exit_rule,
        net_credit=spread_meta["net_credit"],
        short_strike=spread_meta["short_strike"],
        long_strike=spread_meta["long_strike"],
        spot_at_expiry=spot_at_expiry,
        expiry_date=expiry_date,
    )

    lot = int(under.lot_size)
    # Long-leg ITM intrinsic only matters when settling at expiry; on an
    # early-close branch both legs are exited explicitly so no settlement
    # STT applies.
    long_intrinsic = (
        max(0.0, spread_meta["long_strike"] - spot_at_expiry)
        if not decision.closed_before_expiry
        else 0.0
    )
    txn_cost = spread_roundtrip_cost(
        short_entry_premium=spread_meta["short_premium"],
        short_exit_premium=decision.short_exit_premium,
        long_entry_premium=spread_meta["long_premium"],
        long_exit_premium=decision.long_exit_premium,
        lot=lot,
        closed_before_expiry=decision.closed_before_expiry,
        settle_intrinsic_long=long_intrinsic,
    )
    gross = decision.pnl_per_share * lot
    net = gross - txn_cost

    size_mult = _sig.month_of_year_size_mult(entry_date)

    # CapitalSpec does not carry a margin multiplier in the current schema —
    # the SPAN+exposure proxy lives on `Underlying.margin_multiplier`. We
    # allow `strategy_spec.capital_rule.margin_multiplier` as an override
    # hook for future schema changes, and fall back to the underlying's
    # value (which matches the legacy SpreadConfig default of 1.5).
    margin_mult = getattr(
        strategy_spec.capital_rule, "margin_multiplier", None,
    )
    if margin_mult is None:
        margin_mult = float(under.margin_multiplier)

    # Width for a PUT credit spread: short strike is above long strike, so
    # magnitude = short - long. When `spread_meta` supplies an explicit
    # `width`, honour it verbatim (the legacy SpreadTrade stores
    # `cfg.spread_width`, which equals short-long for consistent picks but
    # can drift if the long-leg lookup snaps to a different strike).
    width = spread_meta.get(
        "width",
        spread_meta["short_strike"] - spread_meta["long_strike"],
    )

    trade = SpreadTrade(
        underlying=under.name,
        cycle_year=entry_date.year,
        cycle_month=entry_date.month,
        entry_date=entry_date,
        expiry_date=expiry_date,
        exit_date=decision.exit_date,
        dte_entry=(expiry_date - entry_date).days,
        dte_exit=decision.dte_exit,
        spot_entry=spread_meta["spot_at_entry"],
        spot_exit=spot_at_expiry,
        short_strike=spread_meta["short_strike"],
        long_strike=spread_meta["long_strike"],
        width=width,
        net_credit=spread_meta["net_credit"],
        net_close_at_exit=decision.net_close_at_exit,
        pnl_per_share=decision.pnl_per_share,
        pnl_contract=net,
        gross_pnl_contract=gross,
        txn_cost_contract=txn_cost,
        buying_power=spread_meta["max_loss"] * lot * margin_mult * size_mult,
        outcome=decision.outcome,
        entry_delta=spread_meta["short_delta"],
        entry_iv=spread_meta["short_iv"],
        size_mult=size_mult,
    )

    cid = _cycle_id(under.name, expiry_date, strategy_spec.strategy_version)
    tid = _trade_id(
        underlying=under.name,
        expiry_date=expiry_date,
        short_strike=spread_meta["short_strike"],
        long_strike=spread_meta["long_strike"],
        width=trade.width,
        delta_target=strategy_spec.universe.delta_target,
        exit_variant=strategy_spec.exit_rule.variant,
        entry_date=entry_date,
    )
    return SimulatedTrade(spread_trade=trade, cycle_id=cid, trade_id=tid)


def run_cycle_from_dhan(
    *,
    client: DhanClient,
    under: Underlying,
    strategy_spec: StrategySpec,
    entry_date: date,
    expiry_date: date,
    spot_daily: pd.DataFrame,
) -> SimulatedTrade | None:
    """Fetch legs via DhanClient, then delegate to ``simulate_cycle_pure``.

    Parity-equivalent of legacy ``backtest._run_cycle``. Cache hits on
    ``data/nfo/rolling/`` mean no network I/O is performed for any cycle
    the cache already covers.
    """
    # Translate StrategySpec → SpreadConfig for the existing pick_put_spread
    # helper. `margin_multiplier` on SpreadConfig is only used for sizing and
    # gets re-resolved inside simulate_cycle_pure; we copy the engine view
    # here just to satisfy the dataclass.
    cap_mm = getattr(strategy_spec.capital_rule, "margin_multiplier", None)
    cfg = SpreadConfig(
        underlying=under.name,
        target_delta=strategy_spec.universe.delta_target,
        target_dte=strategy_spec.universe.dte_target,
        profit_take=strategy_spec.exit_rule.profit_take_fraction or 1.0,
        manage_at_dte=strategy_spec.exit_rule.manage_at_dte,
        margin_multiplier=(
            cap_mm if cap_mm is not None else float(under.margin_multiplier)
        ),
        spread_width=strategy_spec.universe.width_value or 100.0,
    )
    spread = pick_put_spread(
        client, cfg, under,
        expiry_code=1, expiry_flag="MONTH",
        expiry_date=expiry_date,
        entry_date=entry_date,
    )
    if spread is None:
        return None

    short_series = load_fixed_strike_daily(
        client, under,
        expiry_code=1, expiry_flag="MONTH",
        option_type="PUT", strike=spread.short_strike,
        from_date=entry_date.isoformat(),
        to_date=expiry_date.isoformat(),
        offset_range=(-12, 10),
    )
    long_series = load_fixed_strike_daily(
        client, under,
        expiry_code=1, expiry_flag="MONTH",
        option_type="PUT", strike=spread.long_strike,
        from_date=entry_date.isoformat(),
        to_date=expiry_date.isoformat(),
        offset_range=(-15, 8),
    )
    merged = _merge_series(short_series, long_series, expiry_date)

    spot_row = spot_daily[spot_daily["date"] == pd.Timestamp(expiry_date)]
    if spot_row.empty:
        spot_row = spot_daily[spot_daily["date"] <= pd.Timestamp(expiry_date)].tail(1)
    spot_at_expiry = (
        float(spot_row["close"].iloc[0]) if not spot_row.empty else float("nan")
    )

    # Legacy SpreadTrade stores `width=cfg.spread_width` verbatim. When the
    # chain snap finds the exact (short - spread_width) strike, this equals
    # (short - long); but on a fallback snap the two can differ. Honour the
    # legacy storage so the engine wrapper matches byte-for-byte.
    meta = {
        "short_strike": spread.short_strike,
        "long_strike": spread.long_strike,
        "short_premium": spread.short_premium,
        "long_premium": spread.long_premium,
        "net_credit": spread.net_credit,
        "spot_at_entry": spread.spot_at_entry,
        "short_delta": spread.short_delta,
        "short_iv": spread.short_iv,
        "max_loss": spread.max_loss,
        "width": cfg.spread_width,
    }
    return simulate_cycle_pure(
        strategy_spec=strategy_spec, under=under, spread_meta=meta,
        merged_legs=merged, entry_date=entry_date, expiry_date=expiry_date,
        spot_at_expiry=spot_at_expiry,
    )
