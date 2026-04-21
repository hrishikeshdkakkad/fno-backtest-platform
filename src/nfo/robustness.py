"""Robustness-test primitives for the V3 credit-spread filter.

Purpose
-------
Turn the single "V3 fires 8 cycles, 100 % win" headline into a set of
sensitivity statistics that tell us whether the edge survives:

* realistic slippage,
* loss of any single V3 cycle (leave-one-out),
* resampling of the observed cycles (block bootstrap).

Composition
-----------
The helpers here are intentionally pure: given a DataFrame of matched V3
trades, they return a new DataFrame or a small dataclass. The CLI driver
at `scripts/nfo/v3_robustness.py` composes them and writes markdown/CSV.
Existing infrastructure is reused wherever possible:

* V3 filter evaluation → `scripts/nfo/redesign_variants.get_firing_dates`
* Per-cycle trade selection → mirrors `v3_capital_analysis._pick_trade`
* Per-trade summary stats → `nfo.calibrate.summary_stats`

All monetary values use rupees (₹).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .calibrate import SummaryStats, summary_stats
from .config import RESULTS_DIR
# Re-export EquityResult from engine.capital so `robustness.EquityResult` is
# the same class object (not a duplicate). The engine implementation is the
# source of truth; robustness.compute_equity_curves is a thin shim over it.
from nfo.engine.capital import (  # noqa: E402
    EquityResult,
    compute_equity_curves as _engine_compute_equity_curves,
)


# ── Shared defaults ─────────────────────────────────────────────────────────

# V3 is defined against NIFTY at Δ≈0.30 on a 100-pt-wide spread; this is the
# selection filter used by `v3_capital_analysis._pick_trade` and echoed here so
# scripts can match trades without reaching into that script's internals.
V3_PARAM_DELTA = 0.30
V3_PARAM_WIDTH = 100.0

TRADES_PATH = RESULTS_DIR / "spread_trades.csv"
GAPS_PATH = RESULTS_DIR / "spread_trades_v3_gaps.csv"


# ── Trade matching ──────────────────────────────────────────────────────────


def load_trades_with_gaps(
    *,
    trades_path: Path = TRADES_PATH,
    gaps_path: Path = GAPS_PATH,
) -> pd.DataFrame:
    """Load `spread_trades.csv` and merge `spread_trades_v3_gaps.csv` if present.

    The gap file captures V3 fires that don't sit on the standard 35-DTE
    grid; `v3_capital_analysis` merges them the same way, so robustness
    tests must too.
    """
    trades = pd.read_csv(trades_path)
    if gaps_path.exists():
        gaps = pd.read_csv(gaps_path)
        trades = pd.concat([trades, gaps], ignore_index=True)
    return trades


def pick_trade_for_expiry(
    trades: pd.DataFrame,
    expiry: str,
    pt_variant: str,
    *,
    param_delta: float = V3_PARAM_DELTA,
    param_width: float = V3_PARAM_WIDTH,
) -> pd.Series | None:
    """Return the V3 trade at this expiry for the given exit variant.

    Mirrors `scripts/nfo/v3_capital_analysis._pick_trade` so both code paths
    resolve cycles to trades identically. Returns None when no matching row
    exists in `trades`.
    """
    sub = trades[
        (trades["param_delta"] == param_delta)
        & (trades["param_width"] == param_width)
        & (trades["expiry_date"] == expiry)
    ]
    if sub.empty:
        return None
    if pt_variant == "pt50":
        pt = sub[sub["param_pt"] == 0.50]
        return pt.iloc[0] if not pt.empty else sub.iloc[0]
    if pt_variant == "hte":
        hte = sub[sub["param_pt"] == 1.0]
        return hte.iloc[0] if not hte.empty else sub.iloc[0]
    raise ValueError(f"pt_variant must be 'pt50' or 'hte', got {pt_variant!r}")


def get_v3_matched_trades(
    signals_df: pd.DataFrame,
    trades: pd.DataFrame,
    pt_variant: str,
) -> pd.DataFrame:
    """Return one matched trade per V3-firing cycle (or empty if none).

    The V3 filter is applied via `redesign_variants.get_firing_dates`; the
    resulting fire dates are grouped by target_expiry (one cycle per
    expiry) and matched against `trades` via `pick_trade_for_expiry`. The
    returned frame has the same columns as `trades` plus a `v3_first_fire`
    column recording which session first tripped the filter for that cycle.
    Row order follows expiry ascending.
    """
    # Imported here to avoid a hard dependency when only the cost model is
    # needed — the scripts/ directory isn't on the package path by default.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "nfo"))
    import redesign_variants as _rv  # noqa: E402

    v3 = next(v for v in _rv.make_variants() if v.name == "V3")
    atr_series = _rv.load_nifty_atr(signals_df["date"])
    fires = _rv.get_firing_dates(v3, signals_df, atr_series)

    # Group fire dates by target_expiry so we get one cycle per expiry.
    by_expiry: dict[str, list[pd.Timestamp]] = {}
    for fire_date, _ in fires:
        row = signals_df[signals_df["date"].dt.date == fire_date]
        if row.empty:
            continue
        exp = row["target_expiry"].iloc[0]
        if not exp:
            continue
        ts = pd.Timestamp(fire_date)
        by_expiry.setdefault(str(exp), []).append(ts)

    rows: list[pd.Series] = []
    for exp in sorted(by_expiry):
        trade = pick_trade_for_expiry(trades, exp, pt_variant)
        if trade is None:
            continue
        first_fire = min(by_expiry[exp])
        enriched = trade.copy()
        enriched["v3_first_fire"] = first_fire.date().isoformat()
        rows.append(enriched)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).reset_index(drop=True)


# ── Equity curves ───────────────────────────────────────────────────────────
#
# `EquityResult` and the core computation now live in `nfo.engine.capital`.
# The function below is a thin shim that translates the legacy kwargs
# (`capital`, `deployment_frac`) into a `CapitalSpec` and forwards the call.
# This keeps every existing caller in `scripts/nfo/*` and the tests under
# `tests/nfo/test_robustness.py` working unchanged.


def compute_equity_curves(
    trades: pd.DataFrame,
    *,
    capital: float,
    years: float | None = None,
    deployment_frac: float = 1.0,
) -> EquityResult:
    """Shim over `nfo.engine.capital.compute_equity_curves`.

    Accepts the legacy kwargs (`capital`, `years`, `deployment_frac`) and
    forwards to the engine with a `CapitalSpec`. Behaviour is identical to
    the pre-refactor implementation; see `nfo.engine.capital` for the
    canonical docstring.

    Raises
    ------
    ValueError
        If `deployment_frac` is outside `(0.0, 1.0]`.
    """
    if not 0.0 < deployment_frac <= 1.0:
        raise ValueError("deployment_frac must be in (0.0, 1.0]")
    from nfo.specs.strategy import CapitalSpec
    spec = CapitalSpec(
        fixed_capital_inr=capital,
        deployment_fraction=deployment_frac,
        compounding=False,  # engine always emits both curves; flag is advisory.
    )
    return _engine_compute_equity_curves(trades, capital_spec=spec, years=years or 0.0)


# ── Slippage sweep ──────────────────────────────────────────────────────────


def apply_slippage(
    trades: pd.DataFrame,
    slippage_rupees_per_lot: float,
) -> pd.DataFrame:
    """Return a copy of `trades` with flat rupee slippage subtracted from each
    trade's P&L.

    Modelled as an extra round-trip cost charged symmetrically — lowering
    `pnl_contract` by the same amount regardless of trade direction. This
    mirrors how real execution drag accumulates on a wide-strike NIFTY
    credit spread (ticks × lot dominate, not a % of premium). Leaves
    `gross_pnl_contract` untouched so the audit trail is preserved;
    `txn_cost_contract` is bumped so downstream reporting still reconciles.
    """
    if slippage_rupees_per_lot < 0:
        raise ValueError("slippage_rupees_per_lot must be >= 0")
    out = trades.copy()
    out["pnl_contract"] = out["pnl_contract"].astype(float) - slippage_rupees_per_lot
    if "txn_cost_contract" in out.columns:
        out["txn_cost_contract"] = out["txn_cost_contract"].astype(float) + slippage_rupees_per_lot
    return out


# ── Leave-one-out ───────────────────────────────────────────────────────────


@dataclass(slots=True)
class LooRow:
    """One row of a leave-one-out table.

    `summary.sharpe` is the per-lot Sharpe from `calibrate.summary_stats` —
    annualised by √252 under the assumption that each trade is a daily
    observation. `equity_sharpe` is the capital-deployed Sharpe from
    `compute_equity_curves`, annualised by √(trades/year). The latter
    matches what `v3_capital_analysis` reports and is the correct metric
    for monthly-cycle backtests; the former is kept for tests that already
    assert against it, but new reports should prefer `equity_sharpe`.
    """
    dropped_index: int
    dropped_expiry: str
    dropped_outcome: str
    dropped_pnl_contract: float
    summary: SummaryStats
    total_pnl_fixed: float
    total_pnl_compound: float
    final_equity_compound: float
    equity_sharpe: float
    max_drawdown_pct: float


def leave_one_out(
    matched_trades: pd.DataFrame,
    *,
    capital: float,
    years: float,
) -> list[LooRow]:
    """Drop each matched trade in turn; recompute summary stats + equity.

    Returns one `LooRow` per matched trade, in the same row order as the
    input frame. The `summary` field uses `calibrate.summary_stats`, so
    Sharpe uses the same convention as the rest of the codebase.
    """
    results: list[LooRow] = []
    for i in matched_trades.index:
        held = matched_trades.drop(index=i).reset_index(drop=True)
        if held.empty:
            continue
        stats = summary_stats(held)
        equity = compute_equity_curves(held, capital=capital, years=years)
        dropped = matched_trades.loc[i]
        results.append(
            LooRow(
                dropped_index=int(i),
                dropped_expiry=str(dropped.get("expiry_date", "")),
                dropped_outcome=str(dropped.get("outcome", "")),
                dropped_pnl_contract=float(dropped.get("pnl_contract", 0.0)),
                summary=stats,
                total_pnl_fixed=equity.total_pnl_fixed,
                total_pnl_compound=equity.total_pnl_compound,
                final_equity_compound=equity.final_equity_compound,
                equity_sharpe=equity.sharpe,
                max_drawdown_pct=equity.max_drawdown_pct,
            )
        )
    return results


# ── Tail-loss injection ─────────────────────────────────────────────────────


def synthetic_max_loss_row(template: pd.Series, width: float = 100.0) -> pd.Series:
    """Return a cycle-sized max-loss row based on `template`.

    A V3 trade hits max loss when both strikes end in-the-money at expiry:
    `pnl_per_share = net_credit - width`, so `pnl_contract = (net_credit - width) * lot`.
    We keep the trade's original `buying_power`, lot size, and entry
    metadata so the equity simulator treats it like any other row — only
    PnL, outcome, and exit columns change.

    This is deliberately a worst-case template. Historical V3 cycles never
    produced a max loss; we're asking "what if one of them had?"
    """
    lot = 65  # NIFTY lot size; matches spec freeze
    net_credit = float(template.get("net_credit", 0.0))
    loss_per_share = net_credit - float(width)
    out = template.copy()
    out["outcome"] = "max_loss"
    out["pnl_per_share"] = loss_per_share
    gross = loss_per_share * lot
    cost = float(template.get("txn_cost_contract", 100.0))
    out["gross_pnl_contract"] = gross
    out["txn_cost_contract"] = cost
    out["pnl_contract"] = gross - cost
    out["net_close_at_exit"] = float(width)
    out["synthetic_max_loss"] = True
    return out


def inject_tail_losses(
    matched_trades: pd.DataFrame,
    *,
    n_injections: int,
    rng: np.random.Generator,
    width: float = 100.0,
) -> pd.DataFrame:
    """Replace `n_injections` random rows in `matched_trades` with synthetic
    max-loss cycles. Returns a fresh DataFrame; the original is untouched.

    `rng` is a `numpy.random.Generator` — the caller controls seeding for
    reproducibility. When `n_injections >= len(matched_trades)`, every row
    becomes a max-loss.
    """
    if n_injections < 0:
        raise ValueError("n_injections must be >= 0")
    if n_injections == 0 or matched_trades.empty:
        out = matched_trades.copy()
        if "synthetic_max_loss" not in out.columns:
            out["synthetic_max_loss"] = False
        return out
    n_rows = len(matched_trades)
    k = min(n_injections, n_rows)
    inject_idx = rng.choice(n_rows, size=k, replace=False)
    out = matched_trades.copy().reset_index(drop=True)
    out["synthetic_max_loss"] = False
    for i in inject_idx:
        out.loc[i] = synthetic_max_loss_row(out.loc[i], width=width)
    return out


# ── Block bootstrap ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class BootstrapResult:
    """Aggregate outcome of a bootstrap resampling run.

    Each array has length `n_iter`; percentile digests are computed over
    them. Raw arrays are kept so callers can re-cut percentiles or draw
    histograms without rerunning the bootstrap. `capital` is echoed so
    callers can report compound-account probabilities relative to the
    starting balance rather than against zero.
    """
    n_iter: int
    capital: float
    total_pnl_fixed: np.ndarray
    total_pnl_compound: np.ndarray
    final_equity_compound: np.ndarray
    cagr_compound_pct: np.ndarray
    max_drawdown_pct: np.ndarray

    def prob_positive_fixed(self) -> float:
        """P(non-compounding total P&L > 0) across all resamples."""
        if self.n_iter == 0:
            return float("nan")
        return float((self.total_pnl_fixed > 0).mean())

    def prob_positive_compound(self) -> float:
        """P(compound final equity > starting capital) across all resamples.

        This is the correct headline number when the report presents
        compound equity / CAGR — non-compounding positivity can mask draws
        where the account ends below its starting balance.
        """
        if self.n_iter == 0:
            return float("nan")
        return float((self.final_equity_compound > self.capital).mean())

    def percentiles(self, ps: Iterable[float] = (5, 25, 50, 75, 95)) -> pd.DataFrame:
        rows = []
        for p in ps:
            rows.append({
                "percentile": p,
                "total_pnl_fixed": float(np.percentile(self.total_pnl_fixed, p)),
                "total_pnl_compound": float(np.percentile(self.total_pnl_compound, p)),
                "final_equity_compound": float(np.percentile(self.final_equity_compound, p)),
                "cagr_compound_pct": float(np.percentile(self.cagr_compound_pct, p)),
                "max_drawdown_pct": float(np.percentile(self.max_drawdown_pct, p)),
            })
        return pd.DataFrame(rows)


def block_bootstrap(
    matched_trades: pd.DataFrame,
    *,
    capital: float,
    years: float,
    n_iter: int = 10_000,
    seed: int = 42,
) -> BootstrapResult:
    """Resample `matched_trades` with replacement `n_iter` times.

    Each draw pulls `len(matched_trades)` rows with replacement and walks
    them through `compute_equity_curves`. The result is the empirical
    distribution of the equity outcomes, which converts a "we got lucky
    eight times" into a percentile view.

    The resampling unit is one cycle (one row). `years` is held constant
    across draws so annualisation is apples-to-apples. Use `seed` to make
    runs reproducible across the CI regression tests.
    """
    if matched_trades.empty:
        return BootstrapResult(
            n_iter=0,
            capital=capital,
            total_pnl_fixed=np.array([]),
            total_pnl_compound=np.array([]),
            final_equity_compound=np.array([]),
            cagr_compound_pct=np.array([]),
            max_drawdown_pct=np.array([]),
        )
    rng = np.random.default_rng(seed)
    n_rows = len(matched_trades)
    totals_fx = np.empty(n_iter, dtype=float)
    totals_cp = np.empty(n_iter, dtype=float)
    finals = np.empty(n_iter, dtype=float)
    cagrs = np.empty(n_iter, dtype=float)
    dds = np.empty(n_iter, dtype=float)
    for i in range(n_iter):
        idx = rng.integers(0, n_rows, size=n_rows)
        sampled = matched_trades.iloc[idx].reset_index(drop=True)
        eq = compute_equity_curves(sampled, capital=capital, years=years)
        totals_fx[i] = eq.total_pnl_fixed
        totals_cp[i] = eq.total_pnl_compound
        finals[i] = eq.final_equity_compound
        cagrs[i] = eq.annualised_pct_compound
        dds[i] = eq.max_drawdown_pct
    return BootstrapResult(
        n_iter=n_iter,
        capital=capital,
        total_pnl_fixed=totals_fx,
        total_pnl_compound=totals_cp,
        final_equity_compound=finals,
        cagr_compound_pct=cagrs,
        max_drawdown_pct=dds,
    )
