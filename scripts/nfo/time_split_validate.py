"""Out-of-sample validation for the redesign variants.

The redesign loop in `redesign_variants.py` found V3 the winner — but V3 was
chosen on the same 70 trades it's measured against. That's in-sample. To
check whether V3 is a real edge or classic overfitting, we split trades
into train (2024) and test (2025+), apply each variant to both subsets,
and report side-by-side metrics.

Decision framework:
  - If V3 metrics are **similar** on train and test → real structural edge,
    safe to promote.
  - If V3 **collapses on test** (win-rate <70%, negative Sharpe, or fires
    zero times) → overfitted, needs loosening or different rule.
  - If V3 **fires too rarely** on test (< 2 trades) → inconclusive, can't
    reject the overfitting hypothesis.

Output: `results/nfo/time_split_report.md`.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from nfo.config import RESULTS_DIR

# Re-use everything from the sibling orchestrator rather than duplicate.
import importlib.util
import sys
_spec = importlib.util.spec_from_file_location(
    "_rv", Path(__file__).parent / "redesign_variants.py"
)
_rv = importlib.util.module_from_spec(_spec)
sys.modules["_rv"] = _rv
_spec.loader.exec_module(_rv)

log = logging.getLogger("time_split")


def _filter_by_date(
    signals_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    lo: date,
    hi: date,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    sig = signals_df[(signals_df["date"] >= pd.Timestamp(lo)) &
                     (signals_df["date"] < pd.Timestamp(hi))].reset_index(drop=True)
    trade_dt = pd.to_datetime(trades_df["entry_date"])
    trd = trades_df[(trade_dt >= pd.Timestamp(lo)) &
                    (trade_dt < pd.Timestamp(hi))].reset_index(drop=True)
    return sig, trd


def _fmt_row(name: str, r: dict) -> str:
    """One row of the train/test/full comparison."""
    n_fires = r.get("firing_days", 0)
    fires_yr = r.get("firing_per_year", 0)
    n_trades = r.get("filtered_trades", 0) or 0
    win = (r.get("win_rate") or 0) * 100
    sharpe = r.get("sharpe") or float("nan")
    max_loss = (r.get("max_loss_rate") or 0) * 100
    return (f"| {name} | {n_fires} | {fires_yr} | {n_trades} | "
            f"{win:.0f}% | {sharpe:+.2f} | {max_loss:.1f}% |")


def _write_report(
    per_variant: dict[str, dict[str, dict]],
    split_date: date,
) -> str:
    lines = [
        "# Time-split validation — V3 and siblings",
        "",
        f"Split date: **{split_date.isoformat()}**.",
        "",
        "- **Train** = trades entered before the split date.",
        "- **Test**  = trades entered on or after.",
        "- **Full**  = both combined (what `redesign_variants.py` reports).",
        "",
        "A variant is **robust** if train and test metrics agree within "
        "reasonable tolerance (win-rate within ±15 pp, Sharpe directionally "
        "consistent, non-zero fire count in both).",
        "",
    ]

    for name, splits in per_variant.items():
        r_full = splits["full"]
        r_train = splits["train"]
        r_test = splits["test"]
        lines += [
            f"## {name} — {r_full['description']}",
            "",
            "| Split | Fires | Fires/yr | Trades | Win% | Sharpe | MaxLoss% |",
            "|---|---:|---:|---:|---:|---:|---:|",
            _fmt_row("Full", r_full),
            _fmt_row("Train", r_train),
            _fmt_row("Test", r_test),
            "",
        ]

        # Verdict specific to this variant.
        t_win = (r_train.get("win_rate") or 0) * 100
        e_win = (r_test.get("win_rate") or 0) * 100
        t_sharpe = r_train.get("sharpe") or float("nan")
        e_sharpe = r_test.get("sharpe") or float("nan")
        t_trades = r_train.get("filtered_trades") or 0
        e_trades = r_test.get("filtered_trades") or 0

        verdict_notes: list[str] = []
        # Require ≥ 10 out-of-sample trades before any pattern claim is
        # statistically defensible. At n < 10 the sample variance dominates
        # any comparison; reviewer flagged that the prior threshold (< 2)
        # let a 2-trade OOS sample mis-label as "Holds up".
        if e_trades < 10:
            verdict_notes.append(
                f"**Inconclusive** — test-set has only {e_trades} matched "
                f"trade(s); need ≥ 10 for a meaningful OOS verdict. "
                f"(observed: train {t_win:.0f}% win / test {e_win:.0f}% win)")
        elif abs(t_win - e_win) > 15:
            verdict_notes.append(
                f"**Overfit warning** — train win {t_win:.0f}% vs test win "
                f"{e_win:.0f}% differs by > 15 pp.")
        elif (t_sharpe > 0) != (e_sharpe > 0):
            verdict_notes.append(
                f"**Sharpe-sign flip** — train Sharpe {t_sharpe:+.2f} vs test "
                f"{e_sharpe:+.2f}. Edge is regime-dependent, not structural.")
        else:
            verdict_notes.append(
                f"**Holds up** — train/test win-rate {t_win:.0f}% / {e_win:.0f}%, "
                f"Sharpe {t_sharpe:+.2f} / {e_sharpe:+.2f}.")
        lines += verdict_notes + [""]

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--split-date", default="2025-01-01",
                   help="Train cutoff: all trades with entry < this date go into train.")
    p.add_argument("--variants", default="V3,V4,V5,V6",
                   help="Comma-separated subset of variants to validate.")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    signals_df = pd.read_parquet(_rv.SIGNALS_PATH)
    signals_df["date"] = pd.to_datetime(signals_df["date"])
    trades_df = pd.read_csv(_rv.TRADES_PATH)
    atr_series = _rv._load_nifty_atr(signals_df["date"])

    split_date = date.fromisoformat(args.split_date)
    window_start = signals_df["date"].min().date()
    window_end = signals_df["date"].max().date()
    log.info("Split %s → Train: %s..%s  Test: %s..%s",
             split_date, window_start, split_date, split_date, window_end)

    all_variants = _rv.make_variants()
    wanted = {v.strip() for v in args.variants.split(",")}
    variants = [v for v in all_variants if v.name in wanted]

    # Full is just the normal evaluation over the entire frame.
    sigs_train, trd_train = _filter_by_date(signals_df, trades_df, window_start, split_date)
    sigs_test, trd_test = _filter_by_date(signals_df, trades_df, split_date, window_end + pd.Timedelta(days=1))

    log.info("Train: %d signal-days, %d trades", len(sigs_train), len(trd_train))
    log.info("Test : %d signal-days, %d trades", len(sigs_test), len(trd_test))

    per_variant: dict[str, dict[str, dict]] = {}
    for v in variants:
        log.info("Evaluating %s (%s)", v.name, v.description)
        r_full = _rv.evaluate_variant(v, signals_df, trades_df, atr_series)
        r_train = _rv.evaluate_variant(v, sigs_train, trd_train, atr_series)
        r_test = _rv.evaluate_variant(v, sigs_test, trd_test, atr_series)
        per_variant[v.name] = {"full": r_full, "train": r_train, "test": r_test}
        log.info(
            "  full:  fires=%d  trades=%d  win=%s  sharpe=%s",
            r_full["firing_days"], r_full["filtered_trades"] or 0,
            f"{(r_full['win_rate'] or 0)*100:.0f}%", r_full["sharpe"],
        )
        log.info(
            "  train: fires=%d  trades=%d  win=%s  sharpe=%s",
            r_train["firing_days"], r_train["filtered_trades"] or 0,
            f"{(r_train['win_rate'] or 0)*100:.0f}%", r_train["sharpe"],
        )
        log.info(
            "  test:  fires=%d  trades=%d  win=%s  sharpe=%s",
            r_test["firing_days"], r_test["filtered_trades"] or 0,
            f"{(r_test['win_rate'] or 0)*100:.0f}%", r_test["sharpe"],
        )

    report = _write_report(per_variant, split_date)
    out_md = RESULTS_DIR / "time_split_report.md"
    out_md.write_text(report, encoding="utf-8")
    log.info("Wrote %s", out_md)
    print()
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
