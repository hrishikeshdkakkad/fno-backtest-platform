"""6-month IWM CSP backtest to validate the engine end-to-end on real data."""
from __future__ import annotations

from datetime import date

import pandas as pd
from rich.console import Console

from csp.backtest import run_csp_backtest, summarise
from csp.client import MassiveClient
from csp.strategy import StrategyConfig

console = Console()


def main() -> None:
    cfg = StrategyConfig(
        underlying="IWM",
        target_delta=0.20,
        target_dte=35,
        profit_take=0.5,
        manage_at_dte=21,
        div_yield=0.013,
        strike_increment=1.0,
    )
    with MassiveClient() as c:
        # 6-month run: should emit ~5 cycles
        trades = run_csp_backtest(c, cfg, date(2024, 4, 17), date(2024, 10, 31))
    if trades.empty:
        console.print("[red]no trades[/]")
        return
    console.print(trades[[
        "cycle", "entry_date", "expiry", "strike", "spot_entry",
        "entry_premium", "entry_delta", "outcome", "pnl_dollars", "return_pct",
    ]].to_string(index=False))
    console.print("")
    console.print(summarise(trades))


if __name__ == "__main__":
    main()
