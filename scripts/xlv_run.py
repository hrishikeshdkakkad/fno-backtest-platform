"""Quick XLV-only backtest at the two most informative configs."""
from __future__ import annotations

import sys
from datetime import date

import pandas as pd

from csp.backtest import run_csp_backtest, summarise
from csp.client import MassiveClient
from csp.config import RESULTS_DIR
from csp.strategy import StrategyConfig

START = date(2024, 4, 17)
END = date(2026, 4, 17)

GRID = [
    (0.20, 35, 1.00, None),   # hold-to-expiry baseline
    (0.30, 35, 1.00, None),
]


def main():
    summaries = []
    trades_list = []
    with MassiveClient() as c:
        for delta, dte, pt, mg in GRID:
            cfg = StrategyConfig(
                underlying="XLV",
                target_delta=delta,
                target_dte=dte,
                profit_take=pt,
                manage_at_dte=mg,
                div_yield=0.014,
                strike_increment=1.0,
            )
            trades = run_csp_backtest(c, cfg, START, END)
            s = summarise(trades)
            s.update(underlying="XLV", target_delta=delta, target_dte=dte,
                     profit_take=pt, manage_at_dte=mg)
            summaries.append(s)
            if not trades.empty:
                trades = trades.assign(param_delta=delta, param_dte=dte,
                                       param_pt=pt, param_manage=mg)
                trades_list.append(trades)
            print(
                f"XLV Δ={delta:.2f} pt={pt} mg@{mg}  n={s.get('n',0)} "
                f"avg/mo=${s.get('avg_monthly_pnl',0):,.0f} "
                f"worst=${s.get('worst_month_pnl',0):,.0f} "
                f"ann={s.get('annualized_return_on_collateral',0):.1%}",
                flush=True,
            )
    # Append to existing summary/trades
    existing_sum = pd.read_csv(RESULTS_DIR / "summary.csv") if (RESULTS_DIR / "summary.csv").exists() else pd.DataFrame()
    new_sum = pd.DataFrame(summaries)
    combined_sum = pd.concat([existing_sum, new_sum], ignore_index=True)
    combined_sum.to_csv(RESULTS_DIR / "summary.csv", index=False)
    if trades_list:
        existing_t = pd.read_csv(RESULTS_DIR / "trades.csv") if (RESULTS_DIR / "trades.csv").exists() else pd.DataFrame()
        new_t = pd.concat(trades_list, ignore_index=True)
        combined_t = pd.concat([existing_t, new_t], ignore_index=True)
        combined_t.to_csv(RESULTS_DIR / "trades.csv", index=False)


if __name__ == "__main__":
    main()
