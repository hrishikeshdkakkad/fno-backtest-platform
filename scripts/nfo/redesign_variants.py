"""Iterative filter redesign loop — prototype 7 variants, measure, pick winner.

Operates purely on cached data:
  - `results/nfo/historical_signals.parquet`  (495 days × signals)
  - `results/nfo/spread_trades.csv`           (70 real trades with PnL)
  - `data/nfo/index/NIFTY_*.parquet`          (for ATR recomputation)

Zero Dhan / Parallel calls. Each variant applies a filter_fn row-wise to the
signals parquet; days where filter_fn is True are "firing days". Real trades
whose `entry_date` matches a firing day form the *filtered trade set* — we
measure Sharpe / win-rate / max-loss on THAT set only, which is the
out-of-sample validation: does the proposed filter preferentially cover the
good entries?

Success criteria (all four must hold):
  - firing_rate ∈ [5, 20] days/year (≈ 10-40 / 2 yrs)
  - win_rate_on_filtered_trades ≥ 0.85
  - sharpe_lift ≥ 30% vs unfiltered baseline
  - max_loss_rate_on_filtered < baseline (4.3%)

Outputs:
  - `results/nfo/redesign_comparison.csv` — one row per variant
  - `results/nfo/redesign_comparison.md`  — readable report + final verdict
  - `results/nfo/redesign_winner.json`    — adopted filter config (if any)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from nfo import calibrate, signals as sig_mod
from nfo.config import DATA_DIR, RESULTS_DIR

log = logging.getLogger("redesign_variants")

SIGNALS_PATH = RESULTS_DIR / "historical_signals.parquet"
TRADES_PATH = RESULTS_DIR / "spread_trades.csv"
OUT_CSV = RESULTS_DIR / "redesign_comparison.csv"
OUT_MD = RESULTS_DIR / "redesign_comparison.md"
OUT_WINNER = RESULTS_DIR / "redesign_winner.json"

BASELINE_SHARPE = -0.843
BASELINE_WIN = 0.80
BASELINE_MAX_LOSS_RATE = 0.0732


# ── Event rule helpers ──────────────────────────────────────────────────────
#
# Each variant picks a severity_map (which event kinds are "high") and an
# event_window_days cap (how far into the cycle we look). `row.events_in_window`
# is a pre-computed string from historical_backtest.py; we re-parse it with the
# hardcoded event calendar to apply the variant's window cap.

try:
    from scripts.nfo.historical_backtest import HARD_EVENTS  # type: ignore[import-not-found]
except Exception:
    HARD_EVENTS = None   # type: ignore[assignment]


def _load_hard_events() -> list[tuple[date, str, str]]:
    """Load the hardcoded event list from historical_backtest.py without
    depending on package import (scripts/ isn't a package)."""
    global HARD_EVENTS
    if HARD_EVENTS is not None:
        return HARD_EVENTS
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location(
        "_hb", Path(__file__).parent / "historical_backtest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_hb"] = mod
    spec.loader.exec_module(mod)
    HARD_EVENTS = mod.HARD_EVENTS
    return HARD_EVENTS


def _event_pass(
    entry_date: date,
    dte: int,
    *,
    severity_high_kinds: set[str],
    window_days: int | None = None,
) -> bool:
    """Return True if NO "high" event falls inside this variant's window.

    `severity_high_kinds` — set of {"RBI","FOMC","CPI","BUDGET"} that the
    variant treats as high severity. `window_days` caps the lookahead; None
    means "use full DTE".
    """
    if not np.isfinite(dte) or dte <= 0:
        return True
    lookahead = int(dte) if window_days is None else min(int(dte), window_days)
    horizon = entry_date + timedelta(days=lookahead)
    events = _load_hard_events()
    for d, _name, kind in events:
        if entry_date <= d <= horizon and kind in severity_high_kinds:
            return False
    return True


# ── Variant definitions ─────────────────────────────────────────────────────


@dataclass(slots=True)
class Variant:
    name: str
    description: str
    # Thresholds (override by variant)
    vix_rich: float = 20.0
    vix_pct_rich: float = 0.80
    iv_rv_rich: float = -2.0
    pullback_pct: float = 2.0        # legacy rule
    pullback_atr: float | None = None  # when set, use ATR-scaled pullback instead
    iv_rank_rich: float = 0.60
    trend_min: int = 2
    # Event rule
    severity_high_kinds: set[str] = field(default_factory=lambda: {"RBI","FOMC","CPI","BUDGET"})
    event_window_days: int | None = None    # None = full DTE; int = first N days
    # Grade rule
    specific_pass_gate: bool = False         # if True, require s3+s6+s8 AND ≥1 of (s1,s2,s5)
    min_score: int = 7                       # min passes of 7 countable signals (excl. s7)


def make_variants() -> list[Variant]:
    return [
        Variant(
            name="V0",
            description="Baseline — current thresholds, CPI=high, full-DTE window, 7/7 required",
        ),
        Variant(
            name="V1",
            description="Demote CPI to medium — only RBI/FOMC/Budget count as 'high'",
            severity_high_kinds={"RBI","FOMC","BUDGET"},
        ),
        Variant(
            name="V2",
            description="V1 + event window = first 10 days of cycle only",
            severity_high_kinds={"RBI","FOMC","BUDGET"},
            event_window_days=10,
        ),
        Variant(
            name="V3",
            description="V2 + specific-pass gate (IV-RV + trend + event + ≥1 of VIX/IV-rank)",
            severity_high_kinds={"RBI","FOMC","BUDGET"},
            event_window_days=10,
            specific_pass_gate=True,
            min_score=4,   # loosen: 4 of 7 when specific gate is in force
        ),
        Variant(
            name="V4",
            description="V3 + tuned thresholds (vix_rich=22, pullback_atr=1.5)",
            vix_rich=22.0,
            vix_pct_rich=0.80,
            iv_rv_rich=-2.0,
            pullback_atr=1.5,
            iv_rank_rich=0.60,
            severity_high_kinds={"RBI","FOMC","BUDGET"},
            event_window_days=10,
            specific_pass_gate=True,
            min_score=4,
        ),
        Variant(
            name="V5",
            description="V4 + relaxed grade (score ≥ 3 of 7, keep specific gate)",
            vix_rich=22.0,
            pullback_atr=1.5,
            severity_high_kinds={"RBI","FOMC","BUDGET"},
            event_window_days=10,
            specific_pass_gate=True,
            min_score=3,
        ),
        Variant(
            name="V6",
            description="V4 minus specific-pass gate — broadest variant that kept tuned thresholds",
            vix_rich=22.0,
            pullback_atr=1.5,
            severity_high_kinds={"RBI","FOMC","BUDGET"},
            event_window_days=10,
            specific_pass_gate=False,
            min_score=3,
        ),
    ]


# ── Evaluation ──────────────────────────────────────────────────────────────


def _load_nifty_atr(parquet_dates: pd.Series) -> pd.Series:
    """Return ATR-14 aligned to the parquet's date index. Loads cached NIFTY
    bars, computes rolling Wilder ATR, then forward-fills to trading-day grid."""
    root = DATA_DIR / "index"
    frames = [pd.read_parquet(p) for p in root.glob("NIFTY_*.parquet")]
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    atr = sig_mod.atr(df, 14)
    atr_by_date = pd.Series(atr.values, index=df["date"])
    return atr_by_date.reindex(pd.to_datetime(parquet_dates.values), method="ffill")


def _row_passes(row: pd.Series, variant: Variant, atr_value: float) -> tuple[bool, dict]:
    """Apply variant's filter to one parquet row. Returns (passed, detail_dict)."""
    # Re-evaluate signal booleans at variant's thresholds (ignore the baked-in
    # ones in the parquet — they were computed under V0's rules).
    vix = row.get("vix", np.nan)
    s1 = bool(np.isfinite(vix) and vix > variant.vix_rich)
    vpct = row.get("vix_pct_3mo", np.nan)
    s2 = bool(np.isfinite(vpct) and vpct >= variant.vix_pct_rich)
    iv_rv = row.get("iv_minus_rv", np.nan)
    s3 = bool(np.isfinite(iv_rv) and iv_rv >= variant.iv_rv_rich) if np.isfinite(iv_rv) else None
    spot = row.get("spot", np.nan)
    if variant.pullback_atr is not None and np.isfinite(atr_value) and atr_value > 0:
        # ATR-scaled: rebuild pullback_atr from pullback_pct × spot / atr.
        pb_pct = row.get("pullback_pct", 0.0) or 0.0
        pb_atr = (pb_pct / 100.0) * spot / atr_value if np.isfinite(spot) else 0.0
        s4 = pb_atr >= variant.pullback_atr
    else:
        s4 = bool((row.get("pullback_pct") or 0.0) >= variant.pullback_pct)
    ivr = row.get("iv_rank_12mo", np.nan)
    s5 = bool(np.isfinite(ivr) and ivr >= variant.iv_rank_rich)
    s6 = bool((row.get("trend_score") or 0) >= variant.trend_min)
    entry = row["date"]
    if isinstance(entry, pd.Timestamp):
        entry = entry.date()
    dte = row.get("dte", np.nan)
    s8 = _event_pass(
        entry, int(dte) if np.isfinite(dte) else 35,
        severity_high_kinds=variant.severity_high_kinds,
        window_days=variant.event_window_days,
    )

    # Count passes. s3 might be None (missing chain data); treat as fail.
    passes = {"s1": s1, "s2": s2, "s3": bool(s3) if s3 is not None else False,
              "s4": s4, "s5": s5, "s6": s6, "s8": s8}
    score = sum(1 for v in passes.values() if v)

    # Specific-pass gate: require s3 + s6 + s8 AND at least one of s1/s2/s5.
    if variant.specific_pass_gate:
        core_ok = passes["s3"] and passes["s6"] and passes["s8"]
        vol_ok = passes["s1"] or passes["s2"] or passes["s5"]
        if not (core_ok and vol_ok):
            return False, {"score": score, **passes}

    return score >= variant.min_score, {"score": score, **passes}


def get_firing_dates(
    variant: Variant,
    signals_df: pd.DataFrame,
    atr_series: pd.Series,
) -> list[tuple[date, dict]]:
    """Return the list of (entry_date, detail) where `variant` fires.

    Extracted from `evaluate_variant` so external scripts (e.g. robustness
    harnesses) can reuse the filter evaluation without needing to reach into
    the private `_row_passes` function or re-implement the row loop.
    """
    atr_by_date: dict[date, float] = {}
    for d, v in atr_series.items():
        atr_by_date[pd.Timestamp(d).date()] = float(v) if np.isfinite(v) else np.nan
    fires: list[tuple[date, dict]] = []
    for _, row in signals_df.iterrows():
        entry = row["date"]
        if isinstance(entry, pd.Timestamp):
            entry = entry.date()
        atr_val = atr_by_date.get(entry, np.nan)
        passed, detail = _row_passes(row, variant, atr_val)
        if passed:
            fires.append((entry, detail))
    return fires


def load_nifty_atr(parquet_dates: pd.Series) -> pd.Series:
    """Public alias for the cached-parquet ATR loader. See `_load_nifty_atr`."""
    return _load_nifty_atr(parquet_dates)


def evaluate_variant(
    variant: Variant,
    signals_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    atr_series: pd.Series,
) -> dict:
    """Apply the variant filter, cross-reference with trades, compute metrics."""
    fires = get_firing_dates(variant, signals_df, atr_series)

    n_days = len(signals_df)
    n_fires = len(fires)
    firing_rate_per_year = n_fires / (n_days / 252) if n_days else 0

    # Cross-reference firing dates with real-trade entry dates.
    firing_dates = {d for d, _ in fires}
    trades_df = trades_df.copy()
    trades_df["entry_date"] = pd.to_datetime(trades_df["entry_date"]).dt.date
    filtered_trades = trades_df[trades_df["entry_date"].isin(firing_dates)]

    if filtered_trades.empty:
        return {
            "variant": variant.name, "description": variant.description,
            "firing_days": n_fires,
            "firing_per_year": round(firing_rate_per_year, 2),
            "filtered_trades": 0,
            "win_rate": None, "avg_pnl_contract": None,
            "sharpe": None, "sortino": None, "max_loss_rate": None,
            "sharpe_lift_pct": None,
            "passes_all_criteria": False,
            "firing_dates": [d.isoformat() for d, _ in sorted(fires)[:20]],
        }

    stats = calibrate.summary_stats(filtered_trades)
    sharpe_lift = (stats.sharpe - BASELINE_SHARPE) / abs(BASELINE_SHARPE) * 100 if BASELINE_SHARPE != 0 else 0

    passes_all = (
        5 <= firing_rate_per_year <= 20 and
        stats.win_rate >= 0.85 and
        sharpe_lift >= 30 and
        stats.max_loss_rate < BASELINE_MAX_LOSS_RATE
    )

    return {
        "variant": variant.name, "description": variant.description,
        "firing_days": n_fires,
        "firing_per_year": round(firing_rate_per_year, 2),
        "filtered_trades": stats.n,
        "win_rate": round(stats.win_rate, 4),
        "avg_pnl_contract": round(stats.avg_pnl_contract, 2),
        "sharpe": round(stats.sharpe, 3),
        "sortino": round(stats.sortino, 3),
        "max_loss_rate": round(stats.max_loss_rate, 4),
        "sharpe_lift_pct": round(sharpe_lift, 1),
        "passes_all_criteria": bool(passes_all),
        "firing_dates": [d.isoformat() for d, _ in sorted(fires)[:20]],
    }


# ── P2 engine shadow (V3 only, non-authoritative) ──────────────────────────


def _shadow_v3_via_engine(
    signals_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    atr_series: pd.Series,
    *,
    legacy_firing_days: int | None = None,
) -> Any:
    """Shadow V3 through `nfo.studies.variant_comparison` (engine path).

    Non-authoritative: the return value of `evaluate_variant` remains the
    source of truth for the report; this helper exists so the engine path
    runs on every `redesign_variants` invocation and surfaces drift vs
    legacy in the log stream. When `legacy_firing_days` is supplied and the
    engine disagrees, we emit a single warning line. Errors inside the
    engine path are caught and logged (never fatal) so this wiring can't
    break the existing report pipeline.

    Returns `VariantResult` on success, `None` on any exception.
    """
    try:
        from nfo.config import ROOT as _ROOT
        from nfo.specs.loader import load_strategy
        from nfo.studies.variant_comparison import run_variant_comparison_v3

        _spec, _ = load_strategy(_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml")

        def _legacy_event_resolver(entry, dte):
            return "high" if not _event_pass(
                entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"}, window_days=10,
            ) else "none"

        engine_result = run_variant_comparison_v3(
            spec=_spec, features_df=signals_df, atr_series=atr_series,
            trades_df=trades_df, event_resolver=_legacy_event_resolver,
        )
        if legacy_firing_days is not None and engine_result.n_fires != legacy_firing_days:
            log.warning(
                "V3 engine/legacy n_fires drift: engine=%s legacy=%s",
                engine_result.n_fires, legacy_firing_days,
            )
        return engine_result
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("V3 engine shadow failed: %s", exc)
        return None


# ── Report + verdict ────────────────────────────────────────────────────────


def _rank_variants(results: list[dict]) -> list[dict]:
    """Rank by: passes_all_criteria first (True > False), then sharpe desc."""
    def key(r):
        passed = 1 if r.get("passes_all_criteria") else 0
        sharpe = r.get("sharpe") or -999
        return (-passed, -sharpe)
    return sorted(results, key=key)


def _write_report(results: list[dict]) -> str:
    ranked = _rank_variants(results)
    lines = [
        "# Filter redesign — variant comparison",
        "",
        "Baseline (unfiltered 82-trade cost-inclusive set):",
        f"- Sharpe: **{BASELINE_SHARPE}**",
        f"- Win rate: **{BASELINE_WIN*100:.0f}%**",
        f"- Max-loss rate: **{BASELINE_MAX_LOSS_RATE*100:.2f}%**",
        "",
        "## Success criteria (all four must hold):",
        "",
        "1. Firing rate ∈ [5, 20] days/year",
        "2. Win-rate on filtered trades ≥ 85%",
        "3. Sharpe lift ≥ +30% vs baseline",
        "4. Max-loss rate on filtered trades < baseline (7.32%)",
        "",
        "## Variant results (ranked)",
        "",
        "| # | Variant | Fires/yr | Filt Trades | Win% | Sharpe | Δ Sharpe | MaxLoss% | ✓All? |",
        "|---|---|---:|---:|---:|---:|---:|---:|:-:|",
    ]
    for i, r in enumerate(ranked, 1):
        lines.append(
            f"| {i} | **{r['variant']}** | {r['firing_per_year']} | "
            f"{r['filtered_trades'] or 0} | "
            f"{(r['win_rate'] or 0)*100:.0f}% | "
            f"{r['sharpe'] or float('nan'):+.2f} | "
            f"{r['sharpe_lift_pct'] or 0:+.0f}% | "
            f"{(r['max_loss_rate'] or 0)*100:.1f}% | "
            f"{'✅' if r['passes_all_criteria'] else '—'} |"
        )

    lines += ["", "## Per-variant detail", ""]
    for r in ranked:
        lines += [
            f"### {r['variant']} — {r['description']}",
            "",
            f"- Firing days (total): **{r['firing_days']}**  "
            f"(≈ {r['firing_per_year']} / year)",
            f"- Filtered trades: **{r['filtered_trades']}**  "
            f"(of 70 real trades)",
            f"- Win rate: **{(r['win_rate'] or 0)*100:.1f}%**",
            f"- Avg PnL / contract: **₹{r['avg_pnl_contract'] or 0:,.0f}**",
            f"- Sharpe: **{r['sharpe']}** "
            f"(Δ {r['sharpe_lift_pct'] or 0:+.0f}% vs baseline)",
            f"- Sortino: {r['sortino']}",
            f"- Max-loss rate: **{(r['max_loss_rate'] or 0)*100:.2f}%**",
            f"- Passes ALL criteria: **{'YES' if r['passes_all_criteria'] else 'NO'}**",
            "",
            f"First 20 firing dates: {', '.join(r['firing_dates']) or '—'}",
            "",
        ]

    winner = next((r for r in ranked if r["passes_all_criteria"]), None)
    if winner:
        lines += [
            "## 🏆 Winner: **" + winner["variant"] + "**",
            "",
            winner["description"],
            "",
            f"Satisfies all four criteria. Ready to adopt as `regime_watch.py` configuration.",
        ]
    else:
        # Find best on each criterion.
        best_sharpe = max(results, key=lambda r: r["sharpe"] or -999)
        best_win = max(results, key=lambda r: r["win_rate"] or 0)
        lines += [
            "## ⚠️ No variant satisfies all four criteria",
            "",
            f"- Best Sharpe: **{best_sharpe['variant']}** ({best_sharpe['sharpe']})",
            f"- Best win-rate: **{best_win['variant']}** ({(best_win['win_rate'] or 0)*100:.0f}%)",
            "",
            "Trade-offs surfaced — pick a variant based on which criterion matters most, "
            "or iterate on new variants targeting the specific gap.",
        ]

    return "\n".join(lines)


def _legacy_main() -> dict[str, Any]:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not SIGNALS_PATH.exists():
        log.error("Missing %s — run historical_backtest.py first.", SIGNALS_PATH)
        return {
            "metrics": {},
            "body_markdown": "",
            "warnings": [f"Missing {SIGNALS_PATH.name}"],
        }
    if not TRADES_PATH.exists():
        log.error("Missing %s — run backtest grid first.", TRADES_PATH)
        return {
            "metrics": {},
            "body_markdown": "",
            "warnings": [f"Missing {TRADES_PATH.name}"],
        }

    signals_df = pd.read_parquet(SIGNALS_PATH)
    signals_df["date"] = pd.to_datetime(signals_df["date"])
    trades_df = pd.read_csv(TRADES_PATH)

    log.info("Loaded %d signal-days and %d real trades.", len(signals_df), len(trades_df))

    atr_series = _load_nifty_atr(signals_df["date"])
    log.info("Computed ATR-14 over %d days.", atr_series.notna().sum())

    # P2 engine parity path: run V3 through the engine as a shadow evaluation.
    # The authoritative metrics for the report still come from legacy
    # evaluate_variant; engine results are logged for drift monitoring.
    variants = make_variants()
    results: list[dict] = []
    for v in variants:
        log.info("Evaluating %s: %s", v.name, v.description)
        r = evaluate_variant(v, signals_df, trades_df, atr_series)
        results.append(r)
        log.info(
            "  → fires=%d/yr, filt_trades=%d, win=%s, sharpe=%s, max_loss=%s, passes=%s",
            r["firing_per_year"], r["filtered_trades"] or 0,
            f"{(r['win_rate'] or 0)*100:.0f}%",
            r["sharpe"], f"{(r['max_loss_rate'] or 0)*100:.1f}%",
            r["passes_all_criteria"],
        )
        if v.name == "V3":
            _shadow_v3_via_engine(
                signals_df, trades_df, atr_series,
                legacy_firing_days=int(r["firing_days"]),
            )

    pd.DataFrame(results).to_csv(OUT_CSV, index=False)
    report = _write_report(results)
    OUT_MD.write_text(report, encoding="utf-8")
    winner = next((r for r in _rank_variants(results) if r["passes_all_criteria"]), None)
    if winner:
        OUT_WINNER.write_text(json.dumps(winner, indent=2, default=str), encoding="utf-8")
        log.info("🏆 Winner: %s", winner["variant"])
    else:
        log.info("⚠️  No variant satisfies all four criteria.")
    log.info("Wrote %s and %s", OUT_CSV, OUT_MD)
    print()
    print(report)
    metrics: dict[str, Any] = {
        "n_variants": int(len(results)),
        "winner": winner["variant"] if winner else None,
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
        return _legacy_main()

    result = wrap_legacy_run(
        study_type="variant_comparison",
        strategy_path=ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml",
        study_path=ROOT / "configs" / "nfo" / "studies" / "variant_comparison_default.yaml",
        legacy_artifacts=[
            RESULTS_DIR / "redesign_comparison.csv",
            RESULTS_DIR / "redesign_comparison.md",
            RESULTS_DIR / "redesign_winner.json",
        ],
        window=(date(2024, 2, 1), date(2026, 4, 18)),
        run_logic=run_logic,
        runs_root=RESULTS_DIR / "runs",
    )
    print(result.run_dir.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
