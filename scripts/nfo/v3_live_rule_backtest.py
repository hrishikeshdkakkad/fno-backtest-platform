"""V3 live-rule backtest — thin wrapper over `nfo.studies.live_replay`.

Why this exists
---------------
A **live rule** can only enter on or after the V3 firing date; the canonical
35-DTE-grid entry used by the cycle-matched backtest can predate V3's first
fire, which is a look-ahead bias. This script forces entry to the first-fire
session (snapped forward to the next NSE trading day when needed) and runs
both the pt50 and hte exit variants end-to-end.

After P5-A1, the body delegates the full engine cycle to
`nfo.studies.live_replay.run_live_replay`, which composes triggers → cycle
grouping → live-rule selection → per-cycle simulation. The only remaining
script-local logic is (a) loading signals + ATR, (b) mapping the two
variants to their strategy YAMLs, (c) writing the legacy CSV/MD outputs
with the same column schema.

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
from pathlib import Path
from typing import Any

import pandas as pd

from nfo import calibrate
from nfo.client import DhanClient
from nfo.config import DATA_DIR, RESULTS_DIR, ROOT
from nfo.data import load_underlying_daily
from nfo.specs.loader import load_strategy
from nfo.specs.study import DatasetRef as _DatasetRef
from nfo.studies.live_replay import run_live_replay
from nfo.universe import get as get_under

log = logging.getLogger("v3_live_rule")

_HERE = Path(__file__).resolve().parent

# P6: dataset references for drift detection.

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

# (variant_name, strategy_yaml_filename) — P5-A1 delegates each variant to
# `run_live_replay` with its own spec; the pt50 spec mirrors v3_live_rule.yaml
# but swaps exit_rule to variant=pt50 / profit_take=0.5 / manage_at_dte=21.
VARIANTS: tuple[tuple[str, str], ...] = (
    ("pt50", "v3_live_rule_pt50.yaml"),
    ("hte", "v3_live_rule.yaml"),
)

CAPITAL = 10_00_000


def _load_rv_module(alias: str):
    """Import `scripts/nfo/redesign_variants.py` directly (scripts/ isn't a
    package). Used for the legacy event-resolver + cached-parquet ATR loader.
    """
    spec = importlib.util.spec_from_file_location(alias, _HERE / "redesign_variants.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


def _legacy_main() -> dict[str, Any]:
    """P5-A1: thin wrapper over `nfo.studies.live_replay.run_live_replay`.

    Loads signals + ATR, resolves the pt50 + hte live-rule specs, runs the
    study for each variant, writes the legacy CSVs + MD report, and returns
    the `wrap_legacy_run` contract dict.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    signals_df = pd.read_parquet(RESULTS_DIR / "historical_signals.parquet")
    signals_df["date"] = pd.to_datetime(signals_df["date"])

    rv = _load_rv_module("_legacy_rv_v3lrb")
    atr_series = rv.load_nifty_atr(signals_df["date"])

    def _event_resolver(entry, dte):
        return "high" if not rv._event_pass(
            entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"}, window_days=10,
        ) else "none"

    under = get_under("NIFTY")
    per_variant_rows: dict[str, pd.DataFrame] = {}
    with DhanClient() as client:
        spot_daily = load_underlying_daily(
            client, under, from_date="2023-12-15", to_date="2026-04-18",
        )
        for variant_name, yaml_name in VARIANTS:
            spec, _ = load_strategy(ROOT / "configs" / "nfo" / "strategies" / yaml_name)
            log.info("=== variant %s (spec %s @ %s) ===",
                     variant_name, spec.strategy_id, spec.strategy_version)
            result = run_live_replay(
                spec=spec, features_df=signals_df, atr_series=atr_series,
                spot_daily=spot_daily, client=client, under=under,
                event_resolver=_event_resolver,
            )
            df = result.selected_trades.copy()
            if not df.empty:
                df["v3_first_fire"] = df["first_fire_date"]
                df["variant"] = variant_name
                df["param_delta"] = spec.universe.delta_target
                df["param_width"] = spec.universe.width_value
                df["param_pt"] = spec.exit_rule.profit_take_fraction or 1.0
                df["param_manage"] = (
                    spec.exit_rule.manage_at_dte if spec.exit_rule.manage_at_dte is not None else 0
                )
            df.to_csv(RESULTS_DIR / f"v3_live_trades_{variant_name}.csv", index=False)
            per_variant_rows[variant_name] = df

    start = signals_df["date"].min().date()
    end = signals_df["date"].max().date()
    years = (end - start).days / 365.25
    report_lines: list[str] = [
        "# V3 live-rule backtest",
        "",
        "Entry date is forced to the first V3 firing session (or the next NSE trading",
        "day if the fire lands off-session).",
        "",
        f"Window: {start.isoformat()} → {end.isoformat()} ({years:.2f} years).",
        "Capital: ₹10L per cycle.",
        "",
    ]
    for variant_name, _ in VARIANTS:
        df = per_variant_rows[variant_name]
        if df.empty:
            continue
        stats = calibrate.summary_stats(df)
        report_lines.extend([
            f"## {variant_name.upper()}",
            "",
            f"- Trades: **{stats.n}**",
            f"- Win rate: **{stats.win_rate*100:.0f}%**",
            f"- Sharpe: **{stats.sharpe:+.2f}**",
            f"- Total P&L: **₹{df['pnl_contract'].sum():,.0f}**",
            "",
        ])
    (RESULTS_DIR / "v3_live_report.md").write_text("\n".join(report_lines), encoding="utf-8")

    metrics: dict[str, Any] = {}
    for variant_name, _ in VARIANTS:
        df = per_variant_rows.get(variant_name)
        metrics[f"{variant_name}_trades"] = int(len(df)) if df is not None else 0
        metrics[f"{variant_name}_total_pnl"] = (
            float(df["pnl_contract"].sum()) if df is not None and not df.empty else 0.0
        )
    return {
        "metrics": metrics,
        "body_markdown": (
            "See `tables/` for full outputs. Legacy artifacts mirrored from "
            "`results/nfo/`.\n"
        ),
        "warnings": [],
    }


