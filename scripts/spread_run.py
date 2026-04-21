"""Put credit spread grid backtest.

Underlyings x delta x width x (profit_take, manage_at_dte).

Writes results/spread_summary.csv and results/spread_trades.csv. Incremental
checkpoints after each config so the run is resumable and partial results
are always queryable.
"""
from __future__ import annotations

import sys
import time
from datetime import date

import pandas as pd

from csp.client import MassiveClient
from csp.config import RESULTS_DIR
from csp.spread import SpreadConfig
from csp.spread_backtest import run_spread_backtest, summarise_spread

START = date(2024, 4, 17)
END = date(2026, 4, 17)

# Each row is (ticker, div_yield, strike_increment, note)
UNDERLYINGS = [
    ("IWM", 0.013, 1.0, "Russell 2000 ETF"),
    ("QQQ", 0.006, 1.0, "Nasdaq 100 ETF"),
]

# (target_delta, target_dte, spread_width, profit_take, manage_at_dte)
# Hold-to-expiry configs first — the CSP backtest showed they dominate on
# yield. We want those numbers landing before we burn API budget on the
# manage@21 variants that we can compare later if time permits.
GRID = [
    # high-yield hold-to-expiry first
    (0.30, 35, 10.0, 1.00, None),
    (0.25, 35, 10.0, 1.00, None),
    (0.20, 35, 10.0, 1.00, None),
    (0.30, 35, 5.0,  1.00, None),
    (0.25, 35, 5.0,  1.00, None),
    (0.20, 35, 5.0,  1.00, None),
    # manage@21 variants for comparison
    (0.30, 35, 10.0, 0.50, 21),
    (0.25, 35, 10.0, 0.50, 21),
    (0.20, 35, 10.0, 0.50, 21),
    (0.30, 35, 5.0,  0.50, 21),
    (0.25, 35, 5.0,  0.50, 21),
    (0.20, 35, 5.0,  0.50, 21),
]


def main() -> None:
    all_summaries: list[dict] = []
    all_trades: list[pd.DataFrame] = []
    t_start = time.time()

    with MassiveClient() as client:
        for ticker, div, inc, note in UNDERLYINGS:
            print(f"\n== {ticker} == ({note})", flush=True)
            for delta, dte, width, pt, mg in GRID:
                cfg = SpreadConfig(
                    underlying=ticker,
                    target_delta=delta,
                    target_dte=dte,
                    profit_take=pt,
                    manage_at_dte=mg,
                    div_yield=div,
                    strike_increment=inc,
                    spread_width=width,
                )
                t0 = time.time()
                try:
                    trades = run_spread_backtest(client, cfg, START, END)
                except Exception as exc:
                    print(f"  ERROR Δ={delta} w={width} pt={pt} mg@{mg}: {exc}", flush=True)
                    continue
                elapsed = time.time() - t0
                s = summarise_spread(trades)
                s.update(
                    underlying=ticker,
                    target_delta=delta,
                    target_dte=dte,
                    profit_take=pt,
                    manage_at_dte=mg,
                    spread_width=width,
                )
                all_summaries.append(s)
                if not trades.empty:
                    trades = trades.assign(
                        param_delta=delta,
                        param_dte=dte,
                        param_width=width,
                        param_pt=pt,
                        param_manage=mg,
                    )
                    all_trades.append(trades)
                print(
                    f"  {ticker} Δ={delta:.2f} w=${width:.0f} pt={pt:.2f} mg@{mg}  "
                    f"n={s.get('n',0)} "
                    f"avg/mo=${s.get('avg_monthly_pnl',0):,.0f} "
                    f"worst=${s.get('worst_month_pnl',0):,.0f} "
                    f"maxloss%={s.get('max_loss_rate',0):.0%} "
                    f"wins={s.get('win_rate',0):.0%} "
                    f"BP=${s.get('avg_buying_power',0):,.0f} "
                    f"ROI/cyc={s.get('avg_return_on_bp_per_cycle',0):.1%} "
                    f"({elapsed:.0f}s)",
                    flush=True,
                )
                _save(all_summaries, all_trades)

    elapsed = time.time() - t_start
    print(f"\nDone in {elapsed/60:.1f} min", flush=True)


def _save(summaries, trades_list) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summaries).to_csv(RESULTS_DIR / "spread_summary.csv", index=False)
    if trades_list:
        pd.concat(trades_list, ignore_index=True).to_csv(
            RESULTS_DIR / "spread_trades.csv", index=False
        )


if __name__ == "__main__":
    main()
