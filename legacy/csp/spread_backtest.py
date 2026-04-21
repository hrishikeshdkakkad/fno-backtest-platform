"""Monthly-roll put credit spread backtester.

Semantics mirror `backtest.run_csp_backtest` but operate on the spread's
net premium (= short_close - long_close) each day:

- Entry: target_dte calendar days before monthly expiry, at daily close of
  both legs. net_credit_entry = short_close - long_close (with symmetric
  slippage applied to the whole credit).
- Daily exit loop:
    1. net_spread_close <= net_credit * (1 - profit_take)  → profit_take
    2. net_spread_close >= net_credit * stop_loss_mult     → stopped
    3. DTE <= manage_at_dte                                → managed
- Expiry: settle against the underlying's expiry-day close via the
  closed-form spread payoff (spread.spread_payoff_per_share).

The long leg's close may be missing on some days (low volume); when both
legs don't both have a bar on a given day we skip that day's exit check
rather than synthesizing a price. Conservative.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

import pandas as pd

from .client import MassiveClient
from .data import load_option_bars, load_stock_bars
from .spread import SpreadConfig, pick_put_spread_for_cycle, spread_payoff_per_share
from .universe import latest_trading_day_on_or_before, monthly_expirations


@dataclass(slots=True)
class SpreadTrade:
    cycle: int
    underlying: str
    entry_date: pd.Timestamp
    expiry: date
    exit_date: pd.Timestamp
    short_strike: float
    long_strike: float
    width: float
    dte_entry: int
    spot_entry: float
    spot_exit: float
    short_entry_premium: float
    long_entry_premium: float
    net_credit_entry: float        # per share, post-slippage
    net_credit_exit: float         # per share, post-slippage
    short_entry_iv: float
    short_entry_delta: float
    pnl_per_share: float
    pnl_dollars: float             # per contract of 100 shares
    max_loss: float                # per share (width - net_credit_entry_raw)
    buying_power: float            # max_loss * 100 (per contract dollars)
    return_on_bp: float            # pnl_dollars / buying_power
    outcome: str                   # 'profit_take'|'stopped'|'managed'|'expired_worthless'|'partial_loss'|'max_loss'


def _net_spread_close_on(
    short_bars: pd.DataFrame, long_bars: pd.DataFrame, on: pd.Timestamp
) -> tuple[float, float, float] | None:
    sr = short_bars.loc[short_bars["date"] == on]
    lr = long_bars.loc[long_bars["date"] == on]
    if sr.empty or lr.empty:
        return None
    s = float(sr["c"].iloc[0])
    l = float(lr["c"].iloc[0])
    return s, l, s - l


def run_spread_backtest(
    client: MassiveClient,
    cfg: SpreadConfig,
    start: date,
    end: date,
    slippage_frac: float = 0.02,
) -> pd.DataFrame:
    """Run a monthly-roll put credit spread backtest for one underlying."""
    stock = load_stock_bars(client, cfg.underlying, start, end + timedelta(days=7))
    if stock.empty:
        return pd.DataFrame()
    stock = stock.sort_values("date").reset_index(drop=True)

    expiries = monthly_expirations(start + timedelta(days=cfg.target_dte + 3), end)
    trades: list[SpreadTrade] = []

    for cycle, expiry in enumerate(expiries, start=1):
        target_entry = expiry - timedelta(days=cfg.target_dte)
        entry_ts = latest_trading_day_on_or_before(stock, target_entry)
        if entry_ts is None:
            continue

        pick = pick_put_spread_for_cycle(client, cfg, stock, entry_ts, expiry)
        if pick is None:
            continue

        # Pull full-range bars for both legs (already cached from the picker calls,
        # so these are free API-wise).
        short_bars = load_option_bars(client, pick.short_ticker, entry_ts.date(), expiry)
        long_bars = load_option_bars(client, pick.long_ticker, entry_ts.date(), expiry)
        if short_bars.empty or long_bars.empty:
            continue
        short_bars = short_bars.sort_values("date").reset_index(drop=True)
        long_bars = long_bars.sort_values("date").reset_index(drop=True)

        net_credit_raw = pick.net_credit
        width = pick.short_strike - pick.long_strike
        max_loss_raw = width - net_credit_raw

        # Apply slippage symmetrically: we receive less on entry, pay more on exit.
        net_credit_entry = net_credit_raw * (1.0 - slippage_frac)

        profit_threshold_raw = net_credit_raw * (1.0 - cfg.profit_take)
        stop_threshold_raw = (
            net_credit_raw * cfg.stop_loss_mult if cfg.stop_loss_mult else None
        )

        exit_date = pd.Timestamp(expiry)
        net_credit_exit = 0.0
        outcome = "expired_worthless"

        post_entry_dates = short_bars.loc[short_bars["date"] > entry_ts, "date"]
        for d in post_entry_dates:
            both = _net_spread_close_on(short_bars, long_bars, d)
            if both is None:
                continue
            _s, _l, spread_close = both
            dte_left = (expiry - d.date()).days

            if spread_close <= profit_threshold_raw:
                exit_date = d
                net_credit_exit = spread_close * (1.0 + slippage_frac)
                outcome = "profit_take"
                break
            if stop_threshold_raw is not None and spread_close >= stop_threshold_raw:
                exit_date = d
                net_credit_exit = spread_close * (1.0 + slippage_frac)
                outcome = "stopped"
                break
            if cfg.manage_at_dte is not None and dte_left <= cfg.manage_at_dte:
                exit_date = d
                net_credit_exit = spread_close * (1.0 + slippage_frac)
                outcome = "managed"
                break

        # Spot at exit (for reporting; also needed if we fell through to expiry)
        if outcome == "expired_worthless":
            expiry_bar = stock.loc[stock["date"] <= pd.Timestamp(expiry)].tail(1)
            if expiry_bar.empty:
                continue
            spot_exit = float(expiry_bar["c"].iloc[0])
            pnl_per_share_raw, outcome = spread_payoff_per_share(
                pick.short_strike, pick.long_strike, net_credit_raw, spot_exit
            )
            # Convert to post-slippage P/L. At expiry we're settling mechanically,
            # so only entry-side slippage matters.
            pnl_per_share = pnl_per_share_raw - net_credit_raw * slippage_frac
            net_credit_exit = net_credit_raw - pnl_per_share_raw  # intrinsic
        else:
            exit_bar = stock.loc[stock["date"] == exit_date]
            spot_exit = (
                float(exit_bar["c"].iloc[0]) if not exit_bar.empty else pick.spot_at_entry
            )
            pnl_per_share = net_credit_entry - net_credit_exit

        pnl_dollars = pnl_per_share * 100.0
        bp = max_loss_raw * 100.0
        return_on_bp = pnl_dollars / bp if bp > 0 else 0.0

        trades.append(SpreadTrade(
            cycle=cycle,
            underlying=cfg.underlying,
            entry_date=entry_ts,
            expiry=expiry,
            exit_date=exit_date,
            short_strike=pick.short_strike,
            long_strike=pick.long_strike,
            width=width,
            dte_entry=(expiry - entry_ts.date()).days,
            spot_entry=pick.spot_at_entry,
            spot_exit=spot_exit,
            short_entry_premium=pick.short_premium,
            long_entry_premium=pick.long_premium,
            net_credit_entry=net_credit_entry,
            net_credit_exit=net_credit_exit,
            short_entry_iv=pick.short_iv,
            short_entry_delta=pick.short_delta,
            pnl_per_share=pnl_per_share,
            pnl_dollars=pnl_dollars,
            max_loss=max_loss_raw,
            buying_power=bp,
            return_on_bp=return_on_bp,
            outcome=outcome,
        ))

    return pd.DataFrame([asdict(t) for t in trades])


def summarise_spread(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"n": 0}
    wins = (trades["pnl_dollars"] > 0).sum()
    max_losses = (trades["outcome"] == "max_loss").sum()
    total_pnl = trades["pnl_dollars"].sum()
    months = len(trades)
    avg_bp = trades["buying_power"].mean()
    return {
        "n": int(months),
        "wins": int(wins),
        "win_rate": float(wins / months),
        "max_losses": int(max_losses),
        "max_loss_rate": float(max_losses / months),
        "total_pnl": float(total_pnl),
        "avg_monthly_pnl": float(total_pnl / months),
        "median_monthly_pnl": float(trades["pnl_dollars"].median()),
        "worst_month_pnl": float(trades["pnl_dollars"].min()),
        "best_month_pnl": float(trades["pnl_dollars"].max()),
        "pnl_std_dollars": float(trades["pnl_dollars"].std()),
        "avg_buying_power": float(avg_bp),
        "avg_net_credit": float(trades["net_credit_entry"].mean()),
        "avg_return_on_bp_per_cycle": float(trades["return_on_bp"].mean()),
        "annualized_return_on_bp": float((total_pnl / avg_bp) * (12.0 / months)),
    }
