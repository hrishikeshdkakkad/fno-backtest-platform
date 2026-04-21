"""₹10L capital deployment analysis — V3 firing days → realistic P&L.

For each monthly cycle where V3 fired at least once, take the corresponding
real backtest trade (0.30Δ × 100-width spread, matched by expiry_date) and
size it to ₹10,00,000 of buying power. Report cumulative equity, drawdown,
Sharpe, and annualised return.

Two scenarios:
  - **Non-compounding**: each trade always sized to the initial ₹10L.
    Answers "if the strategy runs at a fixed size, total P&L after 2 yrs?"
  - **Compounding**: equity grows/shrinks with each trade; next trade
    deploys full running equity. Answers "if reinvested every cycle?"

After P5-B2, the body delegates the engine pipeline to
`nfo.studies.capital_analysis.run_capital_analysis`, which composes
triggers → cycles → cycle_matched selection → equity curves → summary stats.
The remaining script-local logic is (a) loading signals + ATR, (b) shaping
the selected-trades frame into the legacy 12-column capital CSV, and (c)
writing the markdown report.

Usage:
  .venv/bin/python scripts/nfo/v3_capital_analysis.py
  .venv/bin/python scripts/nfo/v3_capital_analysis.py --pt-variant hte
"""
from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nfo.config import RESULTS_DIR, ROOT
from nfo.specs.loader import load_strategy
from nfo.studies.capital_analysis import CapitalAnalysisResult, run_capital_analysis

log = logging.getLogger("v3_capital")

_HERE = Path(__file__).resolve().parent

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


