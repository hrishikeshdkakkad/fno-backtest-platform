"""Exit-rule sweep for V3 — PT25 / PT50 / PT75 / HTE / DTE=2.

Runs the same backtest engine (`nfo.backtest.run_spread_backtest`) across
five exit rules against NIFTY 0.30Δ × 100-wide spreads, then summarises
per-rule win rate / Sharpe / max-loss / Sharpe against the full 82-trade
baseline.

Rolling option parquets under `data/nfo/rolling/` are cached — only the
exit logic changes per config, so the run is fast once data is resident.

Usage:
    .venv/bin/python scripts/nfo/exit_sweep_backtest.py

Output:
    results/nfo/exit_sweep_trades.csv — one row per (exit_rule, summary).
"""
from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd

from nfo import calibrate
from nfo.backtest import run_spread_backtest
from nfo.client import DhanClient
from nfo.config import RESULTS_DIR
from nfo.spread import SpreadConfig

log = logging.getLogger("exit_sweep")

START = date(2024, 1, 1)
END = date(2026, 4, 30)

# (label, profit_take, manage_at_dte)
EXIT_RULES: list[tuple[str, float, int | None]] = [
    ("PT25", 0.25, 21),
    ("PT50", 0.50, 21),
    ("PT75", 0.75, 21),
    ("HTE", 1.00, None),
    ("DTE2", 1.00, 2),
]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    rows = []
    per_trade_rows: list[pd.DataFrame] = []
    t0 = time.time()
    with DhanClient() as client:
        for label, pt, manage in EXIT_RULES:
            cfg = SpreadConfig(
                underlying="NIFTY",
                target_delta=0.30,
                target_dte=35,
                profit_take=pt,
                manage_at_dte=manage,
                spread_width=100.0,
            )
            t_cfg = time.time()
            trades = run_spread_backtest(client, cfg, START, END)
            if trades.empty:
                log.warning("%s produced 0 trades", label)
                continue
            # Keep per-trade rows for downstream analysis.
            per_trade_rows.append(trades.assign(exit_rule=label))
            # Summary metrics.
            stats = calibrate.summary_stats(trades)
            rows.append({
                "exit_rule": label,
                "profit_take": pt,
                "manage_at_dte": manage,
                "n": stats.n,
                "win_rate": stats.win_rate,
                "avg_pnl_contract": stats.avg_pnl_contract,
                "total_pnl_contract": stats.total_pnl_contract,
                "worst_cycle_pnl": stats.worst_cycle_pnl,
                "best_cycle_pnl": stats.best_cycle_pnl,
                "std_pnl_contract": stats.std_pnl_contract,
                "sharpe": stats.sharpe,
                "sortino": stats.sortino,
                "max_loss_rate": stats.max_loss_rate,
            })
            log.info(
                "%s  n=%d win=%.0f%% avg=₹%.0f sharpe=%.2f maxL=%.1f%%  (%ds)",
                label, stats.n, stats.win_rate * 100, stats.avg_pnl_contract,
                stats.sharpe, stats.max_loss_rate * 100, time.time() - t_cfg,
            )

    pd.DataFrame(rows).to_csv(RESULTS_DIR / "exit_sweep_trades.csv", index=False)
    if per_trade_rows:
        pd.concat(per_trade_rows, ignore_index=True).to_csv(
            RESULTS_DIR / "exit_sweep_per_trade.csv", index=False,
        )
    log.info("Done in %.1fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
