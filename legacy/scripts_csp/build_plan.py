"""Generate the final CSP income plan from backtest results.

Reads results/summary.csv + results/trades.csv, picks the best allocation
for a $41,000 capital budget targeting $500/month, writes results/plan.md.
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from csp.config import RESULTS_DIR
from csp.portfolio import best_allocation, cards_from_summary

console = Console()

CAPITAL = 41_000.0
TARGET_MONTHLY = 500.0


def main() -> None:
    summary_path = RESULTS_DIR / "summary.csv"
    trades_path = RESULTS_DIR / "trades.csv"
    if not summary_path.exists():
        console.print("[red]results/summary.csv not found — run a backtest first[/]")
        return
    summary = pd.read_csv(summary_path)
    trades = pd.read_csv(trades_path) if trades_path.exists() else pd.DataFrame()

    cards = cards_from_summary(summary)
    alloc = best_allocation(cards, CAPITAL, TARGET_MONTHLY)
    if not alloc:
        console.print("[red]no feasible allocation[/]")
        return

    console.print(f"[bold]Capital:[/] ${CAPITAL:,.0f}  [bold]Target:[/] ${TARGET_MONTHLY:.0f}/mo")
    console.print(
        f"Expected monthly P/L: ${alloc['expected_monthly']:.0f}  "
        f"(target hit: {alloc['hits_target']})"
    )
    console.print(
        f"Worst single-month P/L (sum): ${alloc['worst_single_month']:.0f}"
    )
    console.print(
        f"Collateral used: ${alloc['collateral']:,.0f}  "
        f"Cash reserve: ${alloc['cash_reserve']:,.0f}"
    )

    t = Table(title="Allocation")
    for col in [
        "underlying", "contracts", "delta", "dte", "pt", "manage@",
        "per-contract $/mo", "collateral", "win rate", "assign rate",
    ]:
        t.add_column(col)
    for pos in alloc["positions"]:
        c = pos["card"]
        t.add_row(
            c.underlying,
            str(pos["n"]),
            f"{c.target_delta:.2f}",
            str(c.target_dte),
            f"{c.profit_take:.2f}",
            str(c.manage_at_dte) if c.manage_at_dte is not None else "—",
            f"${c.avg_monthly_pnl:.0f}",
            f"${c.collateral:,.0f}",
            f"{c.win_rate:.0%}",
            f"{c.assignment_rate:.0%}",
        )
    console.print(t)

    _write_plan_markdown(alloc, summary, trades)


def _equity_curve_ascii(trades: pd.DataFrame, width: int = 60, height: int = 12) -> str:
    """Simple ASCII sparkline of cumulative P/L over trade sequence."""
    if trades.empty:
        return ""
    cum = np.cumsum(trades["pnl_dollars"].values)
    if len(cum) < 2:
        return ""
    # Downsample to `width` points
    if len(cum) > width:
        idx = np.linspace(0, len(cum) - 1, width).astype(int)
        points = cum[idx]
    else:
        points = cum
    lo, hi = float(points.min()), float(points.max())
    if hi == lo:
        hi = lo + 1.0
    rows: list[list[str]] = [[" "] * len(points) for _ in range(height)]
    for x, v in enumerate(points):
        y = int((v - lo) / (hi - lo) * (height - 1))
        y = (height - 1) - y  # invert for top-down rows
        rows[y][x] = "*"
    lines = ["".join(r) for r in rows]
    return (
        f"  equity curve (width={width} pts, range ${lo:,.0f} to ${hi:,.0f}):\n"
        + "\n".join("    " + l for l in lines)
    )


def _portfolio_simulation(alloc: dict, trades: pd.DataFrame) -> dict:
    """Simulate the actual portfolio by summing monthly P/L across positions.

    Assumes each contract is written every monthly cycle (aligned on the 3rd-Friday
    expiry). We match trades on (underlying, cycle, delta, dte, pt, manage).
    """
    if trades.empty or not alloc.get("positions"):
        return {}
    # tag each position with its count
    per_pos_pnls: list[pd.Series] = []
    for pos in alloc["positions"]:
        c = pos["card"]
        n = pos["n"]
        sel = trades[
            (trades["underlying"] == c.underlying)
            & (trades["param_delta"] == c.target_delta)
            & (trades["param_dte"] == c.target_dte)
            & (trades["param_pt"] == c.profit_take)
            & (
                (trades["param_manage"].isna() & (c.manage_at_dte is None))
                | (trades["param_manage"] == (c.manage_at_dte if c.manage_at_dte is not None else -1))
            )
        ].copy()
        if sel.empty:
            continue
        sel = sel.sort_values("expiry")
        per_pos_pnls.append(
            sel.set_index("expiry")["pnl_dollars"].mul(n).rename(c.key)
        )
    if not per_pos_pnls:
        return {}
    combined = pd.concat(per_pos_pnls, axis=1).fillna(0.0)
    monthly = combined.sum(axis=1)
    cum = monthly.cumsum()
    return {
        "monthly_pnl": monthly,
        "cumulative_pnl": cum,
        "avg_monthly": float(monthly.mean()),
        "median_monthly": float(monthly.median()),
        "std_monthly": float(monthly.std()),
        "min_monthly": float(monthly.min()),
        "max_monthly": float(monthly.max()),
        "hit_rate_500": float((monthly >= 500).mean()),
        "hit_rate_zero": float((monthly > 0).mean()),
        "max_drawdown": float((cum - cum.cummax()).min()),
    }


def _write_plan_markdown(alloc: dict, summary: pd.DataFrame, trades: pd.DataFrame) -> None:
    p = RESULTS_DIR / "plan.md"
    pos = alloc["positions"]
    sim = _portfolio_simulation(alloc, trades)
    lines: list[str] = []

    lines.append(f"# CSP Income Plan — ${CAPITAL:,.0f} → target ${TARGET_MONTHLY:.0f}/mo\n")

    lines.append("## Executive summary\n")
    lines.append(f"- Capital: **${CAPITAL:,.0f}**")
    lines.append(
        f"- Monthly target: **${TARGET_MONTHLY:.0f}** "
        f"(~{TARGET_MONTHLY * 12 / CAPITAL:.1%} annual on capital)"
    )
    lines.append(f"- Backtest window: 2024-04-17 to 2026-04-17 (~24 monthly cycles)")
    lines.append(
        f"- **Expected monthly P/L:** ${alloc['expected_monthly']:.0f} "
        f"→ {'HITS' if alloc['hits_target'] else 'UNDERSHOOTS'} target"
    )
    lines.append(
        f"- **Collateral used:** ${alloc['collateral']:,.0f}  "
        f"(cash reserve ${alloc['cash_reserve']:,.0f})"
    )
    if sim:
        lines.append(
            f"- **Simulated portfolio over backtest:** avg ${sim['avg_monthly']:.0f}/mo, "
            f"median ${sim['median_monthly']:.0f}/mo, "
            f"worst ${sim['min_monthly']:.0f}, "
            f"best ${sim['max_monthly']:.0f}"
        )
        lines.append(
            f"- **Max drawdown:** ${sim['max_drawdown']:.0f}  "
            f"Months ≥ $0: {sim['hit_rate_zero']:.0%}  "
            f"Months ≥ $500: {sim['hit_rate_500']:.0%}"
        )
    lines.append("")

    lines.append("## Positions to open each monthly cycle\n")
    lines.append(
        "| Underlying | N | Δ | DTE | Profit take | Manage @ DTE | "
        "Collateral | $/contract/mo (backtest) | Win rate | Assign rate |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for ppos in pos:
        c = ppos["card"]
        lines.append(
            f"| {c.underlying} | {ppos['n']} | {c.target_delta:.2f} | {c.target_dte} | "
            f"{c.profit_take:.2f} | {c.manage_at_dte if c.manage_at_dte is not None else '—'} | "
            f"${c.collateral * ppos['n']:,.0f} | ${c.avg_monthly_pnl:.0f} | "
            f"{c.win_rate:.0%} | {c.assignment_rate:.0%} |"
        )
    lines.append("")

    lines.append("## Execution playbook\n")
    lines.append("### Entry\n")
    lines.append("1. **Entry day:** the trading day closest to 35 calendar days before the next 3rd-Friday monthly expiry.")
    lines.append("2. **Strike selection:** pick the put whose BSM delta at that day's close is closest to the target delta in the allocation table.")
    lines.append("3. **Order type:** limit order at the bid+0.05 or mid-5%, good-'til-cancelled. Avoid market orders on options.")
    lines.append("4. **Cash requirement:** strike × 100 per contract; ensure it is held in cash (not on margin).")
    lines.append("")
    lines.append("### Management\n")
    lines.append("- **Profit take:** close the short put when you can buy it back for 50% of entry credit (standard tastyworks-style management).")
    lines.append("- **Time management:** if still open at 21 DTE, close regardless of P/L to avoid gamma risk. Re-enter next cycle.")
    lines.append("- **Rolling:** on an adverse move (underlying breaks below strike by >1%), you may roll down-and-out to a further-dated put at a lower strike for even or net credit. Do not roll for a debit.")
    lines.append("- **Assignment:** on assignment, accept delivery and either (a) sell shares at next open or (b) run the 'wheel' by writing a covered call ~30 DTE near the current price. The backtest assumes (a).")
    lines.append("")
    lines.append("### Guardrails\n")
    lines.append("- **Earnings:** this portfolio is ETF-only; not applicable unless you add single names.")
    lines.append("- **VIX filter:** if VIX > 30 at entry, halve contract count; if VIX > 40, skip the cycle.")
    lines.append("- **Macro filter:** avoid entering a new cycle in the week of an FOMC meeting or CPI release if VIX > 25.")
    lines.append("- **Liquidity:** only write contracts with open interest > 500 and bid-ask spread < 5% of mid.")
    lines.append("- **Re-evaluate:** re-run the backtest every 6 months with fresh data.")
    lines.append("")

    lines.append("## What the backtest shows (per-config)\n")
    lines.append(summary.sort_values("avg_monthly_pnl", ascending=False).to_markdown(index=False, floatfmt=".3f"))
    lines.append("")

    if sim and len(sim.get("cumulative_pnl", pd.Series())) > 0:
        lines.append("## Simulated equity curve\n")
        lines.append("```")
        lines.append(_equity_curve_ascii(trades.assign(pnl_dollars=sim["monthly_pnl"].reset_index(drop=True).values if False else trades["pnl_dollars"])))
        lines.append("```")
        lines.append("")

    lines.append(dedent(
        f"""
        ## Caveats

        - Fills are modeled at daily close plus/minus 2% slippage (Basic tier gives no NBBO quotes).
          Live fills at mid-of-spread on liquid ETFs should be *tighter* than this, so real P/L is
          likely somewhat better — but the backtest is intentionally conservative.
        - Assignment P/L assumes the stock is sold at the expiry close. Running the full wheel
          (covered calls after assignment) typically recovers some of the assignment loss but is
          not modeled here.
        - Historical greeks are not provided by Massive Basic; delta is computed from BSM on the
          put's closing mid/close. On very short DTE or deep ITM contracts BSM deviates somewhat
          from market-implied greeks, but for 20-30 delta, 35 DTE puts on liquid ETFs the error
          is in tenths of a delta at worst.
        - Two-year backtest covers only one regime (a strong bull market 2024-2026). A longer
          window or a stress scenario (e.g., 2022 bear market) would give a more complete picture
          of tail risk. Consider upgrading to Options Developer ($79/mo) for 4 years of history.
        """
    ).strip() + "\n")

    p.write_text("\n".join(lines))
    console.print(f"\n[bold green]Plan written to {p}[/]")


if __name__ == "__main__":
    main()
