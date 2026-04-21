"""Per-cycle simulation loop for NFO put credit spreads.

For each monthly cycle:
  1. Pick short+long leg on entry_date via `spread.pick_put_spread`
  2. Reconstruct daily close of each leg via `data.load_fixed_strike_daily`
  3. Walk forward daily:
       - profit-take: close when net_close ≤ (1 - profit_take) × net_credit
       - stop-loss: close when net_close ≥ stop_multiple × net_credit (disabled v1)
       - manage: close at manage_at_dte if still open
       - settle: use spot-at-expiry intrinsic payoff if untouched
  4. Record one SpreadTrade row.

spot_at_expiry comes from the underlying's daily close on expiry_date — NOT from
the reconstructed option series, which can drop off when spot drifts > ATM±10.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import pandas as pd

from . import signals as _sig
from . import spread as _sp
from .calendar_nfo import MonthlyCycle, build_cycles
from .client import DhanClient
from .costs import spread_roundtrip_cost
from .data import load_fixed_strike_daily, load_underlying_daily
from .spread import SpreadConfig, pick_put_spread, spread_payoff_per_share
from .universe import Underlying


@dataclass(slots=True)
class SpreadTrade:
    underlying: str
    cycle_year: int
    cycle_month: int
    entry_date: date
    expiry_date: date
    exit_date: date
    dte_entry: int
    dte_exit: int
    spot_entry: float
    spot_exit: float
    short_strike: float
    long_strike: float
    width: float
    net_credit: float
    net_close_at_exit: float
    pnl_per_share: float
    # `pnl_contract` is NET of transaction costs (STT + NSE + SEBI + GST +
    # brokerage + settlement STT on ITM auto-exercise). `gross_pnl_contract`
    # and `txn_cost_contract` preserve the gross/cost split for audit.
    pnl_contract: float
    gross_pnl_contract: float
    txn_cost_contract: float
    buying_power: float
    outcome: str     # expired_worthless / partial_loss / max_loss / profit_take / managed
    entry_delta: float
    entry_iv: float
    size_mult: float = 1.0   # month-of-year buying-power multiplier (see signals.py)


def _manage_exit(
    merged: pd.DataFrame,
    cfg: SpreadConfig,
    net_credit: float,
) -> tuple[pd.Series | None, str]:
    """Scan daily series for profit-take or manage-at-DTE. Return (row, outcome)
    or (None, '') if neither triggered.
    """
    if cfg.profit_take < 1.0:
        # profit-take at `profit_take` fraction of max credit → buy back at
        # (1 - profit_take) × credit or less.
        buyback_threshold = (1.0 - cfg.profit_take) * net_credit
        hits = merged[merged["net_close"] <= buyback_threshold]
        if not hits.empty:
            return hits.iloc[0], "profit_take"
    if cfg.manage_at_dte is not None:
        manage_rows = merged[merged["dte"] <= cfg.manage_at_dte]
        if not manage_rows.empty:
            return manage_rows.iloc[0], "managed"
    return None, ""


def _run_cycle(
    client: DhanClient,
    cfg: SpreadConfig,
    under: Underlying,
    cycle: MonthlyCycle,
    spot_daily: pd.DataFrame,
) -> SpreadTrade | None:
    spread = pick_put_spread(
        client, cfg, under,
        expiry_code=1, expiry_flag="MONTH",
        expiry_date=cycle.expiry_date,
        entry_date=cycle.entry_target_date,
    )
    if spread is None:
        return None

    lot = under.lot_size
    short_series = load_fixed_strike_daily(
        client, under,
        expiry_code=1, expiry_flag="MONTH",
        option_type="PUT", strike=spread.short_strike,
        from_date=cycle.entry_target_date.isoformat(),
        to_date=cycle.expiry_date.isoformat(),
        offset_range=(-12, 10),
    )
    long_series = load_fixed_strike_daily(
        client, under,
        expiry_code=1, expiry_flag="MONTH",
        option_type="PUT", strike=spread.long_strike,
        from_date=cycle.entry_target_date.isoformat(),
        to_date=cycle.expiry_date.isoformat(),
        offset_range=(-15, 8),
    )
    merged = _merge_series(short_series, long_series, cycle.expiry_date)

    # Find spot at expiry from the underlying index (authoritative).
    spot_row = spot_daily[spot_daily["date"] == pd.Timestamp(cycle.expiry_date)]
    if spot_row.empty:
        spot_row = spot_daily[spot_daily["date"] <= pd.Timestamp(cycle.expiry_date)].tail(1)
    spot_exit = float(spot_row["close"].iloc[0]) if not spot_row.empty else float("nan")

    exit_row, outcome = _manage_exit(merged, cfg, spread.net_credit) if not merged.empty else (None, "")
    if exit_row is not None:
        net_close_at_exit = float(exit_row["net_close"])
        exit_date = exit_row["date"].date()
        dte_exit = int(exit_row["dte"])
        pnl_per_share = spread.net_credit - net_close_at_exit
        closed_before_expiry = True
        # Split the observed net_close back into short-leg and long-leg close
        # prices using the last merged bar's actual leg closes (avoids naïvely
        # assigning all of net_close to one leg).
        last = exit_row
        short_exit_prem = float(last.get("short_close", net_close_at_exit))
        long_exit_prem = float(last.get("long_close", 0.0))
    else:
        # Settle at expiry using spot.
        pnl_per_share, outcome = spread_payoff_per_share(
            spread.short_strike, spread.long_strike, spread.net_credit, spot_exit,
        )
        net_close_at_exit = spread.net_credit - pnl_per_share
        exit_date = cycle.expiry_date
        dte_exit = 0
        closed_before_expiry = False
        # No explicit exit orders. Cost model only needs entry premiums plus
        # settlement STT on the long leg if it's ITM.
        short_exit_prem = 0.0
        long_exit_prem = 0.0

    lot_int = int(lot)
    # Long leg is ITM only if spot expires below the long strike. Short-leg
    # intrinsic is the counterparty's cost — we pass 0.
    long_intrinsic = max(0.0, spread.long_strike - spot_exit) if not closed_before_expiry else 0.0
    txn_cost_contract = spread_roundtrip_cost(
        short_entry_premium=spread.short_premium,
        short_exit_premium=short_exit_prem,
        long_entry_premium=spread.long_premium,
        long_exit_premium=long_exit_prem,
        lot=lot_int,
        closed_before_expiry=closed_before_expiry,
        settle_intrinsic_long=long_intrinsic,
    )

    gross_pnl_contract = pnl_per_share * lot
    pnl_contract_net = gross_pnl_contract - txn_cost_contract

    # Month-of-year sizing multiplier (docs/india-fno-nuances.md §4 + §8):
    # reduces May deployments (pre-election/budget vol), boosts March/December
    # (fiscal-year position clearing → negative VIX drift). Applied to
    # buying_power; the PnL numbers are per-contract and stay unscaled so the
    # grid search continues to optimize contract-level economics.
    size_mult = _sig.month_of_year_size_mult(cycle.entry_target_date)

    return SpreadTrade(
        underlying=under.name,
        cycle_year=cycle.year,
        cycle_month=cycle.month,
        entry_date=cycle.entry_target_date,
        expiry_date=cycle.expiry_date,
        exit_date=exit_date,
        dte_entry=(cycle.expiry_date - cycle.entry_target_date).days,
        dte_exit=dte_exit,
        spot_entry=spread.spot_at_entry,
        spot_exit=spot_exit,
        short_strike=spread.short_strike,
        long_strike=spread.long_strike,
        width=cfg.spread_width,
        net_credit=spread.net_credit,
        net_close_at_exit=net_close_at_exit,
        pnl_per_share=pnl_per_share,
        pnl_contract=pnl_contract_net,
        gross_pnl_contract=gross_pnl_contract,
        txn_cost_contract=txn_cost_contract,
        buying_power=spread.max_loss * lot * cfg.margin_multiplier * size_mult,
        outcome=outcome,
        entry_delta=spread.short_delta,
        entry_iv=spread.short_iv,
        size_mult=size_mult,
    )


def _merge_series(short_series: pd.DataFrame, long_series: pd.DataFrame, expiry: date) -> pd.DataFrame:
    if short_series.empty or long_series.empty:
        return pd.DataFrame(columns=["date", "net_close", "dte"])
    merged = short_series[["date", "close"]].rename(columns={"close": "short_close"}).merge(
        long_series[["date", "close"]].rename(columns={"close": "long_close"}),
        on="date", how="inner",
    ).sort_values("date").reset_index(drop=True)
    merged["net_close"] = merged["short_close"] - merged["long_close"]
    # A put credit spread's net close must lie in [0, width]. Anything
    # outside that range is a stale-bar / illiquidity artefact (e.g. one
    # leg has a fresh print and the other has yesterday's close). Drop
    # those bars so they don't trigger false profit-takes or distort P&L.
    if "short_close" in merged.columns and not merged.empty:
        # We don't know spread_width here, but any negative net_close is
        # physically impossible; any net_close above max(short_close, long_close)
        # also impossible. Filter the clearly bad rows.
        bad = (merged["net_close"] < 0) | (merged["short_close"] < merged["long_close"])
        merged = merged[~bad].reset_index(drop=True)
    merged["dte"] = (pd.Timestamp(expiry) - merged["date"]).dt.days
    return merged


def run_spread_backtest(
    client: DhanClient,
    cfg: SpreadConfig,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Iterate monthly cycles in [start, end]. Returns a DataFrame of trades."""
    from .universe import get as get_under

    under = get_under(cfg.underlying)
    spot_daily = load_underlying_daily(
        client, under,
        from_date=(pd.Timestamp(start) - pd.Timedelta(days=10)).date().isoformat(),
        to_date=(pd.Timestamp(end) + pd.Timedelta(days=10)).date().isoformat(),
    )
    cycles = build_cycles(under, spot_daily, start, end, target_dte=cfg.target_dte)
    trades: list[SpreadTrade] = []
    for cyc in cycles:
        try:
            t = _run_cycle(client, cfg, under, cyc, spot_daily)
        except Exception as exc:  # log-and-skip in backtest loops
            print(f"  !! {under.name} {cyc.year}-{cyc.month:02d}: {type(exc).__name__}: {exc}", flush=True)
            continue
        if t is None:
            continue
        trades.append(t)
    return pd.DataFrame([asdict(t) for t in trades])


def summarise_spread(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"n": 0}
    total_pnl = float(trades["pnl_contract"].sum())
    avg_bp = float(trades["buying_power"].mean())
    wins = (trades["pnl_per_share"] > 0).sum()
    return {
        "n": int(len(trades)),
        "win_rate": float(wins / len(trades)),
        "avg_pnl_contract": float(trades["pnl_contract"].mean()),
        "total_pnl_contract": total_pnl,
        "worst_cycle_pnl": float(trades["pnl_contract"].min()),
        "best_cycle_pnl": float(trades["pnl_contract"].max()),
        "avg_buying_power": avg_bp,
        "avg_return_on_bp_per_cycle": float(trades["pnl_contract"].mean() / avg_bp) if avg_bp > 0 else 0.0,
        "max_loss_rate": float((trades["outcome"] == "max_loss").mean()),
        "expired_worthless_rate": float((trades["outcome"] == "expired_worthless").mean()),
        "profit_take_rate": float((trades["outcome"] == "profit_take").mean()),
        "managed_rate": float((trades["outcome"] == "managed").mean()),
    }
