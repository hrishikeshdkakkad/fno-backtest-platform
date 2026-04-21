"""The 'wheel' strategy: sell CSPs until assigned, then sell covered calls on
the assigned stock until called away, then rinse and repeat.

This complements `backtest.run_csp_backtest` which treats each CSP cycle as
standalone (selling assigned shares at expiry close). The wheel typically
recovers some or all of an assignment loss via subsequent covered-call
premium, especially on underlyings with persistent IV above realized vol.

Modeling decisions:
- Covered call uses target delta +0.25 (inversely: we sell calls at ~0.25
  delta, same target-delta logic as the put leg).
- We stop the wheel at the backtest end date and mark-to-market the
  remaining position at the last close.
- Profit-take and manage-DTE rules from StrategyConfig apply to both legs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

import pandas as pd

from . import bsm
from .client import MassiveClient
from .data import load_option_bars, load_stock_bars
from .strategy import (
    StrategyConfig,
    estimate_target_strike,
    pick_put_for_cycle,
    realized_vol,
    round_strike,
)
from .universe import (
    latest_trading_day_on_or_before,
    make_option_ticker,
    monthly_expirations,
)


@dataclass(slots=True)
class WheelEvent:
    cycle: int
    leg: str                 # 'put' or 'call'
    underlying: str
    entry_date: pd.Timestamp
    expiry: date
    exit_date: pd.Timestamp
    strike: float
    spot_entry: float
    spot_exit: float
    entry_premium: float
    exit_premium: float
    pnl_dollars: float
    outcome: str


def _pick_call_for_cycle(
    client: MassiveClient,
    cfg: StrategyConfig,
    stock_df: pd.DataFrame,
    entry_ts: pd.Timestamp,
    expiry: date,
    ref_spot: float,   # strike floor for covered calls
) -> dict | None:
    """Pick an OTM call at ~target_delta_call for the next CC leg.

    We use the same BSM delta targeting but on the call side. Call delta is
    in [0, 1]; target ~0.25 means OTM call.
    Strike must be >= ref_spot (never sell covered calls below our cost basis).
    """
    row = stock_df.loc[stock_df["date"] == entry_ts]
    if row.empty:
        return None
    spot = float(row["c"].iloc[0])
    sigma_hat = realized_vol(stock_df, entry_ts, cfg.vol_window)
    t = max((expiry - entry_ts.date()).days, 1) / 365.0

    # Target call-delta = cfg.target_delta (same magnitude).
    # Call delta ≈ e^(-qT) N(d1). Target p = cfg.target_delta.
    # ⇒ d1 = Φ^-1(target_delta * e^(qT))
    import math
    from scipy.stats import norm
    target = min(max(cfg.target_delta * math.exp(cfg.div_yield * t), 1e-4), 1 - 1e-4)
    d1 = norm.ppf(target)
    est_strike = spot * math.exp(
        -(-d1 * sigma_hat * math.sqrt(t) - (cfg.risk_free_rate - cfg.div_yield + 0.5 * sigma_hat * sigma_hat) * t)
    )
    est_strike = max(est_strike, ref_spot + cfg.strike_increment)
    k0 = round_strike(est_strike, cfg.strike_increment)
    if k0 < ref_spot:
        k0 = ref_spot + cfg.strike_increment

    for k in (k0, k0 + cfg.strike_increment, k0 - cfg.strike_increment, k0 + 2 * cfg.strike_increment):
        if k < ref_spot + cfg.strike_increment * 0.5:
            continue
        ticker = make_option_ticker(cfg.underlying, expiry, "C", k)
        bars = load_option_bars(client, ticker, entry_ts.date(), expiry)
        if bars.empty:
            continue
        e = bars[bars["date"] == entry_ts]
        if e.empty:
            e = bars.head(1)
            if e.empty:
                continue
        cclose = float(e["c"].iloc[0])
        if cclose <= 0:
            continue
        return {
            "strike": k,
            "ticker": ticker,
            "entry_premium": cclose,
            "bars": bars.sort_values("date").reset_index(drop=True),
            "spot": spot,
        }
    return None


def run_wheel(
    client: MassiveClient,
    cfg: StrategyConfig,
    start: date,
    end: date,
    slippage_frac: float = 0.02,
) -> tuple[pd.DataFrame, dict]:
    """Run the CSP → assignment → covered-call wheel from `start` to `end`.

    Returns (events_df, summary).
    """
    stock = load_stock_bars(client, cfg.underlying, start, end + timedelta(days=7))
    if stock.empty:
        return pd.DataFrame(), {}
    stock = stock.sort_values("date").reset_index(drop=True)

    expiries = monthly_expirations(start + timedelta(days=cfg.target_dte + 3), end)
    events: list[WheelEvent] = []

    shares_held = 0            # 0 when in CSP mode, 100 when in CC mode
    cost_basis = 0.0
    cycle = 0
    i = 0
    while i < len(expiries):
        expiry = expiries[i]
        target_entry = expiry - timedelta(days=cfg.target_dte)
        entry_ts = latest_trading_day_on_or_before(stock, target_entry)
        if entry_ts is None:
            i += 1
            continue

        cycle += 1
        if shares_held == 0:
            # CSP leg
            pick = pick_put_for_cycle(client, cfg, stock, entry_ts, expiry)
            if pick is None:
                i += 1
                continue
            bars = load_option_bars(client, pick.option_ticker, entry_ts.date(), expiry)
            if bars.empty:
                i += 1
                continue
            bars = bars.sort_values("date").reset_index(drop=True)
            exit_info = _simulate_exit(
                bars, entry_ts, expiry, pick.entry_premium, cfg, slippage_frac
            )
            spot_exit = _spot_on(stock, exit_info["exit_date"])
            if exit_info["outcome"] == "expired_worthless" and spot_exit is not None and spot_exit < pick.strike:
                outcome = "assigned"
                shares_held = 100
                cost_basis = pick.strike - pick.entry_premium   # net cost per share
                exit_prem = max(pick.strike - spot_exit, 0.0)
                pnl = (pick.entry_premium * (1 - slippage_frac)) - exit_prem
            else:
                outcome = exit_info["outcome"]
                exit_prem = exit_info["exit_prem"]
                pnl = pick.entry_premium * (1 - slippage_frac) - exit_prem
            events.append(WheelEvent(
                cycle=cycle, leg="put",
                underlying=cfg.underlying,
                entry_date=entry_ts, expiry=expiry,
                exit_date=exit_info["exit_date"],
                strike=pick.strike,
                spot_entry=pick.spot_at_entry,
                spot_exit=spot_exit if spot_exit is not None else pick.spot_at_entry,
                entry_premium=pick.entry_premium,
                exit_premium=exit_prem,
                pnl_dollars=pnl * 100.0,
                outcome=outcome,
            ))
        else:
            # CC leg
            cc = _pick_call_for_cycle(client, cfg, stock, entry_ts, expiry, cost_basis)
            if cc is None:
                # roll put — skip this cycle, stay in stock
                i += 1
                continue
            bars = cc["bars"]
            exit_info = _simulate_exit(bars, entry_ts, expiry, cc["entry_premium"], cfg, slippage_frac)
            spot_exit = _spot_on(stock, exit_info["exit_date"])
            if exit_info["outcome"] == "expired_worthless" and spot_exit is not None and spot_exit >= cc["strike"]:
                # called away at strike
                outcome = "called_away"
                exit_prem = max(spot_exit - cc["strike"], 0.0)
                stock_pnl = (cc["strike"] - cost_basis)   # realized stock P/L
                option_pnl = cc["entry_premium"] * (1 - slippage_frac) - exit_prem
                pnl_per_share = stock_pnl + option_pnl
                shares_held = 0
                cost_basis = 0.0
            else:
                outcome = exit_info["outcome"]
                exit_prem = exit_info["exit_prem"]
                pnl_per_share = cc["entry_premium"] * (1 - slippage_frac) - exit_prem
            events.append(WheelEvent(
                cycle=cycle, leg="call",
                underlying=cfg.underlying,
                entry_date=entry_ts, expiry=expiry,
                exit_date=exit_info["exit_date"],
                strike=cc["strike"],
                spot_entry=cc["spot"],
                spot_exit=spot_exit if spot_exit is not None else cc["spot"],
                entry_premium=cc["entry_premium"],
                exit_premium=exit_prem,
                pnl_dollars=pnl_per_share * 100.0,
                outcome=outcome,
            ))
        i += 1

    # mark-to-market any remaining stock
    mtm_pnl = 0.0
    if shares_held > 0:
        last_close = float(stock.iloc[-1]["c"])
        mtm_pnl = (last_close - cost_basis) * shares_held

    df = pd.DataFrame([asdict(e) for e in events])
    total = (df["pnl_dollars"].sum() if not df.empty else 0.0) + mtm_pnl
    months = max((end - start).days / 30.44, 1)
    summary = {
        "underlying": cfg.underlying,
        "target_delta": cfg.target_delta,
        "target_dte": cfg.target_dte,
        "n_events": len(events),
        "n_puts": int((df["leg"] == "put").sum()) if not df.empty else 0,
        "n_calls": int((df["leg"] == "call").sum()) if not df.empty else 0,
        "n_assignments": int((df["outcome"] == "assigned").sum()) if not df.empty else 0,
        "n_called_away": int((df["outcome"] == "called_away").sum()) if not df.empty else 0,
        "total_pnl": float(total),
        "avg_monthly_pnl": float(total / months),
        "mtm_pnl": float(mtm_pnl),
    }
    return df, summary


def _simulate_exit(
    bars: pd.DataFrame,
    entry_ts: pd.Timestamp,
    expiry: date,
    entry_premium: float,
    cfg: StrategyConfig,
    slippage_frac: float,
) -> dict:
    profit_threshold = entry_premium * (1.0 - cfg.profit_take)
    stop_threshold = (
        entry_premium * cfg.stop_loss_mult if cfg.stop_loss_mult else None
    )
    post_entry = bars[bars["date"] > entry_ts].reset_index(drop=True)
    for _, bar in post_entry.iterrows():
        close = float(bar["c"])
        dte_left = (expiry - bar["date"].date()).days
        if close <= profit_threshold:
            return {"exit_date": bar["date"], "exit_prem": close * (1 + slippage_frac), "outcome": "profit_take"}
        if stop_threshold is not None and close >= stop_threshold:
            return {"exit_date": bar["date"], "exit_prem": close * (1 + slippage_frac), "outcome": "stopped"}
        if cfg.manage_at_dte is not None and dte_left <= cfg.manage_at_dte:
            return {"exit_date": bar["date"], "exit_prem": close * (1 + slippage_frac), "outcome": "managed"}
    return {"exit_date": pd.Timestamp(expiry), "exit_prem": 0.0, "outcome": "expired_worthless"}


def _spot_on(stock: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    r = stock.loc[stock["date"] <= ts].tail(1)
    if r.empty:
        return None
    return float(r["c"].iloc[0])
