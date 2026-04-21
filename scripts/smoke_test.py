"""Single-cycle smoke test: ensures ticker construction + delta targeting work.

Runs one monthly CSP cycle for IWM, June 2024 expiry, 0.20 delta. Prints the
selected contract, entry details, and final outcome.
"""
from __future__ import annotations

from datetime import date

from rich.console import Console

from csp.client import MassiveClient
from csp.data import load_stock_bars
from csp.strategy import StrategyConfig, pick_put_for_cycle
from csp.universe import (
    latest_trading_day_on_or_before,
    monthly_expirations,
)

console = Console()


def main():
    cfg = StrategyConfig(underlying="IWM", target_delta=0.20, target_dte=35, div_yield=0.013)
    with MassiveClient() as c:
        stock = load_stock_bars(c, "IWM", date(2024, 4, 17), date(2024, 7, 1))
        console.print(f"IWM bars loaded: {len(stock)}")
        expiries = monthly_expirations(date(2024, 6, 1), date(2024, 7, 31))
        console.print(f"Monthly expiries: {expiries!r}")
        expiry = expiries[0]  # June 2024
        target_entry = date(expiry.year, expiry.month, expiry.day)
        # We want entry = expiry - target_dte
        from datetime import timedelta
        entry_target = expiry - timedelta(days=cfg.target_dte)
        entry_ts = latest_trading_day_on_or_before(stock, entry_target)
        console.print(f"Expiry {expiry}, entry target {entry_target}, actual entry {entry_ts.date()}")
        pick = pick_put_for_cycle(c, cfg, stock, entry_ts, expiry)
        if pick is None:
            console.print("[red]No pick![/]")
            return
        console.print(f"Picked: strike ${pick.strike} ({pick.option_ticker})")
        console.print(f"  entry_premium: ${pick.entry_premium:.2f}/sh  (${pick.entry_premium*100:.0f}/contract)")
        console.print(f"  IV: {pick.entry_iv:.1%}  delta: {pick.entry_delta:.3f}  est_strike=${pick.estimated_strike:.2f}")
        console.print(f"  collateral required: ${pick.strike*100:,.0f}")


if __name__ == "__main__":
    main()
