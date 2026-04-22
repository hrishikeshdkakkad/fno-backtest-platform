"""PR3: rolling walk-forward evaluation of V3 over the expanded NIFTY sample.

Strategy: V3 is frozen (v3_frozen.yaml). There is no per-window retuning.
The "train" window here is warmup context only — the test window is where
fire-cycles are counted and trades simulated. Per-window reporting is the
point; there is deliberately no aggregate Sharpe headline.

Scope (per user-approved PR3 design, 2026-04-21):
  - 24m train / 12m test, rolling quarterly.
  - Per-window report: fire-cycles, trades, per-contract P&L, lot-aware P&L
    (using nfo.universe.lot_size_on with the trade's entry date), win rate,
    max drawdown of the cumulative per-contract curve within the window.
  - V3-specific event gate (``scripts/nfo/sentry_2022.v3_fire_mask``), not the
    generic parquet s8_event.
  - Explicit kill rules evaluated up front and stated in the report.

Outputs land under ``results/nfo/audits/`` — canonical artifacts untouched.

Usage:
  .venv/bin/python scripts/nfo/walkforward_v3.py [--verbose] [--strict-dhan]

``--strict-dhan`` fails if any fire-cycle can't be simulated via Dhan; the
default is to skip un-simulatable cycles with a logged warning (for fresh
checkouts where the rolling cache doesn't cover every needed strike).
"""
from __future__ import annotations

import argparse
import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from nfo import calendar_nfo, universe
from nfo.client import DhanClient
from nfo.config import RESULTS_DIR
from nfo.engine.execution import run_cycle_from_dhan
from nfo.specs.loader import load_strategy
from nfo.universe import lot_size_on

import historical_backtest as hb  # type: ignore[import-not-found]
import sentry_2022 as s2  # type: ignore[import-not-found]

log = logging.getLogger("walkforward_v3")

REPO_ROOT = Path(__file__).resolve().parents[2]
V3_STRATEGY_PATH = REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml"

# Kill-rule thresholds (PR3 spec).
MIN_TRADES_PER_WINDOW = 3        # windows with <3 trades are "thin"
MAX_THIN_WINDOWS = 1             # 2+ thin windows triggers kill
SINGLE_WINDOW_DOMINANCE = 0.5    # >=50% of total P&L from one window → burst-dominated


@dataclass(frozen=True, slots=True)
class Window:
    train_start: date
    train_end: date
    test_start: date
    test_end: date


@dataclass
class WindowResult:
    window: Window
    fire_cycles: int
    trades: int
    wins: int
    per_contract_pnl: float
    lot_aware_pnl: float
    win_rate: float
    max_drawdown: float
    simulated_trades: list[dict] = field(default_factory=list)
    skipped_cycles: list[str] = field(default_factory=list)


