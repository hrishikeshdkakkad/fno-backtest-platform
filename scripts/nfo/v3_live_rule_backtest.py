"""V3 live-rule backtest — enter only on or after the first V3 fire.

Why this exists
---------------
The existing cycle-matched trades in `results/nfo/v3_capital_*.csv`
resolve each V3-firing cycle to a row from `spread_trades.csv` whose
`entry_date` is the canonical 35-DTE grid (computed by
`calendar_nfo.build_cycles`). That grid entry has no relationship to the
V3 firing date; it's just "35 days before expiry, snapped to the next
NSE trading day."

A **live rule** cannot do that — you only know V3 has fired on or after
the firing date. If the canonical 35-DTE entry predates V3's first fire
for a cycle, the backtest used information from the future to place the
trade. Four of the eight V3 cycles in the current window suffer from
this look-ahead bias (by 1, 2, 9, and 15 trading days).

This script fixes it: for each V3 firing cycle, enter on the first-fire
date (or the next NSE trading day if the fire lands on a non-session
day) and run the same cycle simulator the canonical backtest uses.
Outputs are fully recomputed; no trades are fabricated.

Reuses `scripts/nfo/v3_fill_gaps.run_custom_cycle` (the exact
`_run_cycle` logic with an explicit entry date).

Outputs
-------
- `results/nfo/v3_live_trades_pt50.csv`
- `results/nfo/v3_live_trades_hte.csv`
- `results/nfo/v3_live_report.md`
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from nfo import calibrate
from nfo.client import DhanClient
from nfo.config import RESULTS_DIR
from nfo.data import load_underlying_daily
from nfo.robustness import compute_equity_curves
from nfo.spread import SpreadConfig
from nfo.universe import get as get_under

log = logging.getLogger("v3_live_rule")

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_v3fg", _HERE / "v3_fill_gaps.py")
_v3fg = importlib.util.module_from_spec(_spec)
sys.modules["_v3fg"] = _v3fg
_spec.loader.exec_module(_v3fg)


# (label, profit_take, manage_at_dte) — matches the frozen-spec variants.
VARIANT_CONFIGS: tuple[tuple[str, float, int | None], ...] = (
    ("pt50", 0.50, 21),
    ("hte", 1.00, None),
)

CAPITAL = 10_00_000


def _v3_cycles(signals_df: pd.DataFrame) -> list[tuple[date, date]]:
    """[(first_fire_date, target_expiry_date), ...] — one per cycle V3 fires on."""
    import redesign_variants as rv  # noqa: E402

    v3 = next(v for v in rv.make_variants() if v.name == "V3")
    atr_series = rv.load_nifty_atr(signals_df["date"])
    fires = rv.get_firing_dates(v3, signals_df, atr_series)

    by_expiry: dict[str, list[pd.Timestamp]] = {}
    for fire_date, _ in fires:
        row = signals_df[signals_df["date"].dt.date == fire_date]
        if row.empty:
            continue
        exp_str = str(row["target_expiry"].iloc[0])
        if not exp_str:
            continue
        by_expiry.setdefault(exp_str, []).append(pd.Timestamp(fire_date))
    out: list[tuple[date, date]] = []
    for exp_str in sorted(by_expiry):
        first_fire = min(by_expiry[exp_str]).date()
        out.append((first_fire, date.fromisoformat(exp_str)))
    return out


def _first_session_on_or_after(target: date, spot_daily: pd.DataFrame) -> date | None:
    """Snap a target date forward to the next NSE trading day present in the
    daily bar frame. If V3 fires on a weekend / holiday, a live rule enters
    on the next session, not on the fire date itself.
    """
    later = spot_daily.loc[spot_daily["date"] >= pd.Timestamp(target), "date"]
    return None if later.empty else later.iloc[0].date()


def _format_inr(x: float) -> str:
    sign = "-" if x < 0 else ""
    v = abs(float(x))
    if v >= 1_00_00_000:
        return f"{sign}₹{v / 1_00_00_000:.2f}Cr"
    if v >= 1_00_000:
        return f"{sign}₹{v / 1_00_000:.2f}L"
    return f"{sign}₹{v:,.0f}"


def _legacy_main() -> dict[str, Any]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    # Make redesign_variants importable for `_v3_cycles`.
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))

    signals_df = pd.read_parquet(RESULTS_DIR / "historical_signals.parquet")
    signals_df["date"] = pd.to_datetime(signals_df["date"])
    cycles = _v3_cycles(signals_df)
    log.info("V3 fired on %d distinct cycles.", len(cycles))

    under = get_under("NIFTY")
    t0 = time.time()
    with DhanClient() as client:
        spot_daily = load_underlying_daily(
            client, under, from_date="2023-12-15", to_date="2026-04-18",
        )
        per_variant_rows: dict[str, list[dict]] = {v: [] for v, _, _ in VARIANT_CONFIGS}
        for variant_name, pt, manage in VARIANT_CONFIGS:
            cfg = SpreadConfig(
                underlying="NIFTY", target_delta=0.30, target_dte=35,
                profit_take=pt, manage_at_dte=manage, margin_multiplier=1.5,
                spread_width=100.0,
            )
            log.info("=== variant %s ===", variant_name)
            for first_fire, expiry in cycles:
                entry_day = _first_session_on_or_after(first_fire, spot_daily)
                if entry_day is None or entry_day >= expiry:
                    log.warning(
                        "Skip: cycle %s → %s (no session on/after fire before expiry)",
                        first_fire, expiry,
                    )
                    continue
                trade = _v3fg.run_custom_cycle(
                    client, cfg, under, entry_day, expiry, spot_daily,
                )
                if trade is None:
                    log.warning("Skip: cycle %s → %s (run_custom_cycle returned None)",
                                first_fire, expiry)
                    continue
                row = asdict(trade)
                row["v3_first_fire"] = first_fire.isoformat()
                row["variant"] = variant_name
                row["param_delta"] = 0.30
                row["param_width"] = 100.0
                row["param_pt"] = pt
                row["param_manage"] = manage if manage is not None else 0
                per_variant_rows[variant_name].append(row)
                log.info(
                    "%s: entry %s (fire %s) → exp %s  pnl=₹%.0f",
                    variant_name, entry_day, first_fire, expiry, trade.pnl_contract,
                )

    results = {}
    for variant_name, _, _ in VARIANT_CONFIGS:
        df = pd.DataFrame(per_variant_rows[variant_name])
        df.to_csv(RESULTS_DIR / f"v3_live_trades_{variant_name}.csv", index=False)
        results[variant_name] = df

    # Render a compact comparison report.
    start = signals_df["date"].min().date()
    end = signals_df["date"].max().date()
    years = (end - start).days / 365.25
    report_lines: list[str] = [
        "# V3 live-rule backtest",
        "",
        "Entry date is forced to the first V3 firing session (or the next NSE trading",
        "day if the fire lands off-session). This removes the look-ahead bias present",
        "in the canonical 35-DTE-grid capital report — four of the eight V3 cycles",
        "had their canonical entry *before* V3 fired, which is information a live",
        "system cannot use.",
        "",
        f"Window: {start.isoformat()} → {end.isoformat()} ({years:.2f} years).",
        f"Capital: {_format_inr(CAPITAL)} per cycle.",
        "",
    ]
    for variant_name, _, _ in VARIANT_CONFIGS:
        df = results[variant_name]
        if df.empty:
            continue
        stats = calibrate.summary_stats(df)
        eq = compute_equity_curves(df, capital=CAPITAL, years=years)
        # Compare against the canonical (look-ahead) numbers.
        canonical_path = RESULTS_DIR / f"v3_capital_trades_{variant_name}.csv"
        canonical_txt = ""
        if canonical_path.exists():
            canon = pd.read_csv(canonical_path)
            canon = canon[canon["trade_found"] == True]  # noqa: E712
            canon_total = float(canon["pnl_per_lot"].sum())
            delta = df["pnl_contract"].sum() / 65 - canon_total
            canonical_txt = (
                f"  (canonical look-ahead total per-lot: {_format_inr(canon_total)}; "
                f"delta vs live rule: {_format_inr(delta)})"
            )
        report_lines.extend([
            f"## {variant_name.upper()}",
            "",
            f"- Trades: **{stats.n}**",
            f"- Win rate: **{stats.win_rate*100:.0f}%**",
            f"- Total per-lot P&L: **{_format_inr(df['pnl_contract'].sum() / 65)}** "
            f"(per-contract) {canonical_txt}",
            f"- Final equity (compound ₹10L): **{_format_inr(eq.final_equity_compound)}**",
            f"- CAGR (compound): **{eq.annualised_pct_compound:+.1f}%**",
            f"- Max DD: **{eq.max_drawdown_pct:.1f}%**",
            f"- Per-trade Sharpe (capital-aware): **{eq.sharpe:+.2f}**",
            "",
            "| cycle first-fire | entry used | expiry | outcome | per-lot P&L |",
            "|---|---|---|---|---:|",
        ])
        for _, r in df.iterrows():
            report_lines.append(
                f"| {r['v3_first_fire']} | {r['entry_date']} | {r['expiry_date']} | "
                f"{r['outcome']} | {_format_inr(r['pnl_contract'] / 65)} |"
            )
        report_lines.append("")
    (RESULTS_DIR / "v3_live_report.md").write_text("\n".join(report_lines), encoding="utf-8")
    log.info("Done in %.1fs. Wrote v3_live_report.md + 2 CSVs.", time.time() - t0)
    metrics: dict[str, Any] = {}
    for variant_name, _, _ in VARIANT_CONFIGS:
        df = results.get(variant_name)
        metrics[f"{variant_name}_trades"] = int(len(df)) if df is not None else 0
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
        return _legacy_main()

    result = wrap_legacy_run(
        study_type="live_replay",
        strategy_path=ROOT / "configs" / "nfo" / "strategies" / "v3_live_rule.yaml",
        study_path=ROOT / "configs" / "nfo" / "studies" / "live_replay_default.yaml",
        legacy_artifacts=[
            RESULTS_DIR / "v3_live_trades_pt50.csv",
            RESULTS_DIR / "v3_live_trades_hte.csv",
            RESULTS_DIR / "v3_live_report.md",
        ],
        window=(date(2024, 2, 1), date(2026, 4, 18)),
        run_logic=run_logic,
        runs_root=RESULTS_DIR / "runs",
    )
    print(result.run_dir.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
