"""Plot real payoff diagrams for put credit spreads on SPY, GOOG, TSLA.

For each underlying we:
  1. Pick the most recent monthly expiry within the data window
  2. Find the entry date (~35 calendar days earlier)
  3. Use the existing picker to select a 0.30-delta short put
  4. Construct a put credit spread by buying a put `width` below the short strike
  5. Fetch the actual entry-day closes for both legs → net credit
  6. Plot the payoff at expiry, annotate with realized outcome
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from csp.client import MassiveClient
from csp.config import RESULTS_DIR
from csp.data import load_option_bars, load_stock_bars
from csp.spread import SpreadConfig, pick_put_spread_for_cycle
from csp.universe import latest_trading_day_on_or_before, monthly_expirations


# (ticker, div_yield, strike_increment, spread_width)
# TSLA monthlies list strikes irregularly around 2026-04 ($350 missing,
# $355/$360/$340 present); use $5 increments and a $20 width to land on
# listed strikes on both legs.
TICKERS = [
    ("SPY",  0.013, 1.0, 5.0),
    ("GOOG", 0.005, 1.0, 5.0),
    ("TSLA", 0.000, 5.0, 20.0),
]

# Most recent monthly expiry in the 2y window (relative to today 2026-04-18)
EXPIRY = date(2026, 4, 17)
DATA_START = date(2024, 4, 17)
DATA_END   = date(2026, 4, 18)

PLOT_DIR = RESULTS_DIR / "plots"
PLOT_DIR.mkdir(parents=True, exist_ok=True)


def _spot_on(stock, ts) -> float:
    r = stock.loc[stock["date"] <= ts].tail(1)
    if r.empty:
        raise RuntimeError("no stock bar on or before " + str(ts))
    return float(r["c"].iloc[0])


def plot_one(client: MassiveClient, ticker: str, div: float, inc: float, width: float):
    cfg = SpreadConfig(
        underlying=ticker,
        target_delta=0.30,
        target_dte=35,
        profit_take=1.0,         # hold-to-expiry payoff is what we're visualizing
        manage_at_dte=None,
        div_yield=div,
        strike_increment=inc,
        spread_width=width,
    )

    stock = load_stock_bars(client, ticker, DATA_START, DATA_END)
    import pandas as pd
    entry_ts = latest_trading_day_on_or_before(
        stock, EXPIRY - timedelta(days=cfg.target_dte)
    )
    if entry_ts is None:
        print(f"{ticker}: no entry bar found")
        return

    pick = pick_put_spread_for_cycle(client, cfg, stock, entry_ts, EXPIRY)
    if pick is None:
        print(f"{ticker}: no spread found")
        return

    spot_entry = pick.spot_at_entry
    spot_exit = _spot_on(stock, pd.Timestamp(EXPIRY))

    short_k = pick.short_strike
    long_k  = pick.long_strike
    credit  = pick.net_credit
    max_loss_per_sh = (short_k - long_k) - credit
    breakeven = short_k - credit

    # ── payoff curve ─────────────────────────────────────────────
    # x range: 15% either side of short strike
    x = np.linspace(long_k - width, short_k + width * 3, 400)
    # Per-share payoff at expiry
    intrinsic = np.maximum(short_k - x, 0) - np.maximum(long_k - x, 0)
    pnl_per_share = credit - intrinsic
    # Scale to per-contract dollars
    pnl_dollars = pnl_per_share * 100.0

    # Realized outcome at actual expiry spot
    realized_intrinsic = max(short_k - spot_exit, 0) - max(long_k - spot_exit, 0)
    realized_pnl = (credit - realized_intrinsic) * 100.0

    # ── plot ─────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(x, pnl_dollars, linewidth=2, color="#1f77b4", label="Payoff at expiry")
    ax.axhline(0, color="gray", linewidth=0.5)
    ax.fill_between(x, pnl_dollars, 0, where=pnl_dollars >= 0, color="#2ecc71", alpha=0.2)
    ax.fill_between(x, pnl_dollars, 0, where=pnl_dollars <  0, color="#e74c3c", alpha=0.2)

    # Vertical markers
    ax.axvline(long_k,   color="#c0392b", linestyle=":", linewidth=1, label=f"long strike ${long_k:.2f}")
    ax.axvline(short_k,  color="#27ae60", linestyle=":", linewidth=1, label=f"short strike ${short_k:.2f}")
    ax.axvline(breakeven, color="black",   linestyle="--", linewidth=1, label=f"breakeven ${breakeven:.2f}")
    ax.axvline(spot_entry, color="#3498db", linestyle="-", linewidth=1.5, alpha=0.8,
               label=f"entry spot ${spot_entry:.2f}")
    ax.axvline(spot_exit,  color="#8e44ad", linestyle="-", linewidth=1.5, alpha=0.8,
               label=f"expiry spot ${spot_exit:.2f}")

    # Horizontal markers
    ax.axhline(credit * 100,           color="#27ae60", linestyle=":", linewidth=0.8, alpha=0.6)
    ax.axhline(-max_loss_per_sh * 100, color="#c0392b", linestyle=":", linewidth=0.8, alpha=0.6)

    # Realized point
    ax.plot([spot_exit], [realized_pnl], "o", markersize=10,
            color="#8e44ad", label=f"actual outcome: ${realized_pnl:+.0f}")

    ax.set_title(
        f"{ticker} Put Credit Spread Payoff\n"
        f"Short ${short_k:.2f} / Long ${long_k:.2f} • "
        f"Entry {entry_ts.date()} → Expiry {EXPIRY}\n"
        f"Net credit ${credit:.2f}/sh (${credit*100:.0f}/contract)  •  "
        f"Max profit ${credit*100:.0f}  Max loss ${max_loss_per_sh*100:.0f}  "
        f"BP ${max_loss_per_sh*100:.0f}"
    )
    ax.set_xlabel(f"{ticker} price at expiry ($)")
    ax.set_ylabel("P/L per contract ($)")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    outfile = PLOT_DIR / f"payoff_{ticker}.png"
    fig.savefig(outfile, dpi=120)
    plt.close(fig)
    print(
        f"{ticker}: {outfile.name}  entry ${spot_entry:.2f} → expiry ${spot_exit:.2f}  "
        f"short ${short_k:.2f} / long ${long_k:.2f}  credit ${credit:.2f}  "
        f"realized ${realized_pnl:+.0f}"
    )


def main():
    with MassiveClient() as c:
        for ticker, div, inc, width in TICKERS:
            try:
                plot_one(c, ticker, div, inc, width)
            except Exception as exc:
                print(f"{ticker}: ERROR {exc}")


if __name__ == "__main__":
    main()