def main(argv: list[str] | None = None) -> int:  # noqa: ARG001 (argv reserved for CLI parity with other scripts)
    from datetime import date
    from nfo.reporting.wrap_legacy_run import wrap_legacy_run

    # _legacy_main generates both PT50 and HTE from two different strategy YAMLs
    # (v3_live_rule_pt50.yaml @ 3.0.2 and v3_live_rule.yaml @ 3.0.1). Each variant
    # must live in its own run directory under the matching spec so the run's
    # manifest + methodology header + drift detection describe exactly the spec
    # that produced the tables. We run the legacy body once, then wrap twice.
    legacy_result: dict = {}

    def run_logic_once() -> dict:
        if not legacy_result:
            legacy_result.update(_legacy_main())
        return {
            "metrics": legacy_result.get("metrics", {}),
            "body_markdown": legacy_result.get("body_markdown", ""),
            "warnings": legacy_result.get("warnings", []),
        }

    per_variant = [
        ("pt50", "v3_live_rule_pt50.yaml", "v3_live_trades_pt50.csv"),
        ("hte",  "v3_live_rule.yaml",      "v3_live_trades_hte.csv"),
    ]
    last_run_path = None
    for variant, yaml_name, csv_name in per_variant:
        result = wrap_legacy_run(
            study_type="live_replay",
            strategy_path=ROOT / "configs" / "nfo" / "strategies" / yaml_name,
            study_path=ROOT / "configs" / "nfo" / "studies" / "live_replay_default.yaml",
            legacy_artifacts=[RESULTS_DIR / csv_name],
            window=(date(2024, 2, 1), date(2026, 4, 18)),
            run_logic=run_logic_once,
            runs_root=RESULTS_DIR / "runs",
            dataset_refs=_DATASET_REFS,
        )
        last_run_path = result.run_dir.path
        print(result.run_dir.path)
    return 0 if last_run_path is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
