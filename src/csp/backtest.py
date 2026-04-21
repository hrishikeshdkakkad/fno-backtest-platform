"""Monthly-roll CSP backtester using daily OHLC option bars.

Semantics (deliberately simple and conservative):
- Entry: `target_dte` calendar days before monthly expiry, at daily close
  of the underlying and the chosen put contract.
- Fill model: entry_premium = put close on entry day. Exit_premium = put close
  on exit day. This mirrors the quality of data available on Starter tier
  (no NBBO access). Slippage is applied symmetrically as `slippage_frac` of
  the put's close price — meaning we receive slightly less on entry and pay
  slightly more on exit.
- Exit conditions in priority order:
    1. Put's close ≤ entry_premium * (1 - profit_take)  → profit-take
    2. Stop-loss (if configured) on put's close ≥ entry_premium * stop_loss_mult
    3. If `manage_at_dte` is set, close at that DTE
    4. Expiry: if underlying close ≥ strike → expires worthless; else assigned
- Assignment: we book the realized loss (strike - expiry_close) minus entry_premium.
  Stock P/L after assignment is not carried forward (each trade is standalone);
  this is a conservative assumption that reflects the worst-case "just close it"
  outcome.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

import pandas as pd

from .client import MassiveClient
from .data import load_option_bars, load_stock_bars
from .strategy import StrategyConfig, pick_put_for_cycle
from .universe import (
    latest_trading_day_on_or_before,
    monthly_expirations,
)


@dataclass(slots=True)
class Trade:
    cycle: int
    underlying: str
    entry_date: pd.Timestamp
    expiry: date
    exit_date: pd.Timestamp
    strike: float
    dte_entry: int
    spot_entry: float
    spot_exit: float
    entry_premium: float
    exit_premium: float
    entry_iv: float
    entry_delta: float
    pnl_per_share: float
    pnl_dollars: float          # per contract of 100 shares
    collateral: float           # strike * 100
    return_pct: float           # pnl_dollars / collateral
    outcome: str                # 'profit_take' | 'expired_worthless' | 'assigned' | 'managed'


def _pnl_at_exit(
    entry_premium: float, exit_premium: float, strike: float, spot_exit: float, outcome: str
) -> float:
    """Per-share P/L for a short put from entry to exit.

    - profit_take / expired_worthless / managed: entry_premium - exit_premium
    - assigned: entry_premium - max(strike - spot_exit, 0)
    """
    if outcome == "assigned":
        return entry_premium - max(strike - spot_exit, 0.0)
    return entry_premium - exit_premium


def run_csp_backtest(
    client: MassiveClient,
    cfg: StrategyConfig,
    start: date,
    end: date,
    slippage_frac: float = 0.02,
) -> pd.DataFrame:
    """Run a monthly-roll CSP backtest for one underlying under `cfg`.

    Returns a DataFrame with one row per cycle. Empty rows (skipped cycles) are
    not emitted; the caller can infer missing cycles from `cycle` gaps.
    """
    stock = load_stock_bars(client, cfg.underlying, start, end + timedelta(days=7))
    if stock.empty:
        return pd.DataFrame()
    stock = stock.sort_values("date").reset_index(drop=True)

    expiries = monthly_expirations(start + timedelta(days=cfg.target_dte + 3), end)
    trades: list[Trade] = []

    for cycle, expiry in enumerate(expiries, start=1):
        target_entry = expiry - timedelta(days=cfg.target_dte)
        entry_ts = latest_trading_day_on_or_before(stock, target_entry)
        if entry_ts is None:
            continue

        pick = pick_put_for_cycle(client, cfg, stock, entry_ts, expiry)
        if pick is None:
            continue
        spot_entry = pick.spot_at_entry

        holding = load_option_bars(client, pick.option_ticker, entry_ts.date(), expiry)
        if holding.empty:
            continue
        holding = holding.sort_values("date").reset_index(drop=True)
        post_entry = holding[holding["date"] > entry_ts].reset_index(drop=True)

        entry_prem = pick.entry_premium * (1.0 - slippage_frac)
        profit_threshold = pick.entry_premium * (1.0 - cfg.profit_take)
        stop_threshold = (
            pick.entry_premium * cfg.stop_loss_mult if cfg.stop_loss_mult else None
        )

        exit_date = pd.Timestamp(expiry)
        exit_prem = 0.0
        outcome = "expired_worthless"

        for _, bar in post_entry.iterrows():
            close = float(bar["c"])
            dte_left = (expiry - bar["date"].date()).days

            if close <= profit_threshold:
                exit_date = bar["date"]
                exit_prem = close * (1.0 + slippage_frac)
                outcome = "profit_take"
                break
            if stop_threshold is not None and close >= stop_threshold:
                exit_date = bar["date"]
                exit_prem = close * (1.0 + slippage_frac)
                outcome = "stopped"
                break
            if cfg.manage_at_dte is not None and dte_left <= cfg.manage_at_dte:
                exit_date = bar["date"]
                exit_prem = close * (1.0 + slippage_frac)
                outcome = "managed"
                break

        # settled at expiry
        spot_exit = spot_entry
        if outcome == "expired_worthless":
            expiry_bar = stock.loc[stock["date"] <= pd.Timestamp(expiry)].tail(1)
            if expiry_bar.empty:
                continue
            spot_exit = float(expiry_bar["c"].iloc[0])
            if spot_exit < pick.strike:
                outcome = "assigned"
                # exit_prem for accounting; real P/L computed below
                exit_prem = max(pick.strike - spot_exit, 0.0)
            else:
                exit_prem = 0.0
        else:
            exit_bar = stock.loc[stock["date"] == exit_date]
            if not exit_bar.empty:
                spot_exit = float(exit_bar["c"].iloc[0])

        pnl_per_share = _pnl_at_exit(
            entry_prem, exit_prem, pick.strike, spot_exit, outcome
        )
        collateral = pick.strike * 100.0
        pnl_dollars = pnl_per_share * 100.0
        return_pct = pnl_dollars / collateral if collateral else 0.0

        trades.append(
            Trade(
                cycle=cycle,
                underlying=cfg.underlying,
                entry_date=entry_ts,
                expiry=expiry,
                exit_date=exit_date,
                strike=pick.strike,
                dte_entry=(expiry - entry_ts.date()).days,
                spot_entry=spot_entry,
                spot_exit=spot_exit,
                entry_premium=pick.entry_premium,
                exit_premium=exit_prem,
                entry_iv=pick.entry_iv,
                entry_delta=pick.entry_delta,
                pnl_per_share=pnl_per_share,
                pnl_dollars=pnl_dollars,
                collateral=collateral,
                return_pct=return_pct,
                outcome=outcome,
            )
        )

    return pd.DataFrame([asdict(t) for t in trades])


def summarise(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"n": 0}
    assigned = (trades["outcome"] == "assigned").sum()
    wins = (trades["pnl_dollars"] > 0).sum()
    total_pnl = trades["pnl_dollars"].sum()
    months = len(trades)
    avg_collateral = trades["collateral"].mean()
    return {
        "n": int(months),
        "wins": int(wins),
        "win_rate": float(wins / months),
        "assigned": int(assigned),
        "assignment_rate": float(assigned / months),
        "total_pnl": float(total_pnl),
        "avg_monthly_pnl": float(total_pnl / months),
        "avg_monthly_return_pct": float(trades["return_pct"].mean()),
        "median_monthly_return_pct": float(trades["return_pct"].median()),
        "worst_month_pnl": float(trades["pnl_dollars"].min()),
        "best_month_pnl": float(trades["pnl_dollars"].max()),
        "pnl_std_dollars": float(trades["pnl_dollars"].std()),
        "avg_collateral": float(avg_collateral),
        "avg_entry_delta": float(trades["entry_delta"].mean()),
        "annualized_return_on_collateral": float(
            (total_pnl / avg_collateral) * (12.0 / months)
        ),
    }
