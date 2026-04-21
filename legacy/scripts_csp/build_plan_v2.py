"""Generate plan v2: stacked portfolio combining CSPs + put credit spreads
against the $41k / $1,000-per-month target."""
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
TARGET_MONTHLY = 1_000.0
# Max tolerable single-month portfolio loss, as fraction of capital
MAX_DD_FRAC = 0.15


def main() -> None:
    csp_path = RESULTS_DIR / "summary.csv"
    spread_path = RESULTS_DIR / "spread_summary.csv"

    cards: list = []
    if csp_path.exists():
        cards.extend(cards_from_summary(pd.read_csv(csp_path), kind="csp"))
        console.print(f"[dim]csp cards loaded: {len(cards)}[/]")
    if spread_path.exists():
        before = len(cards)
        cards.extend(cards_from_summary(pd.read_csv(spread_path), kind="spread"))
        console.print(f"[dim]spread cards added: {len(cards) - before}[/]")

    if not cards:
        console.print("[red]no card data — run scripts/focused_run.py and scripts/spread_run.py first[/]")
        return

    alloc = best_allocation(
        cards,
        capital=CAPITAL,
        target_monthly=TARGET_MONTHLY,
        max_drawdown_frac=MAX_DD_FRAC,
    )
    if not alloc:
        console.print("[red]no feasible allocation[/]")
        return

    console.print(f"\n[bold]Capital:[/] ${CAPITAL:,.0f}  [bold]Target:[/] ${TARGET_MONTHLY:.0f}/mo  [bold]Max 1-mo DD:[/] ${CAPITAL * MAX_DD_FRAC:,.0f}")
    console.print(
        f"Expected monthly: ${alloc['expected_monthly']:.0f}  "
        f"Worst single month: ${alloc['worst_single_month']:.0f}  "
        f"Hits target: {alloc['hits_target']}"
    )
    console.print(f"Capital used: ${alloc['collateral']:,.0f}  Reserve: ${alloc['cash_reserve']:,.0f}")

    t = Table(title="Allocation")
    for col in ["kind", "underlying", "N", "Δ", "DTE", "width", "PT", "mg@",
                "avg $/mo", "worst $/mo", "BP or coll", "win%", "tail%"]:
        t.add_column(col)
    for pos in alloc["positions"]:
        c = pos["card"]
        t.add_row(
            c.kind,
            c.underlying,
            str(pos["n"]),
            f"{c.target_delta:.2f}",
            str(c.target_dte),
            f"${c.spread_width:.0f}" if c.spread_width else "—",
            f"{c.profit_take:.2f}",
            str(c.manage_at_dte) if c.manage_at_dte is not None else "—",
            f"${c.avg_monthly_pnl:.0f}",
            f"${c.worst_month_pnl:.0f}",
            f"${c.capital_per_contract:,.0f}",
            f"{c.win_rate:.0%}",
            f"{c.loss_tail_rate:.0%}",
        )
    console.print(t)

    _write_markdown(alloc, cards)


