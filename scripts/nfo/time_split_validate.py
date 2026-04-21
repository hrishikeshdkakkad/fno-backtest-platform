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

P5-C2 shadow migration: V3's metrics are still computed authoritatively by
the legacy `_rv.evaluate_variant` pipeline (which counts *all* trades whose
entry_date lands on a V3 firing day), while `nfo.studies.time_split.run_time_split`
runs alongside on the cycle-matched selection (one trade per firing cycle)
and the verdict is logged for drift monitoring. The legacy V0-V6 iteration
and markdown report format are preserved byte-for-byte; only the V3 engine
shadow is new.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

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

# P6: dataset references for drift detection.
from nfo.config import DATA_DIR
from nfo.specs.study import DatasetRef as _DatasetRef

_DATASET_REFS = [
    _DatasetRef(
        dataset_id="historical_features_2024-01_2026-04",
        dataset_type="features",
        path=DATA_DIR / "datasets" / "features" / "historical_features_2024-01_2026-04",
    ),
    _DatasetRef(
        dataset_id="trade_universe_nifty_2024-01_2026-04",
        dataset_type="trade_universe",
        path=DATA_DIR / "datasets" / "trade_universe" / "trade_universe_nifty_2024-01_2026-04",
    ),
]


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


def _shadow_v3_time_split(
    signals_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    atr_series: pd.Series,
    *,
    split_date: date,
    window_start: date,
    window_end: date,
) -> Any:
    """Run `nfo.studies.time_split.run_time_split` for V3 as a shadow.

    Non-authoritative: the legacy `_rv.evaluate_variant` path remains the
    source of truth for the markdown report. The engine shadow logs its
    cycle-matched train/test split and verdict so drift is visible in the
    log stream. Exceptions are swallowed so the shadow can never break
    the report pipeline.
    """
    try:
        from nfo.config import ROOT as _ROOT
        from nfo.specs.loader import load_strategy
        from nfo.studies.time_split import run_time_split

        spec, _ = load_strategy(_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml")

        def _event_resolver(entry, dte):
            return "high" if not _rv._event_pass(
                entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"}, window_days=10,
            ) else "none"

        result = run_time_split(
            spec=spec, features_df=signals_df, atr_series=atr_series,
            trades_df=trades_df,
            train_window=(window_start, split_date - timedelta(days=1)),
            test_window=(split_date, window_end),
            event_resolver=_event_resolver,
        )
        log.info(
            "V3 engine shadow: n_train=%d n_test=%d verdict=%s "
            "(train_win=%.0f%% sharpe=%+.2f | test_win=%.0f%% sharpe=%+.2f)",
            result.n_train, result.n_test, result.verdict,
            result.train_stats.win_rate * 100, result.train_stats.sharpe,
            result.test_stats.win_rate * 100, result.test_stats.sharpe,
        )
        return result
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("V3 engine time-split shadow failed: %s", exc)
        return None


def _legacy_main(argv: list[str] | None = None) -> dict[str, Any]:
    """Legacy multi-variant (V3-V6) time-split evaluation + markdown report.

    V3 is additionally run through the engine via
    `nfo.studies.time_split.run_time_split` as a shadow; the legacy numbers
    remain authoritative (see module docstring for rationale).
    """
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

    sigs_train, trd_train = _filter_by_date(signals_df, trades_df, window_start, split_date)
    sigs_test, trd_test = _filter_by_date(
        signals_df, trades_df, split_date, window_end + pd.Timedelta(days=1),
    )
    log.info("Train: %d signal-days, %d trades", len(sigs_train), len(trd_train))
    log.info("Test : %d signal-days, %d trades", len(sigs_test), len(trd_test))

    per_variant: dict[str, dict[str, dict]] = {}
    for v in variants:
        log.info("Evaluating %s (%s)", v.name, v.description)
        per_variant[v.name] = {
            "full": _rv.evaluate_variant(v, signals_df, trades_df, atr_series),
            "train": _rv.evaluate_variant(v, sigs_train, trd_train, atr_series),
            "test": _rv.evaluate_variant(v, sigs_test, trd_test, atr_series),
        }
        if v.name == "V3":
            _shadow_v3_time_split(
                signals_df, trades_df, atr_series,
                split_date=split_date,
                window_start=window_start,
                window_end=window_end,
            )

    report = _write_report(per_variant, split_date)
    out_md = RESULTS_DIR / "time_split_report.md"
    out_md.write_text(report, encoding="utf-8")
    log.info("Wrote %s", out_md)
    print()
    print(report)
    return {
        "metrics": {"n_variants": int(len(per_variant))},
        "body_markdown": (
            "See `tables/` for full outputs. Legacy artifacts mirrored from "
            "`results/nfo/`.\n"
        ),
        "warnings": [],
    }


def main(argv: list[str] | None = None) -> int:
    from datetime import date
    from nfo.config import RESULTS_DIR, ROOT
    from nfo.reporting.wrap_legacy_run import wrap_legacy_run

    def run_logic() -> dict:
        return _legacy_main(argv)

    result = wrap_legacy_run(
        study_type="time_split",
        strategy_path=ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml",
        study_path=ROOT / "configs" / "nfo" / "studies" / "time_split_default.yaml",
        legacy_artifacts=[
            RESULTS_DIR / "time_split_report.md",
        ],
        window=(date(2024, 2, 1), date(2026, 4, 18)),
        run_logic=run_logic,
        runs_root=RESULTS_DIR / "runs",
        dataset_refs=_DATASET_REFS,
    )
    print(result.run_dir.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
