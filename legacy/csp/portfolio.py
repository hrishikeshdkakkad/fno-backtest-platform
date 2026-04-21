"""Capital allocation for the CSP income portfolio.

Given a capital budget and a table of backtested per-contract strategies, solve
for how many contracts of each to write in order to maximize expected monthly
premium subject to:

- sum(contracts_i * collateral_i) <= capital
- each strategy's contracts >= 0 integer
- optional: require projected worst-month P/L > -max_drawdown_dollars

This is a tiny integer program; with a small search space (few strategies and
few candidate counts each) we can enumerate brute-force.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import pandas as pd


@dataclass(slots=True)
class StrategyCard:
    key: str                  # human label
    kind: str                 # 'csp' or 'spread'
    underlying: str
    avg_monthly_pnl: float    # per-contract $
    worst_month_pnl: float    # per-contract $
    pnl_std: float            # per-contract $
    capital_per_contract: float  # CSP: strike*100; spread: buying_power
    win_rate: float
    loss_tail_rate: float     # CSP: assignment_rate; spread: max_loss_rate
    annualized_return: float
    target_delta: float
    target_dte: int
    profit_take: float
    manage_at_dte: int | None
    spread_width: float | None = None   # only for spread cards

    # backwards compatibility for existing call sites
    @property
    def collateral(self) -> float:
        return self.capital_per_contract

    @property
    def assignment_rate(self) -> float:
        return self.loss_tail_rate


def cards_from_summary(df: pd.DataFrame, kind: str = "csp") -> list[StrategyCard]:
    cards: list[StrategyCard] = []
    for _, r in df.iterrows():
        if pd.isna(r.get("avg_monthly_pnl")) or r.get("n", 0) < 12:
            continue
        if kind == "csp":
            capital = float(r["avg_collateral"])
            annual = float(r.get("annualized_return_on_collateral", 0))
            loss_tail = float(r.get("assignment_rate", 0))
            width = None
            label = (
                f"{r['underlying']}|CSP|Δ{r['target_delta']}|dte{r['target_dte']}|"
                f"pt{r['profit_take']}|mg{r['manage_at_dte']}"
            )
        else:  # spread
            capital = float(r["avg_buying_power"])
            annual = float(r.get("annualized_return_on_bp", 0))
            loss_tail = float(r.get("max_loss_rate", 0))
            width = float(r.get("spread_width", 0)) if "spread_width" in r else None
            label = (
                f"{r['underlying']}|SPREAD|Δ{r['target_delta']}|w${width}|dte{r['target_dte']}|"
                f"pt{r['profit_take']}|mg{r['manage_at_dte']}"
            )
        cards.append(
            StrategyCard(
                key=label,
                kind=kind,
                underlying=str(r["underlying"]),
                avg_monthly_pnl=float(r["avg_monthly_pnl"]),
                worst_month_pnl=float(r["worst_month_pnl"]),
                pnl_std=float(r.get("pnl_std_dollars", 0) or 0),
                capital_per_contract=capital,
                win_rate=float(r.get("win_rate", 0)),
                loss_tail_rate=loss_tail,
                annualized_return=annual,
                target_delta=float(r["target_delta"]),
                target_dte=int(r["target_dte"]),
                profit_take=float(r["profit_take"]),
                manage_at_dte=(None if pd.isna(r.get("manage_at_dte")) else int(r.get("manage_at_dte"))),
                spread_width=width,
            )
        )
    return cards


def best_allocation(
    cards: list[StrategyCard],
    capital: float,
    target_monthly: float,
    max_csp_contracts: int = 2,
    max_spread_contracts: int = 20,
    require_diversification: bool = True,
    max_drawdown_frac: float = 0.15,
) -> dict:
    """Enumerate feasible integer allocations and score them.

    Scoring: prefer allocations whose expected avg_monthly_pnl >= target_monthly,
    then minimize the worst-case single-month loss, then prefer lower loss-tail
    rate, then more diversification (distinct underlyings).
    """
    if not cards:
        return {}

    # Keep top candidates per (underlying, kind) ranked by *capital efficiency*
    # (avg_monthly_pnl / capital_per_contract). High-$-per-BP spreads can
    # deploy many contracts under a fixed DD budget even when their absolute
    # per-contract P/L is low, so the top-by-dollars-only rule from v1 misses
    # them. We keep up to 3 per bucket to let the enumerator consider
    # wide-vs-narrow tradeoffs.
    def _efficiency(c: StrategyCard) -> float:
        if c.capital_per_contract <= 0:
            return 0.0
        return c.avg_monthly_pnl / c.capital_per_contract

    by_bucket: dict[tuple[str, str], list[StrategyCard]] = {}
    for c in cards:
        by_bucket.setdefault((c.underlying, c.kind), []).append(c)
    pool: list[StrategyCard] = []
    for key, lst in by_bucket.items():
        # Top 1 by capital efficiency — the card that generates the most
        # $/mo per $ of capital. Under a DD-constrained budget this is
        # the card you want to deploy most copies of.
        lst_sorted = sorted(lst, key=lambda c: (-_efficiency(c), c.loss_tail_rate))
        pool.extend(lst_sorted[:1])

    best: dict | None = None
    max_dd = capital * max_drawdown_frac

    # Per-card max count: spreads are cheap (BP-based), CSPs are expensive.
    max_counts = [
        (max_spread_contracts if c.kind == "spread" else max_csp_contracts)
        for c in pool
    ]
    # Also cap at affordable headroom per card.
    max_counts = [
        min(mc, int(capital / c.capital_per_contract) + 1)
        for mc, c in zip(max_counts, pool)
    ]
    ranges = [range(0, mc + 1) for mc in max_counts]
    for combo in product(*ranges):
        if sum(combo) == 0:
            continue
        collat = sum(n * c.capital_per_contract for n, c in zip(combo, pool))
        if collat > capital:
            continue
        expected = sum(n * c.avg_monthly_pnl for n, c in zip(combo, pool))
        worst = sum(n * c.worst_month_pnl for n, c in zip(combo, pool))
        if worst < -max_dd:
            continue
        # Scoring priorities:
        #  1) hit monthly target (binary)
        #  2) maximize expected P/L
        #  3) minimize loss-tail frequency (fewer big-loss months)
        #  4) maximize worst (less negative) as tiebreaker
        #  5) minimize capital usage
        hit_target = 1 if expected >= target_monthly else 0
        score = (
            hit_target,
            expected,
            -(sum(c.loss_tail_rate * n for n, c in zip(combo, pool))),
            worst,
            -collat,
        )
        if best is None or score > best["score"]:
            best = {
                "score": score,
                "combo": combo,
                "pool": pool,
                "collateral": collat,
                "cash_reserve": capital - collat,
                "expected_monthly": expected,
                "worst_single_month": worst,
                "hits_target": bool(hit_target),
            }
    if best is None:
        return {}
    best["positions"] = [
        {"underlying": c.underlying, "n": n, "card": c}
        for n, c in zip(best["combo"], best["pool"]) if n > 0
    ]
    return best
