"""Focused backtest: IWM + XLK at a small delta grid.

Sized to complete in ~40 minutes on the 5-call/min Basic tier, using the
Parquet cache to avoid re-fetching anything across configs. We deliberately
keep the delta grid tight because each delta requires a fresh strike ladder.
"""
from __future__ import annotations

import sys
import time
from datetime import date

import pandas as pd
from rich.console import Console

from csp.backtest import run_csp_backtest, summarise
from csp.client import MassiveClient
from csp.config import RESULTS_DIR
from csp.strategy import StrategyConfig

console = Console()
console.file = sys.stdout  # force line buffering through tee

UNDERLYINGS = [
    ("IWM", 0.013, 1.0, "Russell 2000 ETF"),
    ("XLK", 0.008, 1.0, "Tech sector ETF"),
]

# Each (delta, dte, profit_take, manage_at_dte) needs a fresh strike ladder
# per delta.  Configs sharing the same (delta, dte) re-use cached bars,
# so varying pt/manage is effectively free.
GRID = [
    (0.20, 35, 0.50, 21),
    (0.20, 35, 1.00, None),   # free — same strikes as row above
    (0.30, 35, 0.50, 21),
    (0.30, 35, 1.00, None),
    (0.30, 35, 0.50, None),   # 50% pt, hold through 21 DTE
]

START = date(2024, 4, 17)
END = date(2026, 4, 17)


def main() -> None:
    all_summaries: list[dict] = []
    all_trades: list[pd.DataFrame] = []
    t_start = time.time()

    with MassiveClient() as client:
        for u, div, inc, note in UNDERLYINGS:
            print(f"\n== {u} == ({note})", flush=True)
            for delta, dte, pt, mg in GRID:
                cfg = StrategyConfig(
                    underlying=u,
                    target_delta=delta,
                    target_dte=dte,
                    profit_take=pt,
                    manage_at_dte=mg,
                    div_yield=div,
                    strike_increment=inc,
                )
                t0 = time.time()
                trades = run_csp_backtest(client, cfg, START, END)
                elapsed = time.time() - t0
                s = summarise(trades)
                s.update(underlying=u, target_delta=delta, target_dte=dte,
                         profit_take=pt, manage_at_dte=mg)
                all_summaries.append(s)
                if not trades.empty:
                    trades = trades.assign(
                        param_delta=delta, param_dte=dte, param_pt=pt, param_manage=mg,
                    )
                    all_trades.append(trades)
                print(
                    f"  {u} Δ={delta:.2f} dte={dte} pt={pt:.2f} mg@{mg}  "
                    f"n={s.get('n',0)} avg/mo=${s.get('avg_monthly_pnl',0):,.0f} "
                    f"worst=${s.get('worst_month_pnl',0):,.0f} "
                    f"assign={s.get('assignment_rate',0):.0%} "
                    f"wins={s.get('win_rate',0):.0%} "
                    f"ann={s.get('annualized_return_on_collateral',0):.1%} "
                    f"({elapsed:.0f}s)",
                    flush=True,
                )
                _save(all_summaries, all_trades)

    elapsed = time.time() - t_start
    print(f"\nDone in {elapsed/60:.1f} min", flush=True)


def _save(summaries, trades_list) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summaries).to_csv(RESULTS_DIR / "summary.csv", index=False)
    if trades_list:
        pd.concat(trades_list, ignore_index=True).to_csv(RESULTS_DIR / "trades.csv", index=False)


if __name__ == "__main__":
    main()
