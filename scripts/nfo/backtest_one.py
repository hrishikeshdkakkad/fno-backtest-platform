"""Phase-2 hand-verify: one NIFTY cycle end-to-end.

Target: NIFTY March-2025 monthly (expiry 2025-03-27, pre-reform Thursday).
Config: Δ = 0.25, width = 500, hold-to-expiry.

Prints:
  1. Entry-day chain snapshot + picked short strike (delta-targeted)
  2. Long leg pick + net credit
  3. Daily close series of both legs through expiry
  4. Exit P&L and outcome label

Hand-verifiable against Sensibull/broker chart screenshots if available.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

import pandas as pd

from nfo import universe
from nfo.client import DhanClient
from nfo.data import load_atm_chain_snapshot, load_fixed_strike_daily
from nfo.spread import SpreadConfig, pick_put_spread, spread_payoff_per_share


def main() -> int:
    n = universe.get("NIFTY")
    # Dhan's expiryCode=1 with from_date inside March 2025 → the March monthly.
    expiry_code = 1
    expiry_flag = "MONTH"
    expiry_date = date(2025, 3, 27)               # pre-reform last Thursday
    entry_date = date(2025, 2, 20)                # 35 DTE target
    cfg = SpreadConfig(
        underlying="NIFTY",
        target_delta=0.30,
        target_dte=35,
        profit_take=1.00,
        manage_at_dte=None,
        spread_width=150.0,     # 3 strikes — must fit within Dhan's ATM±10 cap
    )

    with DhanClient() as client:
        print(f"\n== Entry setup ({entry_date} → {expiry_date}, {(expiry_date-entry_date).days} DTE) ==")
        chain = load_atm_chain_snapshot(
            client, n,
            expiry_code=expiry_code, expiry_flag=expiry_flag,
            option_type="PUT", on_date=entry_date,
            offset_range=(-15, 3),
        )
        if chain.empty:
            print("  ✗ empty chain on entry date"); return 2
        print(f"  chain snapshot rows: {len(chain)}")
        print(chain[["strike", "close", "iv", "spot", "offset"]].to_string(index=False))

        spread = pick_put_spread(
            client, cfg, n,
            expiry_code=expiry_code, expiry_flag=expiry_flag,
            expiry_date=expiry_date, entry_date=entry_date,
        )
        if spread is None:
            print("  ✗ pick_put_spread returned None"); return 3
        print(f"\n== Selected spread ==")
        lot = n.lot_size
        print(f"  entry       : {spread.entry_date.date()}  spot={spread.spot_at_entry:.1f}")
        print(f"  short leg   : {spread.short_strike:.0f} PE  close={spread.short_premium:.2f}  Δ={spread.short_delta:+.3f}  IV={spread.short_iv:.1f}%")
        print(f"  long leg    : {spread.long_strike:.0f} PE  close={spread.long_premium:.2f}")
        print(f"  net credit  : ₹{spread.net_credit:.2f}/sh  (lot {lot}) → ₹{spread.net_credit*lot:,.0f} per contract")
        print(f"  max loss    : ₹{spread.max_loss:.2f}/sh → ₹{spread.max_loss*lot:,.0f} per contract")
        print(f"  BP estimate : ₹{spread.max_loss*lot*cfg.margin_multiplier:,.0f}  (× {cfg.margin_multiplier})")

        print(f"\n== Daily close series of both legs ==")
        short_series = load_fixed_strike_daily(
            client, n,
            expiry_code=expiry_code, expiry_flag=expiry_flag,
            option_type="PUT", strike=spread.short_strike,
            from_date=entry_date.isoformat(),
            to_date=(expiry_date + timedelta(days=1)).isoformat(),
            offset_range=(-15, 5),
        )
        long_series = load_fixed_strike_daily(
            client, n,
            expiry_code=expiry_code, expiry_flag=expiry_flag,
            option_type="PUT", strike=spread.long_strike,
            from_date=entry_date.isoformat(),
            to_date=(expiry_date + timedelta(days=1)).isoformat(),
            offset_range=(-20, 0),
        )
        if short_series.empty or long_series.empty:
            print(f"  ✗ short={len(short_series)} long={len(long_series)} — widen offset range"); return 4
        merged = short_series[["date", "close", "spot"]].rename(
            columns={"close": "short_close", "spot": "spot"}
        ).merge(
            long_series[["date", "close"]].rename(columns={"close": "long_close"}),
            on="date", how="inner",
        )
        merged["net"] = merged["short_close"] - merged["long_close"]
        merged["pnl_per_sh"] = spread.net_credit - merged["net"]
        merged["pnl_contract"] = merged["pnl_per_sh"] * lot
        print(merged.to_string(index=False, formatters={
            "spot": "{:.1f}".format,
            "short_close": "{:.2f}".format,
            "long_close": "{:.2f}".format,
            "net": "{:.2f}".format,
            "pnl_per_sh": "{:+.2f}".format,
            "pnl_contract": "₹{:+,.0f}".format,
        }))

        # Settle at expiry
        spot_at_expiry = float(merged["spot"].iloc[-1])
        pnl_per_sh, outcome = spread_payoff_per_share(
            spread.short_strike, spread.long_strike, spread.net_credit, spot_at_expiry,
        )
        print(f"\n== Settlement ==")
        print(f"  spot at exit : {spot_at_expiry:.1f}")
        print(f"  outcome      : {outcome}")
        print(f"  intrinsic P&L: ₹{pnl_per_sh*lot:+,.0f}/contract  ({pnl_per_sh:+.2f}/sh)")
        # cross-check with market-close pnl
        mkt_pnl = float(merged["pnl_contract"].iloc[-1])
        print(f"  market  P&L : ₹{mkt_pnl:+,.0f}/contract  (close-based, not settlement)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
