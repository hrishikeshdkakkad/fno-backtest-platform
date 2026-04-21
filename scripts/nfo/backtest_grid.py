"""Phase-3 grid backtest.

Strategy grid adapted to Dhan's ATM±10 cap (wide spreads at low delta are
infeasible). Runs NIFTY and BANKNIFTY across 4 configs each; writes
results/nfo/spread_summary.csv and spread_trades.csv with incremental
checkpoints after every config.
"""
from __future__ import annotations

import time
from datetime import date

import pandas as pd

from nfo.backtest import run_spread_backtest, summarise_spread
from nfo.client import DhanClient
from nfo.config import RESULTS_DIR
from nfo.spread import SpreadConfig

START = date(2024, 1, 1)
END = date(2026, 4, 30)

# (underlying, target_delta, width, profit_take, manage_at_dte)
# Widths tuned for the ATM±10 cap at 35 DTE. Hold-to-expiry first (dominated
# yield in the existing US backtest).
GRID = [
    # NIFTY — narrow spreads that fit within ATM±10
    ("NIFTY",     0.30, 100.0, 1.00, None),
    ("NIFTY",     0.30, 150.0, 1.00, None),
    ("NIFTY",     0.30, 100.0, 0.50, 21),
    ("NIFTY",     0.30, 150.0, 0.50, 21),
    # BANKNIFTY — 100-pt strike step → 10 strikes cover ±1000 pts
    ("BANKNIFTY", 0.30, 300.0, 1.00, None),
    ("BANKNIFTY", 0.30, 500.0, 1.00, None),
    ("BANKNIFTY", 0.30, 300.0, 0.50, 21),
    ("BANKNIFTY", 0.30, 500.0, 0.50, 21),
]


def _save(summaries: list[dict], trades_list: list[pd.DataFrame]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summaries).to_csv(RESULTS_DIR / "spread_summary.csv", index=False)
    if trades_list:
        pd.concat(trades_list, ignore_index=True).to_csv(
            RESULTS_DIR / "spread_trades.csv", index=False
        )


def main() -> None:
    summaries: list[dict] = []
    trades_list: list[pd.DataFrame] = []
    t0 = time.time()
    with DhanClient() as client:
        for und, delta, width, pt, manage in GRID:
            cfg = SpreadConfig(
                underlying=und,
                target_delta=delta,
                target_dte=35,
                profit_take=pt,
                manage_at_dte=manage,
                spread_width=width,
            )
            t_cfg = time.time()
            trades = run_spread_backtest(client, cfg, START, END)
            s = summarise_spread(trades)
            s.update(
                underlying=und,
                target_delta=delta,
                spread_width=width,
                profit_take=pt,
                manage_at_dte=manage,
            )
            summaries.append(s)
            if not trades.empty:
                trades_list.append(trades.assign(
                    param_delta=delta,
                    param_width=width,
                    param_pt=pt,
                    param_manage=manage,
                ))
            print(
                f"  {und} Δ={delta} w={width:.0f} pt={pt:.2f} mg@{manage}  "
                f"n={s.get('n', 0)} "
                f"win={s.get('win_rate', 0):.0%} "
                f"avg=₹{s.get('avg_pnl_contract', 0):,.0f} "
                f"ROI/cyc={s.get('avg_return_on_bp_per_cycle', 0):+.2%} "
                f"maxL={s.get('max_loss_rate', 0):.0%} "
                f"({time.time() - t_cfg:.0f}s)",
                flush=True,
            )
            _save(summaries, trades_list)
    print(f"\nDone in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
