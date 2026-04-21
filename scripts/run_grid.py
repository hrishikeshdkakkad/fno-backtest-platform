"""Full parameter-grid backtest, streams progress and saves incrementally.

Design decisions for API-budget-constrained execution:
- Rate limiter in the client caps us at 5 calls / 62s.
- We reuse the Parquet cache across all configs — so re-running extra params
  costs nothing for contracts already fetched.
- Configs are ordered so that different deltas on the SAME underlying run
  consecutively; that way entries warm the stock-bars cache once.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

from csp.backtest import run_csp_backtest, summarise
from csp.client import MassiveClient
from csp.config import RESULTS_DIR
from csp.strategy import StrategyConfig

console = Console()


# Underlyings that fit a $41k budget for at least one full contract.
# (ticker, div_yield, strike_increment, approx_current_price, notes)
UNDERLYINGS = [
    ("IWM", 0.013, 1.0, 240, "Russell 2000 ETF — ~$24k collateral, highest liquidity of small-caps"),
    ("XLK", 0.008, 1.0, 240, "Tech sector ETF — ~$24k collateral, growth-skewed"),
    ("XLV", 0.014, 1.0, 160, "Healthcare sector ETF — ~$16k collateral, lower IV"),
    ("GLD", 0.000, 1.0, 300, "Gold ETF — ~$30k collateral, uncorrelated diversifier"),
]

# (delta, dte, profit_take, manage_at_dte, stop_loss_mult)
PARAM_GRID = [
    (0.20, 35, 0.50, 21,   None),
    (0.25, 35, 0.50, 21,   None),
    (0.30, 35, 0.50, 21,   None),
    (0.20, 35, 1.00, None, None),   # hold-to-expiry baseline
    (0.30, 35, 1.00, None, None),
    (0.30, 35, 0.50, 21,   2.0),    # add stop-loss
]

START = date(2024, 4, 17)
END = date(2026, 4, 17)


def main() -> None:
    all_summaries: list[dict] = []
    all_trades: list[pd.DataFrame] = []
    started = time.time()

    with MassiveClient() as client:
        for ticker, div, inc, price, note in UNDERLYINGS:
            console.print(f"\n[bold cyan]═══ {ticker} ═══[/] {note}")
            for params in PARAM_GRID:
                td, dte, pt, manage, stop = params
                cfg = StrategyConfig(
                    underlying=ticker,
                    target_delta=td,
                    target_dte=dte,
                    profit_take=pt,
                    manage_at_dte=manage,
                    stop_loss_mult=stop,
                    div_yield=div,
                    strike_increment=inc,
                )
                t0 = time.time()
                try:
                    trades = run_csp_backtest(client, cfg, START, END)
                except Exception as e:
                    console.print(f"[red]ERROR on {ticker} {params}: {e}[/]")
                    continue
                summary = summarise(trades)
                summary.update(
                    underlying=ticker,
                    target_delta=td,
                    target_dte=dte,
                    profit_take=pt,
                    manage_at_dte=manage,
                    stop_loss_mult=stop,
                )
                all_summaries.append(summary)
                if not trades.empty:
                    trades = trades.assign(
                        param_delta=td,
                        param_dte=dte,
                        param_pt=pt,
                        param_manage=manage,
                        param_stop=stop,
                    )
                    all_trades.append(trades)
                elapsed = time.time() - t0
                console.print(
                    f"  Δ={td:.2f} dte={dte} pt={pt:.2f} "
                    f"manage@{manage} stop={stop}  →  "
                    f"n={summary.get('n', 0)} "
                    f"avg/mo=${summary.get('avg_monthly_pnl', 0):,.0f} "
                    f"worst=${summary.get('worst_month_pnl', 0):,.0f} "
                    f"assign={summary.get('assignment_rate', 0):.0%} "
                    f"wins={summary.get('win_rate', 0):.0%} "
                    f"ann={summary.get('annualized_return_on_collateral', 0):.1%} "
                    f"({elapsed:.0f}s)"
                )
                # checkpoint after each config
                _save(all_summaries, all_trades)

    total_elapsed = time.time() - started
    console.print(f"\n[bold green]Done in {total_elapsed/60:.1f} min[/]")
    _report(all_summaries)


def _save(summaries: list[dict], trades_list: list[pd.DataFrame]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summaries).to_csv(RESULTS_DIR / "summary.csv", index=False)
    if trades_list:
        pd.concat(trades_list, ignore_index=True).to_csv(RESULTS_DIR / "trades.csv", index=False)


def _report(summaries: list[dict]) -> None:
    df = pd.DataFrame(summaries)
    if df.empty:
        return
    df = df.sort_values("avg_monthly_pnl", ascending=False)
    t = Table(title="Ranked by avg monthly $ per contract")
    for c in [
        "underlying", "target_delta", "target_dte", "profit_take", "manage_at_dte",
        "stop_loss_mult", "n", "win_rate", "assignment_rate",
        "avg_monthly_pnl", "worst_month_pnl", "avg_collateral",
        "annualized_return_on_collateral",
    ]:
        t.add_column(c, overflow="fold")
    for _, r in df.iterrows():
        cells = []
        for c in t.columns:
            v = r.get(c.header)
            if isinstance(v, float):
                if c.header in ("win_rate", "assignment_rate", "annualized_return_on_collateral"):
                    cells.append(f"{v:.1%}")
                else:
                    cells.append(f"{v:,.2f}")
            else:
                cells.append(str(v))
        t.add_row(*cells)
    console.print(t)


if __name__ == "__main__":
    main()
