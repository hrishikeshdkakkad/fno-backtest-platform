"""Fill the 2 missing V3 cycles via custom-entry Dhan backtest.

The 2-year spread_trades.csv only has entries at 35-DTE-before-expiry. V3
fires on 2024-05-03 (expiry 2024-05-30) and 2025-01-06 (expiry 2025-01-30),
which are not 35-DTE entries — so those trades weren't in the CSV.

This script runs the same spread-pick + daily-walk + manage/exit logic as
`nfo.backtest._run_cycle`, but with the V3 first-fire date as entry_date.
Outputs to `results/nfo/spread_trades_v3_gaps.csv`.

~140 Dhan calls total. Cached on second run.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date

import pandas as pd

from nfo import signals as _sig
from nfo.backtest import SpreadTrade, _manage_exit, _merge_series
from nfo.client import DhanClient
from nfo.config import RESULTS_DIR
from nfo.costs import spread_roundtrip_cost
from nfo.data import load_fixed_strike_daily, load_underlying_daily
from nfo.spread import SpreadConfig, pick_put_spread, spread_payoff_per_share
from nfo.universe import get as get_under

log = logging.getLogger("v3_fill_gaps")

# V3 first-fire → (entry_date, expiry_date) for the 2 cycles missing from
# spread_trades.csv at 0.30Δ × 100-wide.
MISSING_CYCLES: list[tuple[date, date]] = [
    (date(2024, 5, 3), date(2024, 5, 30)),
    (date(2025, 1, 6), date(2025, 1, 30)),
]


def run_custom_cycle(
    client: DhanClient,
    cfg: SpreadConfig,
    under,
    entry_date: date,
    expiry_date: date,
    spot_daily: pd.DataFrame,
) -> SpreadTrade | None:
    """Mirror of nfo.backtest._run_cycle but with arbitrary entry_date."""
    spread = pick_put_spread(
        client, cfg, under,
        expiry_code=1, expiry_flag="MONTH",
        expiry_date=expiry_date, entry_date=entry_date,
    )
    if spread is None:
        log.warning("pick_put_spread returned None for entry %s → expiry %s",
                    entry_date, expiry_date)
        return None

    lot = under.lot_size
    short_series = load_fixed_strike_daily(
        client, under,
        expiry_code=1, expiry_flag="MONTH",
        option_type="PUT", strike=spread.short_strike,
        from_date=entry_date.isoformat(), to_date=expiry_date.isoformat(),
        offset_range=(-12, 10),
    )
    long_series = load_fixed_strike_daily(
        client, under,
        expiry_code=1, expiry_flag="MONTH",
        option_type="PUT", strike=spread.long_strike,
        from_date=entry_date.isoformat(), to_date=expiry_date.isoformat(),
        offset_range=(-15, 8),
    )
    merged = _merge_series(short_series, long_series, expiry_date)

    spot_row = spot_daily[spot_daily["date"] == pd.Timestamp(expiry_date)]
    if spot_row.empty:
        spot_row = spot_daily[spot_daily["date"] <= pd.Timestamp(expiry_date)].tail(1)
    spot_exit = float(spot_row["close"].iloc[0]) if not spot_row.empty else float("nan")

    exit_row, outcome = _manage_exit(merged, cfg, spread.net_credit) if not merged.empty else (None, "")
    if exit_row is not None:
        net_close_at_exit = float(exit_row["net_close"])
        exit_dt = exit_row["date"].date()
        dte_exit = int(exit_row["dte"])
        pnl_per_share = spread.net_credit - net_close_at_exit
        closed_before_expiry = True
        short_exit_prem = float(exit_row.get("short_close", net_close_at_exit))
        long_exit_prem = float(exit_row.get("long_close", 0.0))
    else:
        pnl_per_share, outcome = spread_payoff_per_share(
            spread.short_strike, spread.long_strike, spread.net_credit, spot_exit,
        )
        net_close_at_exit = spread.net_credit - pnl_per_share
        exit_dt = expiry_date
        dte_exit = 0
        closed_before_expiry = False
        short_exit_prem = 0.0
        long_exit_prem = 0.0

    long_intrinsic = max(0.0, spread.long_strike - spot_exit) if not closed_before_expiry else 0.0
    txn_cost_contract = spread_roundtrip_cost(
        short_entry_premium=spread.short_premium,
        short_exit_premium=short_exit_prem,
        long_entry_premium=spread.long_premium,
        long_exit_premium=long_exit_prem,
        lot=int(lot),
        closed_before_expiry=closed_before_expiry,
        settle_intrinsic_long=long_intrinsic,
    )
    gross_pnl_contract = pnl_per_share * lot
    pnl_contract_net = gross_pnl_contract - txn_cost_contract
    size_mult = _sig.month_of_year_size_mult(entry_date)

    return SpreadTrade(
        underlying=under.name,
        cycle_year=expiry_date.year, cycle_month=expiry_date.month,
        entry_date=entry_date, expiry_date=expiry_date, exit_date=exit_dt,
        dte_entry=(expiry_date - entry_date).days, dte_exit=dte_exit,
        spot_entry=spread.spot_at_entry, spot_exit=spot_exit,
        short_strike=spread.short_strike, long_strike=spread.long_strike,
        width=cfg.spread_width,
        net_credit=spread.net_credit, net_close_at_exit=net_close_at_exit,
        pnl_per_share=pnl_per_share,
        pnl_contract=pnl_contract_net,
        gross_pnl_contract=gross_pnl_contract,
        txn_cost_contract=txn_cost_contract,
        buying_power=spread.max_loss * lot * cfg.margin_multiplier * size_mult,
        outcome=outcome,
        entry_delta=spread.short_delta, entry_iv=spread.short_iv,
        size_mult=size_mult,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    under = get_under("NIFTY")
    cfg = SpreadConfig(
        underlying="NIFTY", target_delta=0.30, target_dte=35,
        profit_take=0.50, manage_at_dte=21, margin_multiplier=1.5,
        spread_width=100,
    )

    rows = []
    # Two exit strategies: 50% profit-take + 21-DTE manage, AND hold-to-expiry.
    variants = [
        ("pt50", SpreadConfig(
            underlying="NIFTY", target_delta=0.30, target_dte=35,
            profit_take=0.50, manage_at_dte=21, margin_multiplier=1.5,
            spread_width=100,
        )),
        ("hte", SpreadConfig(
            underlying="NIFTY", target_delta=0.30, target_dte=35,
            profit_take=1.00, manage_at_dte=None, margin_multiplier=1.5,
            spread_width=100,
        )),
    ]
    with DhanClient() as client:
        spot_daily = load_underlying_daily(
            client, under,
            from_date="2024-04-01", to_date="2025-02-15",
        )
        for vname, vcfg in variants:
            for entry, expiry in MISSING_CYCLES:
                log.info("Running %s cycle entry=%s → expiry=%s", vname, entry, expiry)
                trade = run_custom_cycle(client, vcfg, under, entry, expiry, spot_daily)
                if trade is None:
                    log.warning("  trade None — skipping")
                    continue
                d = asdict(trade)
                d["param_delta"] = 0.30
                d["param_width"] = 100.0
                d["param_pt"] = vcfg.profit_take
                d["param_manage"] = vcfg.manage_at_dte if vcfg.manage_at_dte else 0
                rows.append(d)
                log.info("  %s %s: pnl/sh=%.2f pnl/lot=%.2f outcome=%s",
                         vname, d["entry_date"], d["pnl_per_share"],
                         d["pnl_contract"], d["outcome"])

    out = RESULTS_DIR / "spread_trades_v3_gaps.csv"
    if rows:
        pd.DataFrame(rows).to_csv(out, index=False)
        log.info("Wrote %d rows to %s", len(rows), out)
    else:
        log.warning("No trades produced — output not written.")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