def _load_rv_module(alias: str = "_legacy_rv_v3ca"):
    """Import `scripts/nfo/redesign_variants.py` directly (scripts/ isn't a
    package). Used for the legacy event-resolver + cached-parquet ATR loader.
    """
    spec = importlib.util.spec_from_file_location(alias, _HERE / "redesign_variants.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


def _format_inr(x: float) -> str:
    """Indian-lakh/crore pretty-formatting, e.g. ₹10,00,000 → ₹10.00L."""
    if x is None or not np.isfinite(x):
        return "—"
    sign = "-" if x < 0 else ""
    x = abs(x)
    if x >= 1_00_00_000:
        return f"{sign}₹{x / 1_00_00_000:.2f}Cr"
    if x >= 1_00_000:
        return f"{sign}₹{x / 1_00_000:.2f}L"
    return f"{sign}₹{x:,.0f}"


def _build_legacy_rows(
    result: CapitalAnalysisResult,
    capital: float,
) -> pd.DataFrame:
    """Project the engine result back to the legacy 12-column schema.

    Columns: v3_first_fire, entry_date, expiry, trade_found, outcome,
    bp_per_lot, pnl_per_lot, lots_fixed, pnl_fixed, lots_compound,
    pnl_compound, equity_after_compound.
    """
    selected = result.selected_trades
    equity = result.equity_result
    rows: list[dict] = []
    for i in range(len(selected)):
        trade = selected.iloc[i]
        rows.append({
            "v3_first_fire": str(trade.get("first_fire_date", "")),
            "entry_date": str(trade.get("entry_date", "")),
            "expiry": str(trade.get("expiry_date", "")),
            "trade_found": True,
            "outcome": str(trade.get("outcome", "")),
            "bp_per_lot": round(float(trade["buying_power"]), 0),
            "pnl_per_lot": round(float(trade["pnl_contract"]), 2),
            "lots_fixed": int(equity.lots_fixed.iloc[i]),
            "pnl_fixed": round(float(equity.pnl_fixed.iloc[i]), 0),
            "lots_compound": int(equity.lots_compound.iloc[i]),
            "pnl_compound": round(float(equity.pnl_compound.iloc[i]), 0),
            "equity_after_compound": round(float(equity.equity_compound.iloc[i]), 0),
        })
    return pd.DataFrame(rows)


def _write_legacy_report(
    variant: str,
    result: CapitalAnalysisResult,
    rows: pd.DataFrame,
    capital: float,
    window_start: str,
    window_end: str,
    results_dir: Path,
) -> Path:
    """Reproduce the legacy markdown report for `v3_capital_report_<variant>.md`."""
    equity = result.equity_result
    n_trades = int(rows["trade_found"].sum()) if not rows.empty else 0
    pnl_fixed_series = rows.loc[rows["trade_found"], "pnl_fixed"] if not rows.empty else pd.Series(dtype=float)
    wins = int((pnl_fixed_series > 0).sum())
    losses = int((pnl_fixed_series < 0).sum())
    win_rate = (wins / n_trades) if n_trades else 0.0
    return_pct_fixed = equity.total_pnl_fixed / capital * 100 if capital else 0.0
    return_pct_compound = (equity.final_equity_compound / capital - 1) * 100 if capital else 0.0

    lines = [
        "# V3 capital-deployment analysis — ₹10L sized per trade",
        "",
        f"Window: **{window_start} → {window_end}** ({result.years:.2f} years).",
        f"Starting capital: **{_format_inr(capital)}**.",
        f"Exit variant: **{variant}** "
        f"({'50% profit-take' if variant == 'pt50' else 'hold to expiry'}).",
        "",
        "## Summary",
        "",
        "| Metric | Non-compounding | Compounding |",
        "|---|---:|---:|",
        f"| Trades taken | {n_trades} of {n_trades} fire-cycles | {n_trades} |",
        f"| Wins / losses | {wins} / {losses} (win-rate {win_rate*100:.0f}%) | — |",
        f"| Total P&L | **{_format_inr(equity.total_pnl_fixed)}** | **{_format_inr(equity.total_pnl_compound)}** |",
        f"| Final equity | — | **{_format_inr(equity.final_equity_compound)}** |",
        f"| Return on capital | {return_pct_fixed:+.1f}% | {return_pct_compound:+.1f}% |",
        f"| Annualised return | {equity.annualised_pct_fixed:+.1f}% | {equity.annualised_pct_compound:+.1f}% |",
        f"| Max drawdown (compounding) | — | {equity.max_drawdown_pct:.1f}% |",
        f"| Sharpe (per-trade, annualised) | {equity.sharpe:+.2f} | — |",
        "",
        "## Per-trade detail",
        "",
        "| V3 first fire | Trade entry | Expiry | Outcome | BP/lot | P&L/lot | Lots (fixed) | P&L (fixed) | Lots (compound) | P&L (compound) | Equity after |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in rows.iterrows():
        lines.append(
            f"| {r['v3_first_fire']} | {r['entry_date']} | {r['expiry']} | "
            f"{r['outcome']} | ₹{r['bp_per_lot']:,.0f} | ₹{r['pnl_per_lot']:+,.0f} | "
            f"{r['lots_fixed']} | {_format_inr(r['pnl_fixed'])} | "
            f"{r['lots_compound']} | {_format_inr(r['pnl_compound'])} | "
            f"{_format_inr(r['equity_after_compound'])} |"
        )
    per_trade = (equity.total_pnl_fixed / max(1, n_trades)) * 2 / 1e3
    lines += [
        "",
        "## Interpretation",
        "",
        "**Two crucial caveats** to set expectations honestly:",
        "",
        "1. **BP per lot ≈ ₹8,500** — a ₹10L allocation runs **~118 lots per trade**. That's "
        "   *enormous* leverage on one cycle. A single max-loss event would wipe 40–50% of "
        "   equity at that sizing. Retail prudence is **10–20% of capital per trade**, not 100%.",
        "",
        "2. **Sample size is 8 trades** — even if the filter is real, 8 trades is too few to "
        "   estimate true long-run return. The V3 backtest metrics "
        "   (90% win, Sharpe 1.75) could easily degrade to 70% / 0.5 in a different regime.",
        "",
        "### What to actually take away",
        "",
        "- The **shape of the answer** — not the magnitude — is what matters.",
        "- V3 produces a small number of high-quality trades. Winners outnumber losers, and",
        "  losers (if any) come from specific cycles the filter didn't catch early enough.",
        "- At **realistic retail sizing (1–2 lots)** over these 8 trades, the total P&L is",
        f"  roughly 2 × {per_trade:.0f}k — few lakh over 2 years.",
        "",
        f"See `results/nfo/v3_capital_trades_{variant}.csv` for the raw per-trade data.",
    ]
    out_md = results_dir / f"v3_capital_report_{variant}.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")
    return out_md


def _legacy_main(argv: list[str] | None = None) -> dict[str, Any]:
    """P5-B2: thin wrapper over `nfo.studies.capital_analysis.run_capital_analysis`.

    Loads the v3_frozen spec, signals, trades, and ATR; runs the engine study;
    writes the legacy CSV + markdown artifacts; returns the `wrap_legacy_run`
    contract dict.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pt-variant", choices=("pt50", "hte"), default="pt50",
        help="Exit variant: pt50 (50%% profit-take) or hte (hold to expiry).",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    capital = 10_00_000.0
    spec, _ = load_strategy(ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml")
    signals_df = pd.read_parquet(RESULTS_DIR / "historical_signals.parquet")
    signals_df["date"] = pd.to_datetime(signals_df["date"])

    trades = pd.read_csv(RESULTS_DIR / "spread_trades.csv")
    gaps_path = RESULTS_DIR / "spread_trades_v3_gaps.csv"
    if gaps_path.exists():
        trades = pd.concat([trades, pd.read_csv(gaps_path)], ignore_index=True)

    rv = _load_rv_module()
    atr = rv.load_nifty_atr(signals_df["date"])

    def _event_resolver(entry, dte):
        return "high" if not rv._event_pass(
            entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"}, window_days=10,
        ) else "none"

    result = run_capital_analysis(
        spec=spec, features_df=signals_df, atr_series=atr, trades_df=trades,
        pt_variant=args.pt_variant, capital_inr=capital,
        event_resolver=_event_resolver,
    )

    # Project to legacy 12-column schema + write artifacts.
    rows = _build_legacy_rows(result, capital)
    out_csv = RESULTS_DIR / f"v3_capital_trades_{args.pt_variant}.csv"
    rows.to_csv(out_csv, index=False)

    window_start = signals_df["date"].min().date().isoformat()
    window_end = signals_df["date"].max().date().isoformat()
    out_md = _write_legacy_report(
        args.pt_variant, result, rows, capital,
        window_start, window_end, RESULTS_DIR,
    )
    log.info("Wrote %s and %s", out_md, out_csv)

    return {
        "metrics": {
            "trades": int(len(rows)),
            "total_pnl_inr": float(result.equity_result.total_pnl_fixed),
            "win_rate": float(result.stats.win_rate),
        },
        "body_markdown": (
            "See `tables/` for full outputs. Legacy artifacts mirrored from "
            "`results/nfo/`.\n"
        ),
        "warnings": [],
    }


def main(argv: list[str] | None = None) -> int:
    from datetime import date
    from nfo.reporting.wrap_legacy_run import wrap_legacy_run

    # Parse --pt-variant before wrapping so the run's legacy_artifacts mirror
    # only the variant actually produced by this invocation. Otherwise a stale
    # sibling CSV from an earlier run would leak into the fresh run dir and
    # misrepresent its provenance.
    parser = argparse.ArgumentParser()
    parser.add_argument("--pt-variant", choices=("pt50", "hte"), default="hte")
    args, _ = parser.parse_known_args(argv)
    variant = args.pt_variant

    def run_logic() -> dict:
        return _legacy_main(argv)

    result = wrap_legacy_run(
        study_type="capital_analysis",
        strategy_path=ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml",
        study_path=ROOT / "configs" / "nfo" / "studies" / "capital_analysis_10l.yaml",
        legacy_artifacts=[
            RESULTS_DIR / f"v3_capital_report_{variant}.md",
            RESULTS_DIR / f"v3_capital_trades_{variant}.csv",
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
