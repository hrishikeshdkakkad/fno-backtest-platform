"""Entry-timing perturbation for V3 — first fire vs +1 day vs worst-of-next-two.

For every V3-firing cycle, re-pick the spread and run `_run_cycle`-style
backtest at three entry timings *for each exit variant*:

* `first_fire`    — V3's first firing date for that cycle (current behaviour).
* `plus_one_day`  — entry one trading day later.
* `worst_of_two`  — the worse PnL of {first_fire, plus_one_day}.

Runs separately for PT50 (50 % profit-take + manage@21) and HTE
(hold-to-expiry). Both exits live in the frozen spec, so entry fragility
may differ between them — HTE takes the full PnL path, PT50 bails at 50 %
credit and can duck a mid-cycle reversal the extra day exposes us to.

This script reuses `scripts/nfo/v3_fill_gaps.run_custom_cycle` — the same
`_run_cycle` logic with an explicit entry date — once per cycle per
timing per variant.

Usage:
    .venv/bin/python scripts/nfo/entry_perturbation_backtest.py

Output:
    results/nfo/entry_perturbation_per_trade.csv  — one row per (cycle, timing, variant)
    results/nfo/entry_perturbation_trades.csv     — summary stats per (timing, variant)
"""
from __future__ import annotations

import argparse
import logging
import time
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from nfo import calibrate
from nfo.client import DhanClient
from nfo.config import RESULTS_DIR
from nfo.data import load_underlying_daily
from nfo.spread import SpreadConfig
from nfo.universe import get as get_under

# Reuse the cycle runner from v3_fill_gaps (already handles arbitrary entries).
import importlib.util
import sys
_here = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("_v3fg", _here / "v3_fill_gaps.py")
_v3fg = importlib.util.module_from_spec(_spec)
sys.modules["_v3fg"] = _v3fg
_spec.loader.exec_module(_v3fg)

log = logging.getLogger("entry_perturbation")


def _v3_cycles(signals_df: pd.DataFrame) -> list[tuple[date, date]]:
    """Return [(first_fire, expiry), ...] for each V3-fired monthly cycle."""
    import redesign_variants as rv
    sys.path.insert(0, str(_here))
    v3 = next(v for v in rv.make_variants() if v.name == "V3")
    atr_series = rv.load_nifty_atr(signals_df["date"])
    fires = rv.get_firing_dates(v3, signals_df, atr_series)

    by_expiry: dict[str, list[pd.Timestamp]] = {}
    for fire_date, _ in fires:
        row = signals_df[signals_df["date"].dt.date == fire_date]
        if row.empty:
            continue
        exp = str(row["target_expiry"].iloc[0])
        if not exp:
            continue
        ts = pd.Timestamp(fire_date)
        by_expiry.setdefault(exp, []).append(ts)

    cycles: list[tuple[date, date]] = []
    for exp_str in sorted(by_expiry):
        first_fire = min(by_expiry[exp_str]).date()
        cycles.append((first_fire, date.fromisoformat(exp_str)))
    return cycles


def _next_trading_day(spot_daily: pd.DataFrame, after: date) -> date | None:
    later = spot_daily.loc[spot_daily["date"] > pd.Timestamp(after), "date"]
    return None if later.empty else later.iloc[0].date()


# (label, profit_take, manage_at_dte) — matches frozen-spec variants.
VARIANT_CONFIGS = (
    ("pt50", 0.50, 21),
    ("hte", 1.00, None),
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variants", default="pt50,hte",
                    help="Comma-separated subset of {pt50, hte} to evaluate.")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    selected = {v.strip() for v in args.variants.split(",") if v.strip()}
    configs = [c for c in VARIANT_CONFIGS if c[0] in selected]
    if not configs:
        raise SystemExit(f"No recognised variants in {args.variants}")

    signals_df = pd.read_parquet(RESULTS_DIR / "historical_signals.parquet")
    signals_df["date"] = pd.to_datetime(signals_df["date"])
    cycles = _v3_cycles(signals_df)
    log.info("V3 fired on %d distinct cycles.", len(cycles))

    under = get_under("NIFTY")

    per_rows: list[dict] = []
    t0 = time.time()
    with DhanClient() as client:
        spot_daily = load_underlying_daily(
            client, under,
            from_date="2023-12-15", to_date="2026-04-18",
        )
        for variant_name, pt, manage in configs:
            cfg = SpreadConfig(
                underlying="NIFTY", target_delta=0.30, target_dte=35,
                profit_take=pt, manage_at_dte=manage, margin_multiplier=1.5,
                spread_width=100.0,
            )
            log.info("=== variant %s (pt=%.2f, manage=%s) ===", variant_name, pt, manage)
            for first_fire, expiry in cycles:
                plus_one = _next_trading_day(spot_daily, first_fire)
                if plus_one is None or plus_one >= expiry:
                    log.warning("Skipping %s: no next trading day before expiry.", first_fire)
                    continue
                t1 = _v3fg.run_custom_cycle(client, cfg, under, first_fire, expiry, spot_daily)
                t2 = _v3fg.run_custom_cycle(client, cfg, under, plus_one, expiry, spot_daily)
                if t1 is None or t2 is None:
                    log.warning("Skipping %s: one of the entries returned None.", first_fire)
                    continue
                d1 = asdict(t1)
                d1.update(variant=variant_name, timing="first_fire",
                          v3_first_fire=first_fire.isoformat())
                d2 = asdict(t2)
                d2.update(variant=variant_name, timing="plus_one_day",
                          v3_first_fire=first_fire.isoformat())
                per_rows.extend([d1, d2])
                worst = t1 if t1.pnl_contract <= t2.pnl_contract else t2
                dw = asdict(worst)
                dw.update(variant=variant_name, timing="worst_of_two",
                          v3_first_fire=first_fire.isoformat())
                if hasattr(dw.get("entry_date"), "isoformat"):
                    dw["entry_date"] = dw["entry_date"].isoformat()
                per_rows.append(dw)
                log.info(
                    "%s → exp %s: first=₹%.0f   +1d=₹%.0f",
                    first_fire, expiry, t1.pnl_contract, t2.pnl_contract,
                )

    df = pd.DataFrame(per_rows)
    df.to_csv(RESULTS_DIR / "entry_perturbation_per_trade.csv", index=False)

    summary_rows = []
    for variant_name, _, _ in configs:
        for timing in ("first_fire", "plus_one_day", "worst_of_two"):
            sub = df[(df["timing"] == timing) & (df["variant"] == variant_name)]
            if sub.empty:
                continue
            stats = calibrate.summary_stats(sub)
            summary_rows.append({
                "variant": variant_name,
                "timing": timing,
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
    pd.DataFrame(summary_rows).to_csv(RESULTS_DIR / "entry_perturbation_trades.csv", index=False)
    log.info("Done in %.1fs. %d per-trade rows, %d summary rows.",
             time.time() - t0, len(per_rows), len(summary_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