def _add_months(d: date, months: int) -> date:
    """Add ``months`` to a date, clamping the day to the resulting month's length."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    day = min(d.day, last_day)
    return date(year, month, day)


def generate_windows(
    *,
    data_start: date,
    data_end: date,
    train_months: int = 24,
    test_months: int = 12,
    step_months: int = 3,
) -> list[Window]:
    """Rolling windows with a fixed train length feeding each test."""
    windows: list[Window] = []
    train_start = data_start
    while True:
        train_end = _add_months(train_start, train_months)
        test_start = train_end
        test_end = _add_months(test_start, test_months)
        if test_end > data_end:
            break
        windows.append(Window(
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
        ))
        train_start = _add_months(train_start, step_months)
    return windows


def _load_union_features() -> pd.DataFrame:
    expanded_path = RESULTS_DIR / "audits" / "expand_history_features.parquet"
    calib_path = RESULTS_DIR / "historical_signals.parquet"
    frames = []
    if expanded_path.exists():
        frames.append(pd.read_parquet(expanded_path))
    if calib_path.exists():
        frames.append(pd.read_parquet(calib_path))
    if not frames:
        raise FileNotFoundError(
            "No features found — run scripts/nfo/expand_history.py first."
        )
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


def _simulate_cycle(
    client: DhanClient,
    strategy_spec,
    under,
    entry_date: date,
    expiry_date: date,
    spot_daily: pd.DataFrame,
) -> dict | None:
    """Run one cycle through the canonical engine; return a trade-row dict or None."""
    try:
        sim = run_cycle_from_dhan(
            client=client,
            under=under,
            strategy_spec=strategy_spec,
            entry_date=entry_date,
            expiry_date=expiry_date,
            spot_daily=spot_daily,
        )
    except Exception as exc:
        log.warning("simulate %s→%s failed: %s", entry_date, expiry_date, exc)
        return None
    if sim is None:
        return None
    st = sim.spread_trade
    lot_size_at_entry = lot_size_on("NIFTY", entry_date)
    return {
        "cycle_id": sim.cycle_id,
        "trade_id": sim.trade_id,
        "entry_date": st.entry_date,
        "expiry_date": st.expiry_date,
        "exit_date": st.exit_date,
        "outcome": st.outcome,
        "short_strike": st.short_strike,
        "long_strike": st.long_strike,
        "net_credit": st.net_credit,
        "pnl_per_share": st.pnl_per_share,
        "pnl_contract": st.pnl_contract,
        "gross_pnl_contract": st.gross_pnl_contract,
        "txn_cost_contract": st.txn_cost_contract,
        "lot_size_at_entry": lot_size_at_entry,
        "lot_aware_pnl": float(st.pnl_per_share) * lot_size_at_entry,
    }


def _first_fire_date_per_cycle(
    features: pd.DataFrame,
    fire_mask: pd.Series,
) -> dict[str, date]:
    """Map target_expiry → earliest fire-day (canonical_trade_chooser=first_fire)."""
    fires = features.loc[fire_mask.values].copy()
    fires = fires.sort_values("date")
    out: dict[str, date] = {}
    for _, row in fires.iterrows():
        expiry = row.get("target_expiry")
        if pd.isna(expiry):
            continue
        if expiry not in out:
            out[expiry] = row["date"].date() if hasattr(row["date"], "date") else row["date"]
    return out


def evaluate_window(
    window: Window,
    features: pd.DataFrame,
    spot_daily: pd.DataFrame,
    client: DhanClient,
    strategy_spec,
    under,
) -> WindowResult:
    mask = (features["date"] >= pd.Timestamp(window.test_start)) & \
           (features["date"] < pd.Timestamp(window.test_end))
    sub = features.loc[mask].reset_index(drop=True)
    fires = s2.v3_fire_mask(sub)
    n_cycles = s2.count_fire_cycles(sub, fires)

    fire_cycles_map = _first_fire_date_per_cycle(sub, fires)
    simulated: list[dict] = []
    skipped: list[str] = []

    for expiry_iso, entry_d in sorted(fire_cycles_map.items()):
        try:
            expiry_d = date.fromisoformat(str(expiry_iso)[:10])
        except ValueError:
            skipped.append(str(expiry_iso))
            continue
        trade = _simulate_cycle(client, strategy_spec, under, entry_d, expiry_d, spot_daily)
        if trade is None:
            skipped.append(f"{entry_d}→{expiry_d}")
            continue
        simulated.append(trade)

    trades_df = pd.DataFrame(simulated) if simulated else pd.DataFrame()
    if trades_df.empty:
        win_rate = 0.0
        per_contract = 0.0
        lot_aware = 0.0
        wins = 0
        max_dd = 0.0
    else:
        wins = int((trades_df["pnl_contract"] > 0).sum())
        win_rate = wins / len(trades_df)
        per_contract = float(trades_df["pnl_contract"].sum())
        lot_aware = float(trades_df["lot_aware_pnl"].sum())
        # Max drawdown on the cumulative per-contract curve (ordered by entry_date).
        curve = trades_df.sort_values("entry_date")["pnl_contract"].cumsum()
        running_max = curve.cummax()
        max_dd = float((curve - running_max).min())

    return WindowResult(
        window=window,
        fire_cycles=n_cycles,
        trades=len(simulated),
        wins=wins,
        per_contract_pnl=per_contract,
        lot_aware_pnl=lot_aware,
        win_rate=win_rate,
        max_drawdown=max_dd,
        simulated_trades=simulated,
        skipped_cycles=skipped,
    )


def apply_kill_rules(rows: list[dict | WindowResult]) -> tuple[bool, list[str]]:
    """Per PR3 design:
      1. any window with 0 fire-cycles → kill for production
      2. median per-window OOS per-contract P&L ≤ 0 → kill
      3. ≥2 windows with <3 test trades → kill for production
      4. ≥50% of total P&L from a single window (burst-dominated) → kill
      5. empty row set (no evaluable windows) → kill
    """
    reasons: list[str] = []
    if not rows:
        reasons.append("no evaluable windows — cannot assess robustness")
        return True, reasons

    def _get(r, key, default=None):
        if isinstance(r, dict):
            return r.get(key, default)
        return getattr(r, key, default)

    zero_fire = [i for i, r in enumerate(rows) if _get(r, "fire_cycles", 0) == 0]
    if zero_fire:
        reasons.append(f"{len(zero_fire)} window(s) with 0 fire-cycles; production unviable")

    pnls = [float(_get(r, "per_contract_pnl", 0.0)) for r in rows]
    median_pnl = statistics.median(pnls)
    if median_pnl <= 0:
        reasons.append(f"median per-window OOS per-contract P&L = {median_pnl:.1f} ≤ 0")

    thin = [i for i, r in enumerate(rows) if _get(r, "trades", 0) < MIN_TRADES_PER_WINDOW]
    if len(thin) > MAX_THIN_WINDOWS:
        reasons.append(
            f"{len(thin)} window(s) with < {MIN_TRADES_PER_WINDOW} test trades — thin; "
            "production claims undefensible"
        )

    total_pnl = sum(pnls)
    if abs(total_pnl) > 1e-9:
        max_contrib = max(pnls, key=abs)
        share = abs(max_contrib) / abs(total_pnl) if abs(total_pnl) > 0 else 0
        if share >= SINGLE_WINDOW_DOMINANCE:
            reasons.append(
                f"single window contributes {share:.0%} of total P&L (burst-dominated)"
            )

    return (len(reasons) > 0), reasons


def _format_report(rows: list[WindowResult], killed: bool, reasons: list[str]) -> str:
    lines = [
        "# PR3 — V3 Rolling Walk-Forward",
        "",
        f"**Generated:** {date.today().isoformat()}",
        "**Design:** 24m train / 12m test, rolling quarterly. V3 frozen; train is warmup context.",
        "**Strategy:** `configs/nfo/strategies/v3_frozen.yaml`",
        "**Event gate:** V3-specific (`{RBI, FOMC, BUDGET}` ∩ 10-day window); CPI demoted.",
        "**Lot sizing:** `nfo.universe.lot_size_on(name, entry_date)` per-trade dated lookup.",
        "**Output path:** `results/nfo/audits/` — canonical artifacts untouched.",
        "",
        "## Kill verdict",
        "",
        f"**{'❌ KILL' if killed else '✅ SURVIVE'}**",
        "",
    ]
    if reasons:
        lines.append("Reasons:")
        for r in reasons:
            lines.append(f"- {r}")
    else:
        lines.append("No kill rule triggered; V3 passes PR3 walk-forward.")

    lines += [
        "",
        "## Per-window results",
        "",
        "| Test window | Fire-cycles | Trades | Wins | Win rate | Per-contract P&L | Lot-aware P&L | Max DD | Skipped |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        w = r.window
        lines.append(
            f"| {w.test_start} → {w.test_end} "
            f"| {r.fire_cycles} "
            f"| {r.trades} "
            f"| {r.wins} "
            f"| {r.win_rate:.1%} "
            f"| ₹{r.per_contract_pnl:,.0f} "
            f"| ₹{r.lot_aware_pnl:,.0f} "
            f"| ₹{r.max_drawdown:,.0f} "
            f"| {len(r.skipped_cycles)} |"
        )

    lines += ["", "## Per-trade detail", ""]
    for r in rows:
        if not r.simulated_trades:
            continue
        lines.append(f"### {r.window.test_start} → {r.window.test_end}")
        lines.append("")
        lines.append("| Entry | Expiry | Exit | Outcome | Short | Long | Lot | per-contract P&L | Lot-aware P&L |")
        lines.append("|---|---|---|---|---:|---:|---:|---:|---:|")
        for t in r.simulated_trades:
            lines.append(
                f"| {t['entry_date']} | {t['expiry_date']} | {t['exit_date']} | {t['outcome']} "
                f"| {int(t['short_strike'])} | {int(t['long_strike'])} "
                f"| {t['lot_size_at_entry']} "
                f"| ₹{t['pnl_contract']:,.0f} "
                f"| ₹{t['lot_aware_pnl']:,.0f} |"
            )
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--strict-dhan", action="store_true",
                   help="Fail if any fire-cycle can't be simulated")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    features = _load_union_features()
    log.info("Union features: %d rows (%s → %s)",
             len(features), features["date"].min().date(), features["date"].max().date())

    windows = generate_windows(
        data_start=features["date"].min().date(),
        data_end=features["date"].max().date(),
    )
    log.info("Generated %d rolling windows", len(windows))

    spot_daily = hb._load_nifty_daily()
    strategy_spec, _ = load_strategy(V3_STRATEGY_PATH)
    under = universe.get("NIFTY")

    with DhanClient() as client:
        results: list[WindowResult] = []
        for i, w in enumerate(windows):
            log.info("Evaluating window %d/%d: test %s → %s",
                     i + 1, len(windows), w.test_start, w.test_end)
            r = evaluate_window(w, features, spot_daily, client, strategy_spec, under)
            if args.strict_dhan and r.skipped_cycles:
                raise RuntimeError(
                    f"window {w.test_start} had {len(r.skipped_cycles)} skipped cycles"
                )
            results.append(r)

    killed, reasons = apply_kill_rules(results)

    out_dir = RESULTS_DIR / "audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"walkforward_v3_{date.today().isoformat()}.md"
    report = _format_report(results, killed, reasons)
    report_path.write_text(report, encoding="utf-8")
    print(report)
    log.info("Wrote %s", report_path)
    return 1 if killed else 0


if __name__ == "__main__":
    raise SystemExit(main())
