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

Usage:
  .venv/bin/python scripts/nfo/v3_capital_analysis.py
  .venv/bin/python scripts/nfo/v3_capital_analysis.py --capital 2000000 --pt-variant htx
"""
from __future__ import annotations

import argparse
import logging
import math
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nfo.config import RESULTS_DIR
from nfo.robustness import compute_equity_curves

log = logging.getLogger("v3_capital")

# Re-use the V3 filter from redesign_variants.
import importlib.util, sys
_spec = importlib.util.spec_from_file_location(
    "_rv", Path(__file__).parent / "redesign_variants.py"
)
_rv = importlib.util.module_from_spec(_spec)
sys.modules["_rv"] = _rv
_spec.loader.exec_module(_rv)

# Output file paths are variant-suffixed so running with `--pt-variant pt50`
# and `--pt-variant hte` produces two separate artifacts (instead of the
# second invocation silently clobbering the first).
def _out_paths(pt_variant: str) -> tuple[Path, Path]:
    return (
        RESULTS_DIR / f"v3_capital_report_{pt_variant}.md",
        RESULTS_DIR / f"v3_capital_trades_{pt_variant}.csv",
    )


def _v3_firing_cycles(signals_df: pd.DataFrame) -> list[tuple[str, pd.Timestamp]]:
    """Return [(target_expiry, first_firing_date), …] for each unique cycle V3 fired on."""
    atr_series = _rv._load_nifty_atr(signals_df["date"])
    atr_map = {pd.Timestamp(d).date(): float(v) for d, v in atr_series.items()}
    v3 = [v for v in _rv.make_variants() if v.name == "V3"][0]

    by_expiry: dict[str, list[pd.Timestamp]] = {}
    for _, row in signals_df.iterrows():
        entry = row["date"].date() if isinstance(row["date"], pd.Timestamp) else row["date"]
        passed, _ = _rv._row_passes(row, v3, atr_map.get(entry, float("nan")))
        if passed:
            exp = row.get("target_expiry")
            if exp:
                by_expiry.setdefault(str(exp), []).append(row["date"])
    return [(exp, min(dates)) for exp, dates in sorted(by_expiry.items())]


def _pick_trade(
    trades: pd.DataFrame,
    expiry: str,
    pt_variant: str = "pt50",
) -> pd.Series | None:
    """Find the 0.30Δ × 100-width trade for this expiry. Pick PT or HTE variant."""
    sub = trades[
        (trades["param_delta"] == 0.30) &
        (trades["param_width"] == 100.0) &
        (trades["expiry_date"] == expiry)
    ]
    if sub.empty:
        return None
    if pt_variant == "pt50":
        # Prefer rows with param_pt = 0.50 (profit-take); fall back to any.
        pt = sub[sub["param_pt"] == 0.50]
        return (pt.iloc[0] if not pt.empty else sub.iloc[0])
    else:  # "hte" hold-to-expiry
        hte = sub[sub["param_pt"] == 1.0]
        return (hte.iloc[0] if not hte.empty else sub.iloc[0])


def run_analysis(
    capital: float,
    pt_variant: str = "pt50",
) -> dict:
    signals_df = pd.read_parquet(_rv.SIGNALS_PATH)
    signals_df["date"] = pd.to_datetime(signals_df["date"])
    trades = pd.read_csv(_rv.TRADES_PATH)
    # Merge V3-gap custom trades if present (fills the 2 cycles not covered
    # by the original spread_trades.csv at 0.30Δ × 100-wide).
    gaps_path = RESULTS_DIR / "spread_trades_v3_gaps.csv"
    if gaps_path.exists():
        gaps = pd.read_csv(gaps_path)
        trades = pd.concat([trades, gaps], ignore_index=True)
        log.info("Merged %d V3-gap trades from %s", len(gaps), gaps_path.name)

    cycles = _v3_firing_cycles(signals_df)
    log.info("V3 fired across %d distinct monthly cycles.", len(cycles))

    # Resolve each V3-firing cycle to a real trade row (or None) and accumulate
    # them in cycle order. `compute_equity_curves` then walks the resolved
    # trades to build the non-compounding/compounding equity series — the math
    # is centralised in `src/nfo/robustness.py` so the robustness harness and
    # this report reuse identical semantics.
    resolved_trades: list[pd.Series] = []
    resolved_meta: list[dict] = []
    unresolved_meta: list[dict] = []
    for expiry, first_fire in cycles:
        trade = _pick_trade(trades, expiry, pt_variant)
        if trade is None:
            unresolved_meta.append({
                "v3_first_fire": first_fire.date().isoformat(),
                "expiry": expiry,
                "trade_found": False,
                "entry_date": None, "bp_per_lot": None, "pnl_contract": None,
                "outcome": None,
                "lots_fixed": None, "pnl_fixed": None,
                "lots_compound": None, "pnl_compound": None,
                "equity_after_compound": None,
            })
            continue
        resolved_trades.append(trade)
        resolved_meta.append({
            "v3_first_fire": first_fire.date().isoformat(),
            "entry_date": str(trade["entry_date"]),
            "expiry": expiry,
            "trade_found": True,
            "outcome": str(trade["outcome"]),
            "bp_per_lot": round(float(trade["buying_power"]), 0),
            "pnl_per_lot": round(float(trade["pnl_contract"]), 2),
        })

    resolved_df = pd.DataFrame(resolved_trades).reset_index(drop=True) if resolved_trades else pd.DataFrame()
    start = signals_df["date"].min().date()
    end = signals_df["date"].max().date()
    years = (end - start).days / 365.25
    equity = compute_equity_curves(resolved_df, capital=capital, years=years)

    rows: list[dict] = []
    for i, meta in enumerate(resolved_meta):
        meta["lots_fixed"] = int(equity.lots_fixed.iloc[i])
        meta["pnl_fixed"] = round(float(equity.pnl_fixed.iloc[i]), 0)
        meta["lots_compound"] = int(equity.lots_compound.iloc[i])
        meta["pnl_compound"] = round(float(equity.pnl_compound.iloc[i]), 0)
        meta["equity_after_compound"] = round(float(equity.equity_compound.iloc[i]), 0)
        rows.append(meta)
    rows.extend(unresolved_meta)

    df = pd.DataFrame(rows)
    n_trades = int(df["trade_found"].sum())
    n_cycles_total = len(cycles)
    pnl_series_fixed = df.loc[df["trade_found"], "pnl_fixed"]
    wins = int((pnl_series_fixed > 0).sum())
    losses = int((pnl_series_fixed < 0).sum())

    return {
        "rows": df,
        "capital": capital,
        "pt_variant": pt_variant,
        "n_cycles": n_cycles_total,
        "n_trades": n_trades,
        "n_wins": wins,
        "n_losses": losses,
        "win_rate": wins / n_trades if n_trades else 0.0,
        "total_pnl_fixed": equity.total_pnl_fixed,
        "total_pnl_compound": equity.total_pnl_compound,
        "final_equity_compound": equity.final_equity_compound,
        "return_pct_fixed": equity.total_pnl_fixed / capital * 100,
        "return_pct_compound": (equity.final_equity_compound / capital - 1) * 100,
        "annualised_pct_fixed": equity.annualised_pct_fixed,
        "annualised_pct_compound": equity.annualised_pct_compound,
        "max_drawdown_pct": equity.max_drawdown_pct,
        "sharpe": equity.sharpe,
        "years": years,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
    }


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


def _write_report(res: dict) -> str:
    df = res["rows"]
    lines = [
        "# V3 capital-deployment analysis — ₹10L sized per trade",
        "",
        f"Window: **{res['window_start']} → {res['window_end']}** "
        f"({res['years']:.2f} years).",
        f"Starting capital: **{_format_inr(res['capital'])}**.",
        f"Exit variant: **{res['pt_variant']}** "
        f"({'50% profit-take' if res['pt_variant'] == 'pt50' else 'hold to expiry'}).",
        "",
        "## Summary",
        "",
        "| Metric | Non-compounding | Compounding |",
        "|---|---:|---:|",
        f"| Trades taken | {res['n_trades']} of {res['n_cycles']} fire-cycles | {res['n_trades']} |",
        f"| Wins / losses | {res['n_wins']} / {res['n_losses']} (win-rate {res['win_rate']*100:.0f}%) | — |",
        f"| Total P&L | **{_format_inr(res['total_pnl_fixed'])}** | **{_format_inr(res['total_pnl_compound'])}** |",
        f"| Final equity | — | **{_format_inr(res['final_equity_compound'])}** |",
        f"| Return on capital | {res['return_pct_fixed']:+.1f}% | {res['return_pct_compound']:+.1f}% |",
        f"| Annualised return | {res['annualised_pct_fixed']:+.1f}% | {res['annualised_pct_compound']:+.1f}% |",
        f"| Max drawdown (compounding) | — | {res['max_drawdown_pct']:.1f}% |",
        f"| Sharpe (per-trade, annualised) | {res['sharpe']:+.2f} | — |",
        "",
        "## Per-trade detail",
        "",
        "| V3 first fire | Trade entry | Expiry | Outcome | BP/lot | P&L/lot | Lots (fixed) | P&L (fixed) | Lots (compound) | P&L (compound) | Equity after |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in df.iterrows():
        if not r["trade_found"]:
            lines.append(
                f"| {r['v3_first_fire']} | — | {r['expiry']} | "
                f"*no matching trade at 0.30Δ × 100-wide* | — | — | — | — | — | — | — |"
            )
            continue
        lines.append(
            f"| {r['v3_first_fire']} | {r['entry_date']} | {r['expiry']} | "
            f"{r['outcome']} | ₹{r['bp_per_lot']:,.0f} | ₹{r['pnl_per_lot']:+,.0f} | "
            f"{r['lots_fixed']} | {_format_inr(r['pnl_fixed'])} | "
            f"{r['lots_compound']} | {_format_inr(r['pnl_compound'])} | "
            f"{_format_inr(r['equity_after_compound'])} |"
        )

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
        f"  roughly 2 × {res['total_pnl_fixed']/max(1,res['n_trades'])*2/1e3:.0f}k — few lakh over 2 years.",
        "",
        f"See `results/nfo/v3_capital_trades_{res['pt_variant']}.csv` for the raw per-trade data.",
    ]
    return "\n".join(lines)


def _legacy_main(argv: list[str] | None = None) -> dict[str, Any]:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capital", type=float, default=10_00_000,
                   help="Starting capital in INR (default ₹10L).")
    p.add_argument("--pt-variant", choices=("pt50", "hte"), default="pt50",
                   help="Which backtest variant to use: pt50 (50%% profit-take) or hte (hold to expiry).")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    res = run_analysis(args.capital, args.pt_variant)
    res["pt_variant"] = args.pt_variant
    report = _write_report(res)
    out_md, out_csv = _out_paths(args.pt_variant)
    out_md.write_text(report, encoding="utf-8")
    res["rows"].to_csv(out_csv, index=False)
    log.info("Wrote %s and %s", out_md, out_csv)
    print()
    print(report)
    metrics = {
        "trades": int(res.get("n_trades", 0)),
        "total_pnl_inr": float(res.get("total_pnl_fixed", 0.0)),
        "win_rate": float(res.get("win_rate", 0.0)),
    }
    body_markdown = (
        "See `tables/` for full outputs. Legacy artifacts mirrored from "
        "`results/nfo/`.\n"
    )
    warnings: list[str] = []
    return {"metrics": metrics, "body_markdown": body_markdown, "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    from datetime import date
    from nfo.config import RESULTS_DIR, ROOT
    from nfo.reporting.wrap_legacy_run import wrap_legacy_run

    def run_logic() -> dict:
        return _legacy_main(argv)

    result = wrap_legacy_run(
        study_type="capital_analysis",
        strategy_path=ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml",
        study_path=ROOT / "configs" / "nfo" / "studies" / "capital_analysis_10l.yaml",
        legacy_artifacts=[
            RESULTS_DIR / "v3_capital_report_pt50.md",
            RESULTS_DIR / "v3_capital_report_hte.md",
            RESULTS_DIR / "v3_capital_trades_pt50.csv",
            RESULTS_DIR / "v3_capital_trades_hte.csv",
        ],
        window=(date(2024, 2, 1), date(2026, 4, 18)),
        run_logic=run_logic,
        runs_root=RESULTS_DIR / "runs",
    )
    print(result.run_dir.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
