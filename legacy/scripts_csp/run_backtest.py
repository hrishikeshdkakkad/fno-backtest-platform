"""Run CSP backtests across the candidate universe and a parameter grid.

Usage:
    .venv/bin/python scripts/run_backtest.py
"""
from __future__ import annotations

import json
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

# Candidate universe sized to fit $41k budget.
# collateral estimate = approximate current share price * 100.
# div yields are approximate 2025 values; risk-free ~4.5%.
CANDIDATES = [
    # ticker, div_yield, strike_increment, approx_price, notes
    ("SPY", 0.013, 1.0, 700, "S&P 500 ETF — benchmark; >$41k collateral per contract"),
    ("QQQ", 0.006, 1.0, 600, "Nasdaq 100 ETF — >$41k collateral per contract"),
    ("IWM", 0.013, 1.0, 240, "Russell 2000 ETF — fits $41k as 1 contract"),
    ("XLK", 0.008, 1.0, 240, "Tech sector ETF — fits $41k as 1 contract"),
    ("XLV", 0.014, 1.0, 160, "Healthcare sector ETF — fits; lower IV"),
    ("GLD", 0.0, 1.0, 300, "Gold ETF — uncorrelated diversifier"),
]

START = date(2024, 4, 17)
END = date(2026, 4, 17)

PARAM_GRID = [
    # (target_delta, target_dte, profit_take, manage_at_dte)
    (0.16, 45, 0.5, 21),
    (0.20, 45, 0.5, 21),
    (0.25, 45, 0.5, 21),
    (0.20, 35, 0.5, None),
    (0.30, 35, 0.5, None),
]


def run_one(client: MassiveClient, ticker: str, div: float, inc: float, params: tuple):
    td, dte, pt, manage = params
    cfg = StrategyConfig(
        underlying=ticker,
        target_delta=td,
        target_dte=dte,
        profit_take=pt,
        manage_at_dte=manage,
        div_yield=div,
        strike_increment=inc,
    )
    trades = run_csp_backtest(client, cfg, START, END)
    summary = summarise(trades)
    summary.update(
        underlying=ticker,
        target_delta=td,
        target_dte=dte,
        profit_take=pt,
        manage_at_dte=manage,
    )
    return cfg, trades, summary


def main() -> None:
    all_summaries: list[dict] = []
    all_trades: list[pd.DataFrame] = []

    with MassiveClient() as client:
        for ticker, div, inc, price, note in CANDIDATES:
            console.print(f"\n[bold cyan]== {ticker} ==[/] ({note})")
            for params in PARAM_GRID:
                t0 = time.time()
                cfg, trades, summary = run_one(client, ticker, div, inc, params)
                elapsed = time.time() - t0
                console.print(
                    f"  Δ={params[0]:.2f} dte={params[1]} pt={params[2]} "
                    f"manage@{params[3]}  →  n={summary.get('n', 0)} "
                    f"avg/mo=${summary.get('avg_monthly_pnl', 0):,.0f} "
                    f"worst=${summary.get('worst_month_pnl', 0):,.0f} "
                    f"assign={summary.get('assignment_rate', 0):.0%} "
                    f"wins={summary.get('win_rate', 0):.0%}  ({elapsed:.0f}s)"
                )
                if not trades.empty:
                    trades = trades.assign(
                        param_delta=params[0],
                        param_dte=params[1],
                        param_pt=params[2],
                        param_manage=params[3],
                    )
                    all_trades.append(trades)
                all_summaries.append(summary)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summary_df = pd.DataFrame(all_summaries)
    summary_df.to_csv(RESULTS_DIR / "summary.csv", index=False)
    (RESULTS_DIR / "summary.json").write_text(summary_df.to_json(orient="records", indent=2))
    if all_trades:
        pd.concat(all_trades, ignore_index=True).to_csv(RESULTS_DIR / "trades.csv", index=False)

    console.print("\n[bold green]Summary saved to results/[/]")
    _print_top_table(summary_df)


def _print_top_table(df: pd.DataFrame) -> None:
    if df.empty:
        return
    df = df.sort_values("avg_monthly_pnl", ascending=False)
    t = Table(title="All strategies ranked by avg monthly $ P/L")
    cols = [
        "underlying",
        "target_delta",
        "target_dte",
        "profit_take",
        "manage_at_dte",
        "n",
        "win_rate",
        "assignment_rate",
        "avg_monthly_pnl",
        "worst_month_pnl",
        "avg_collateral",
        "annualized_return_on_collateral",
    ]
    for c in cols:
        t.add_column(c)
    for _, row in df.iterrows():
        vals = []
        for c in cols:
            v = row.get(c)
            if isinstance(v, float):
                if c in ("win_rate", "assignment_rate", "annualized_return_on_collateral"):
                    vals.append(f"{v:.1%}")
                else:
                    vals.append(f"{v:,.2f}")
            else:
                vals.append(str(v))
        t.add_row(*vals)
    console.print(t)


if __name__ == "__main__":
    main()
