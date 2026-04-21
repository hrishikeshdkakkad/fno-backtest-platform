"""Retrofit existing `spread_trades*.csv` files with the post-cost schema.

Context
-------
`src/nfo/backtest.py` was extended to emit three new per-trade fields
(`gross_pnl_contract`, `txn_cost_contract`, `size_mult`) and to report
`pnl_contract` *net* of transaction costs. CSV artifacts produced before
that change still carry the old 25-column schema with gross-only PnL. Any
downstream analytics (redesign_variants, time_split_validate,
v3_capital_analysis) that read those CSVs therefore show pre-cost numbers.

Regenerating the CSVs from scratch requires Dhan access; this script gives
the same end state offline by recomputing the cost components from fields
already on each row (short_strike, long_strike, net_credit, outcome,
net_close_at_exit, width, expiry spot if available via spot_exit).

Usage
-----
    .venv/bin/python scripts/nfo/recost_trades.py

    # Target specific files:
    .venv/bin/python scripts/nfo/recost_trades.py results/nfo/spread_trades.csv

    # Dry-run (print diff only, don't write):
    .venv/bin/python scripts/nfo/recost_trades.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from nfo import signals as _sig
from nfo.config import RESULTS_DIR
from nfo.costs import spread_roundtrip_cost
from nfo.universe import get as get_under

log = logging.getLogger("recost_trades")

NEW_FIELDS = ("gross_pnl_contract", "txn_cost_contract", "size_mult")


def _looks_like_spread_trades(df: pd.DataFrame) -> bool:
    required = {
        "short_strike", "long_strike", "width", "net_credit",
        "net_close_at_exit", "pnl_per_share", "pnl_contract",
        "outcome", "underlying",
    }
    return required.issubset(df.columns)


def _recost_row(row: pd.Series, lot: int) -> tuple[float, float, float]:
    """Return (gross_pnl_contract, txn_cost_contract, size_mult).

    Cost computation
    ----------------
    We don't have the per-leg close prices on historical rows, so we make
    two conservative approximations:

    * Entry premiums: reconstruct as long_prem = net_close_at_expected_long
      and short_prem = net_credit + long_prem. Without the original chain we
      approximate long_prem as ``width * 0.35`` — roughly a 65/35 split for
      a 100-pt-wide 30Δ structure, matching the historical backtest mix.
      (The cost model's fee calculations are only weakly sensitive to this
      split; Dhan's flat ₹20 brokerage + GST dominate.)

    * Exit path: inferred from the ``outcome`` label. ``expired_worthless``
      / ``max_loss`` / ``partial_loss`` imply settlement (no explicit exit
      orders); anything else (profit_take / managed) implies the spread was
      closed before expiry.
    """
    width = float(row.get("width", 0.0))
    net_credit = float(row.get("net_credit", 0.0))
    long_prem_est = max(0.0, width * 0.35)
    short_prem_est = long_prem_est + net_credit

    outcome = str(row.get("outcome", ""))
    settled_outcomes = {"expired_worthless", "max_loss", "partial_loss"}
    closed_before_expiry = outcome not in settled_outcomes

    if closed_before_expiry:
        net_close = float(row.get("net_close_at_exit", 0.0))
        # Best-effort split: assume long leg halves at close; adjust if needed
        # to preserve the observed net_close.
        long_exit_est = max(0.0, long_prem_est * 0.5)
        short_exit_est = max(0.0, long_exit_est + net_close)
        settle_long = 0.0
    else:
        long_exit_est = 0.0
        short_exit_est = 0.0
        # Settlement STT on ITM long leg — spot_exit < long_strike → intrinsic.
        spot_exit = float(row.get("spot_exit", float("nan")))
        long_strike = float(row.get("long_strike", 0.0))
        if pd.notna(spot_exit) and spot_exit < long_strike:
            settle_long = float(long_strike - spot_exit)
        else:
            settle_long = 0.0

    txn_cost = spread_roundtrip_cost(
        short_entry_premium=short_prem_est,
        short_exit_premium=short_exit_est,
        long_entry_premium=long_prem_est,
        long_exit_premium=long_exit_est,
        lot=lot,
        closed_before_expiry=closed_before_expiry,
        settle_intrinsic_long=settle_long,
    )
    # Gross PnL = the pre-cost pnl_contract that the old backtest wrote.
    gross = float(row.get("pnl_contract", float(row.get("pnl_per_share", 0.0)) * lot))

    entry_date = pd.to_datetime(row["entry_date"]).date()
    size_mult = _sig.month_of_year_size_mult(entry_date)
    return gross, txn_cost, size_mult


def recost_csv(path: Path, *, dry_run: bool = False, backup: bool = True) -> dict:
    df = pd.read_csv(path)
    if df.empty:
        log.info("  %s is empty; skipping", path.name)
        return {"path": str(path), "rows": 0, "rewritten": False}
    if not _looks_like_spread_trades(df):
        log.warning("  %s does not look like a spread_trades CSV; skipping", path.name)
        return {"path": str(path), "rows": len(df), "rewritten": False}
    # If already migrated, no-op unless user forces.
    if all(col in df.columns for col in NEW_FIELDS):
        log.info("  %s already has the new columns; skipping", path.name)
        return {"path": str(path), "rows": len(df), "rewritten": False}

    # Per-underlying lot size; fall back to NIFTY's 65 if unknown.
    def _lot_for(u: str) -> int:
        try:
            return int(get_under(u).lot_size)
        except KeyError:
            return 65

    gross_col, cost_col, mult_col = [], [], []
    for _, row in df.iterrows():
        lot = _lot_for(str(row.get("underlying", "NIFTY")))
        gross, cost, mult = _recost_row(row, lot)
        gross_col.append(gross)
        cost_col.append(cost)
        mult_col.append(mult)

    out = df.copy()
    out["gross_pnl_contract"] = gross_col
    out["txn_cost_contract"] = cost_col
    out["size_mult"] = mult_col
    # Make pnl_contract the net figure, matching the backtest's new semantics.
    out["pnl_contract"] = out["gross_pnl_contract"] - out["txn_cost_contract"]
    # Scale buying_power by size_mult for consistency with current code path.
    if "buying_power" in out.columns:
        out["buying_power"] = out["buying_power"].astype(float) * out["size_mult"]

    summary = {
        "path": str(path),
        "rows": int(len(df)),
        "gross_sum": float(df["pnl_contract"].sum()),
        "cost_sum": float(sum(cost_col)),
        "net_sum": float(out["pnl_contract"].sum()),
        "rewritten": not dry_run,
    }
    if dry_run:
        log.info("  DRY-RUN: would rewrite %s (%d rows, cost drag ₹%.0f, "
                 "gross %s → net %s)",
                 path.name, len(df), summary["cost_sum"],
                 _fmt_inr(summary["gross_sum"]), _fmt_inr(summary["net_sum"]))
        return summary

    if backup:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup_path = path.with_suffix(f".pre-recost-{stamp}.csv")
        shutil.copy2(path, backup_path)
        log.info("  backup → %s", backup_path.name)
    out.to_csv(path, index=False)
    log.info("  rewrote %s (%d rows, cost drag ₹%.0f)",
             path.name, len(df), summary["cost_sum"])
    return summary


def _fmt_inr(v: float) -> str:
    sign = "-" if v < 0 else ""
    return f"{sign}₹{abs(v):,.0f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=Path,
                    help="CSV files to migrate. Default: spread_trades*.csv under results/nfo/.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change without writing.")
    ap.add_argument("--no-backup", action="store_true",
                    help="Skip timestamped .pre-recost backup copy before overwriting.")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    paths: list[Path] = list(args.paths) or sorted(RESULTS_DIR.glob("spread_trades*.csv"))
    if not paths:
        log.warning("No matching CSVs found under %s", RESULTS_DIR)
        return 1

    log.info("Re-costing %d CSV(s)%s", len(paths), " (dry-run)" if args.dry_run else "")
    summaries = []
    for p in paths:
        if not p.exists():
            log.warning("  missing: %s", p)
            continue
        summaries.append(recost_csv(p, dry_run=args.dry_run, backup=not args.no_backup))

    any_rewritten = any(s.get("rewritten") for s in summaries)
    if not args.dry_run and not any_rewritten:
        log.info("Nothing to do — all target CSVs are already cost-inclusive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
