"""Engine: capital deployment / equity-curve simulation.

Pure-function module that walks a sequence of trades against a fixed
capital base and returns an `EquityResult` with both fixed-size and
compounding curves, per-trade lot sizes, and standard summary statistics
(total P&L, final equity, max drawdown, annualised return, Sharpe).

Design notes
------------
* The engine always computes BOTH the non-compounding and the compounding
  paths. The `compounding` flag on `CapitalSpec` is therefore currently a
  no-op at the engine level — callers read `pnl_fixed` when they want the
  fixed-size view and `pnl_compound`/`equity_compound` when they want the
  compounding view. Higher-level code (e.g. reporting) may consult
  `CapitalSpec.compounding` to decide which curve to display; the engine
  itself emits both so the caller can compare.

* `lot_rounding_mode` on `CapitalSpec` honours `floor` (default, matches
  the legacy behaviour used throughout the V3 backtest) and `round`
  (banker's-rounding via `round()`), letting callers trade a touch of
  extra deployment for lot parity against a real broker's margin rounding.

* The lot sizing is clamped at zero on both paths so an underwater
  compounding account cannot take "negative positions" — the cycle is
  skipped, matching what a real broker enforces via a buying-power check.

Conventions
-----------
* `years` is used for annualisation and Sharpe scaling. When omitted or
  0, annualisation becomes 0 (same as the legacy helper).
* `sharpe` is the per-trade Sharpe of non-compounding return-on-capital,
  annualised by √(trades/year) to match `v3_capital_analysis`. This
  differs from `calibrate.summary_stats`' per-lot Sharpe (which uses
  √252).

All monetary values use rupees (₹).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from nfo.specs.strategy import CapitalSpec


@dataclass(slots=True)
class EquityResult:
    """Result of deploying a series of trades against a fixed capital base.

    Attributes
    ----------
    pnl_fixed : pd.Series
        Per-trade P&L under non-compounding (always deploy `capital`).
    pnl_compound : pd.Series
        Per-trade P&L under compounding (deploy current equity).
    equity_compound : pd.Series
        Running equity after each compounding trade.
    lots_fixed : pd.Series
        Lots deployed per trade under non-compounding.
    lots_compound : pd.Series
        Lots deployed per trade under compounding.
    total_pnl_fixed : float
    total_pnl_compound : float
    final_equity_compound : float
    max_drawdown_pct : float
        Peak-to-trough percentage drop in compounding equity.
    annualised_pct_fixed : float
    annualised_pct_compound : float
        CAGR of compounding equity over the window.
    sharpe : float
        Per-trade Sharpe of non-compounding return-on-capital, annualised
        by √(trades/year) to match the convention used by
        `v3_capital_analysis`.
    years : float
    """
    pnl_fixed: pd.Series
    pnl_compound: pd.Series
    equity_compound: pd.Series
    lots_fixed: pd.Series
    lots_compound: pd.Series
    total_pnl_fixed: float
    total_pnl_compound: float
    final_equity_compound: float
    max_drawdown_pct: float
    annualised_pct_fixed: float
    annualised_pct_compound: float
    sharpe: float
    years: float


def _lots(budget: float, bp_per_lot: float, mode: str) -> int:
    """Size `budget` into integer lots at `bp_per_lot`, clamped at zero.

    Parameters
    ----------
    mode : {"floor", "round"}
        "floor" keeps the legacy behaviour (`budget // bp_per_lot`);
        "round" uses banker's rounding via Python's built-in `round()`.
    """
    if bp_per_lot <= 0:
        return 0
    if mode == "round":
        # `round()` returns a float; int() then truncates any residual.
        return max(0, int(round(budget / bp_per_lot)))
    # default: floor
    return max(0, int(budget // bp_per_lot))


def compute_equity_curves(
    trades: pd.DataFrame,
    *,
    capital_spec: CapitalSpec,
    years: float = 0.0,
) -> EquityResult:
    """Walk `trades` in row order, deploying capital per the two standard rules.

    Expected columns: `buying_power` (₹ per lot) and `pnl_contract` (₹ per
    lot, net of costs). Row order is the trade sequence.

    `years` is used for annualisation and Sharpe scaling; if 0 (the
    default), annualisation becomes 0. `v3_capital_analysis` derives
    `years` from the signals-parquet span.

    `capital_spec.deployment_fraction` controls how much of each account
    balance is put to work on each cycle — 1.0 (the default) matches the
    V3 behaviour; 0.1 models a 10 %-of-equity allocation with the
    remainder held as reserve. Applied to BOTH the non-compounding
    budget (fraction of the fixed capital) and the compounding budget
    (fraction of current equity).

    `capital_spec.lot_rounding_mode` chooses between `floor` (default,
    conservative — fractional lots round down) and `round` (nearest).
    """
    capital = float(capital_spec.fixed_capital_inr)
    deployment_frac = float(capital_spec.deployment_fraction)
    lot_mode = capital_spec.lot_rounding_mode

    # CapitalSpec already validates 0 < deployment_fraction <= 1.0 via
    # Pydantic's Field constraints, so we don't re-validate here.

    if trades.empty:
        empty = pd.Series(dtype=float)
        return EquityResult(
            pnl_fixed=empty, pnl_compound=empty, equity_compound=empty,
            lots_fixed=pd.Series(dtype=int), lots_compound=pd.Series(dtype=int),
            total_pnl_fixed=0.0, total_pnl_compound=0.0,
            final_equity_compound=capital, max_drawdown_pct=0.0,
            annualised_pct_fixed=0.0, annualised_pct_compound=0.0,
            sharpe=0.0, years=years or 0.0,
        )

    pnl_fixed_vals: list[float] = []
    pnl_compound_vals: list[float] = []
    equity_compound_vals: list[float] = []
    lots_fixed_vals: list[int] = []
    lots_compound_vals: list[int] = []

    equity = float(capital)
    peak = equity
    max_dd = 0.0

    for _, t in trades.iterrows():
        bp_per_lot = float(t["buying_power"])
        pnl_per_lot = float(t["pnl_contract"])

        fixed_budget = capital * deployment_frac
        compound_budget = equity * deployment_frac
        # You cannot take a negative position just because the account is
        # underwater. Clamp both sizings at zero — the cycle is skipped when
        # there's no capital to deploy, which is what a real broker would
        # enforce via the buying-power check.
        lots_fx = _lots(fixed_budget, bp_per_lot, lot_mode)
        pnl_fx = lots_fx * pnl_per_lot
        lots_cp = _lots(compound_budget, bp_per_lot, lot_mode)
        pnl_cp = lots_cp * pnl_per_lot
        equity += pnl_cp
        peak = max(peak, equity)
        if peak > 0:
            # Clamp at 1.0 so "bankruptcy" registers as 100 % drawdown rather
            # than an artificial >100 % when the account goes negative.
            dd = min(1.0, max(0.0, (peak - equity) / peak))
            max_dd = max(max_dd, dd)

        pnl_fixed_vals.append(pnl_fx)
        pnl_compound_vals.append(pnl_cp)
        equity_compound_vals.append(equity)
        lots_fixed_vals.append(lots_fx)
        lots_compound_vals.append(lots_cp)

    pnl_fixed = pd.Series(pnl_fixed_vals)
    pnl_compound = pd.Series(pnl_compound_vals)
    equity_series = pd.Series(equity_compound_vals)

    total_fx = float(pnl_fixed.sum())
    total_cp = equity - capital
    yrs = years if years is not None and years > 0 else 0.0
    ann_fx = (total_fx / capital) / yrs * 100 if yrs > 0 else 0.0
    ann_cp = ((equity / capital) ** (1 / yrs) - 1) * 100 if yrs > 0 and capital > 0 else 0.0

    if len(pnl_fixed) > 1 and pnl_fixed.std(ddof=1) > 0 and yrs > 0:
        rets = pnl_fixed / capital
        sharpe = float(rets.mean() / rets.std(ddof=1) * math.sqrt(len(pnl_fixed) / yrs))
    else:
        sharpe = 0.0

    return EquityResult(
        pnl_fixed=pnl_fixed,
        pnl_compound=pnl_compound,
        equity_compound=equity_series,
        lots_fixed=pd.Series(lots_fixed_vals),
        lots_compound=pd.Series(lots_compound_vals),
        total_pnl_fixed=total_fx,
        total_pnl_compound=total_cp,
        final_equity_compound=equity,
        max_drawdown_pct=max_dd * 100,
        annualised_pct_fixed=ann_fx,
        annualised_pct_compound=ann_cp,
        sharpe=sharpe,
        years=yrs,
    )