def _write_markdown(alloc: dict, cards: list) -> None:
    p = RESULTS_DIR / "plan_v2.md"
    lines: list[str] = []

    lines.append(f"# CSP + Credit Spread Plan — ${CAPITAL:,.0f} → ${TARGET_MONTHLY:.0f}/month target\n")

    lines.append("## Executive summary\n")
    lines.append(f"- Capital: **${CAPITAL:,.0f}**")
    lines.append(f"- Target: **${TARGET_MONTHLY:.0f}/month** (~{TARGET_MONTHLY * 12 / CAPITAL:.1%} annual on capital)")
    lines.append(f"- Max single-month loss tolerance: **${CAPITAL * MAX_DD_FRAC:,.0f}** ({MAX_DD_FRAC:.0%} of capital)")
    lines.append(f"- **Expected monthly P/L:** ${alloc['expected_monthly']:.0f} → {'HITS target' if alloc['hits_target'] else 'UNDERSHOOTS target'}")
    lines.append(f"- **Worst backtested single-month (sum of position worst-months):** ${alloc['worst_single_month']:.0f}")
    lines.append(f"- **Capital used:** ${alloc['collateral']:,.0f}  •  Reserve ${alloc['cash_reserve']:,.0f}")
    lines.append("")

    lines.append("## Allocation\n")
    lines.append("| Kind | Underlying | N | Δ | DTE | Width | PT | Mg@DTE | Capital/ct | Avg $/mo/ct | Worst $/mo/ct | Win% | Tail% |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for pos in alloc["positions"]:
        c = pos["card"]
        width_cell = f"${c.spread_width:.0f}" if c.spread_width else "—"
        mg_cell = str(c.manage_at_dte) if c.manage_at_dte is not None else "—"
        lines.append(
            f"| {c.kind} | {c.underlying} | {pos['n']} | {c.target_delta:.2f} | {c.target_dte} | "
            f"{width_cell} | {c.profit_take:.2f} | {mg_cell} | ${c.capital_per_contract:,.0f} | "
            f"${c.avg_monthly_pnl:.0f} | ${c.worst_month_pnl:.0f} | {c.win_rate:.0%} | {c.loss_tail_rate:.0%} |"
        )
    lines.append("")

    lines.append("## Execution playbook\n")
    lines.append("### Monthly cycle\n")
    lines.append("1. **Entry day:** trading day closest to 35 calendar days before the 3rd-Friday monthly expiry.")
    lines.append("2. **Strike selection (short leg of spreads and CSPs):** pick the put whose BSM delta at that day's close is closest to the target delta in the allocation.")
    lines.append("3. **Long leg of each spread:** buy the put `spread_width` dollars below the short strike (the plan's `Width` column).")
    lines.append("4. **Order type:** combo limit order on spreads (sell-to-open as a single ticket). Do not leg in — gap risk between fills is real.")
    lines.append("5. **Buying power required:** `(width − net credit) × 100 × N`. Cash-secured puts require `strike × 100 × N`.")
    lines.append("")
    lines.append("### Management\n")
    lines.append("- **Profit take:** close at 50% of entry credit unless the row says PT=1.00 (= hold to expiry). The backtest shows hold-to-expiry dominates for CSPs; spreads vary and this plan uses whichever PT the backtest selected.")
    lines.append("- **Time management:** close at 21 DTE only if the row shows `Mg@DTE = 21`. Otherwise let the trade run.")
    lines.append("- **Adverse moves:** if the underlying gaps through the short strike intraday, consider rolling the entire spread down-and-out for net credit. Do not roll for a debit.")
    lines.append("")
    lines.append("### Guardrails (non-negotiable)\n")
    lines.append(f"- Cap total spread count at **{sum(p['n'] for p in alloc['positions'] if p['card'].kind == 'spread')}** (from the allocation). More spreads means higher correlated max-loss risk.")
    lines.append("- If VIX > 30 on entry, **halve** contract count. If VIX > 40, **skip** the cycle.")
    lines.append("- Keep at least 10% of capital as uncommitted cash for broker stress-test margin calls.")
    lines.append("- Never sell single-name puts unless you have a 6-month live track record on this system.")
    lines.append("")

    lines.append("## Why spreads instead of just CSPs?\n")
    lines.append(dedent(f"""
    On this capital, pure ETF CSPs cannot clear the $1,000/mo bar — the math
    simply doesn't work at prudent deltas. Credit spreads deliver the same
    directional thesis (underlying stays above short strike) but tie up roughly
    **1/20th the capital per trade**. On $41k, you can run ~7 IWM 10-wide
    spreads for the buying power of a single IWM CSP, and stack the premiums.

    The trade-off is real: if IWM gaps through both strikes (like Feb 2025
    when IWM fell 10% in 5 weeks), **every** spread hits max loss
    simultaneously. This is why the guardrail caps the spread count — beyond
    that, a single crash month could blow through the 15%-of-capital loss
    tolerance.

    The backtest in `results/spread_trades.csv` captures this exactly: every
    max-loss outcome happened in the Feb-Mar 2025 and Feb-Mar 2026 windows,
    mirroring the CSP assignment pattern. You are not taking "safer" trades
    with spreads — you are taking the *same* trades with better capital
    leverage. That leverage works both directions.
    """).strip() + "\n")

    lines.append("## Data provenance\n")
    lines.append("All numbers derive from 22 monthly cycles in the 2024-04 → 2026-04 window on Massive.com daily bars (Basic tier).\n")
    lines.append("- CSP configs: `results/summary.csv`  (5 configs × 22 cycles)")
    lines.append("- Spread configs: `results/spread_summary.csv`  (36 configs × up to 22 cycles each)")
    lines.append("- Per-trade detail: `results/trades.csv` and `results/spread_trades.csv`")
    lines.append("")

    lines.append("## Caveats\n")
    lines.append("- 2-year window is a mostly-bull regime with two ~8-10% corrections. A 2022-style drawdown is not in the data.")
    lines.append("- Fills are modeled at daily close ± 2% slippage per leg. Real spread fills at mid-of-spread on liquid ETFs should be tighter; the backtest is intentionally conservative.")
    lines.append("- Long-leg bars can be missing on low-volume days; the engine skips those days' exit checks rather than synthesizing prices.")
    lines.append("- Assignment modeled as sell-at-expiry-close for CSPs; does not model the wheel. Real wheel returns would be ~2-4 points higher annualized.")
    p.write_text("\n".join(lines))
    console.print(f"\n[bold green]Plan written to {p}[/]")


if __name__ == "__main__":
    main()
