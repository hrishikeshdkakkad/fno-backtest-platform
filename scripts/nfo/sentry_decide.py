"""Apply the user-approved decision gate to the expanded NIFTY-only sample.

Gates (per reviewer, 2026-04-21):
  - expanded fire-cycles < ~20-25 → STOP NIFTY-only production path (research-only / likely eliminate)
  - in projected band (roughly 20-30) → continue to rolling walk-forward
  - data quality problems → stop and fix

This script:
  1. Loads the expanded features parquet from results/nfo/audits/expand_history_features.parquet.
  2. Merges with the existing 2024-2026 calibration features (results/nfo/historical_signals.parquet).
  3. Applies scripts/nfo/sentry_2022.v3_fire_mask using the full HARD_EVENTS.
  4. Counts distinct fire-cycles across the union.
  5. Emits a decision report to results/nfo/audits/expansion_decision_YYYY-MM-DD.md.

No canonical artifacts are modified.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from nfo.config import RESULTS_DIR

import sentry_2022 as s  # type: ignore[import-not-found]
import historical_backtest as hb  # type: ignore[import-not-found]

log = logging.getLogger("sentry_decide")


def _load_union() -> pd.DataFrame:
    """Union of expand_history output + existing 2024-2026 features."""
    expanded_path = RESULTS_DIR / "audits" / "expand_history_features.parquet"
    calib_path = RESULTS_DIR / "historical_signals.parquet"
    frames = []
    if expanded_path.exists():
        frames.append(pd.read_parquet(expanded_path))
    if calib_path.exists():
        frames.append(pd.read_parquet(calib_path))
    if not frames:
        raise FileNotFoundError(
            "Neither expanded nor calibration features found; run expand_history.py first."
        )
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


def apply_gate(n_cycles: int) -> tuple[str, str]:
    """Return (verdict, description) per the user's three-gate framework."""
    if n_cycles < 20:
        return (
            "STOP",
            "NIFTY-only production path is effectively closed. "
            "Research-only / likely eliminate for production use. "
            "No more time on robustness theater.",
        )
    if n_cycles <= 30:
        return (
            "CONTINUE_TO_WALKFORWARD",
            "In the projected band. Proceed to PR3 (rolling walk-forward). "
            "Use it to try to eliminate the strategy, not to promote it. "
            "Production shadow remains off the table.",
        )
    return (
        "REEVALUATE",
        "Sample richer than projected. Worth re-reading the original kill plan "
        "to see if any gates should tighten before proceeding.",
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    df = _load_union()
    log.info("Union frame: %d rows, %s → %s", len(df),
             df['date'].min().date(), df['date'].max().date())

    fires = s.v3_fire_mask(df)
    n_fire_days = int(fires.sum())
    n_cycles = s.count_fire_cycles(df, fires)

    years = (df['date'].max() - df['date'].min()).days / 365.25

    verdict, reason = apply_gate(n_cycles)

    fire_rows = df.loc[fires.values]
    cycles_per_expiry = (
        fire_rows.groupby("target_expiry")["date"]
        .agg(["count", "min", "max"])
        .reset_index()
        .rename(columns={"count": "fire_days", "min": "first_fire", "max": "last_fire"})
        .sort_values("target_expiry")
    )

    # Sub-window breakdowns
    def _bucket_cycles(start: str, end: str) -> int:
        mask = (df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))
        sub = df[mask].reset_index(drop=True)
        if sub.empty:
            return 0
        sub_fires = s.v3_fire_mask(sub)
        return s.count_fire_cycles(sub, sub_fires)

    buckets = {
        "2020-08 → 2021-12": _bucket_cycles("2020-08-01", "2021-12-31"),
        "2022-01 → 2022-12": _bucket_cycles("2022-01-01", "2022-12-31"),
        "2023-01 → 2023-12": _bucket_cycles("2023-01-01", "2023-12-31"),
        "2024-01 → 2026-04": _bucket_cycles("2024-01-01", df["date"].max().isoformat()[:10]),
    }

    today = date.today().isoformat()
    report_lines = [
        "# Expansion Decision Report",
        "",
        f"**Generated:** {today}",
        f"**Union window:** {df['date'].min().date()} → {df['date'].max().date()} ({years:.2f} years)",
        f"**Trading days:** {len(df)}",
        f"**HARD_EVENTS loaded:** {len(hb.HARD_EVENTS)} entries",
        "",
        "## Headline",
        "",
        f"- V3 fire-days (full union): **{n_fire_days}**",
        f"- V3 fire-**cycles** (distinct expiries): **{n_cycles}**",
        f"- Fire-cycles per year: **{n_cycles / years:.2f}**",
        "",
        f"**Verdict: `{verdict}`**",
        "",
        f"> {reason}",
        "",
        "## Sub-window fire-cycle counts",
        "",
        "| Sub-window | Fire-cycles |",
        "|---|---:|",
    ]
    for k, v in buckets.items():
        report_lines.append(f"| {k} | {v} |")

    report_lines += [
        "",
        "## Fire-cycles detail",
        "",
        "| Target expiry | Fire-days | First fire | Last fire |",
        "|---|---:|---|---|",
    ]
    for _, r in cycles_per_expiry.iterrows():
        expiry = r["target_expiry"]
        report_lines.append(
            f"| {expiry} | {int(r['fire_days'])} | {r['first_fire'].date()} | {r['last_fire'].date()} |"
        )

    out_path = RESULTS_DIR / "audits" / f"expansion_decision_{today}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(report_lines), encoding="utf-8")

    print("\n".join(report_lines))
    log.info("Wrote %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
