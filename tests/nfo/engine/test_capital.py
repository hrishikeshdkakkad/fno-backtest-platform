"""Unit tests for engine.capital.compute_equity_curves.

Spec-driven behaviour tests for the capital-deployment engine. Parity with
the legacy ``robustness.compute_equity_curves`` (now a thin shim) lives in
test_capital_parity.py.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from nfo.engine.capital import EquityResult, compute_equity_curves
from nfo.specs.strategy import CapitalSpec


def _spec(
    *,
    capital: float = 1_000_000.0,
    deployment_fraction: float = 1.0,
    compounding: bool = False,
    lot_rounding_mode: str = "floor",
) -> CapitalSpec:
    return CapitalSpec(
        fixed_capital_inr=capital,
        deployment_fraction=deployment_fraction,
        compounding=compounding,
        lot_rounding_mode=lot_rounding_mode,
    )


# ── empty-trades frame ──────────────────────────────────────────────────────


def test_empty_trades_returns_zero_curves():
    empty = pd.DataFrame(columns=["buying_power", "pnl_contract"])
    eq = compute_equity_curves(empty, capital_spec=_spec(capital=1_000_000), years=1.0)
    assert isinstance(eq, EquityResult)
    assert eq.total_pnl_fixed == 0.0
    assert eq.total_pnl_compound == 0.0
    assert eq.final_equity_compound == 1_000_000
    assert eq.max_drawdown_pct == 0.0
    assert eq.annualised_pct_fixed == 0.0
    assert eq.annualised_pct_compound == 0.0
    assert eq.sharpe == 0.0
    assert eq.pnl_fixed.empty
    assert eq.pnl_compound.empty
    assert eq.equity_compound.empty
    assert eq.lots_fixed.empty
    assert eq.lots_compound.empty


# ── single trade ────────────────────────────────────────────────────────────


def test_single_trade_known_values():
    """capital=1M, bp_per_lot=100k, pnl=10k/lot, years=0.25 → 10 lots, pnl=100k."""
    df = pd.DataFrame({
        "buying_power": [100_000.0],
        "pnl_contract": [10_000.0],
    })
    eq = compute_equity_curves(
        df, capital_spec=_spec(capital=1_000_000), years=0.25,
    )
    assert list(eq.lots_fixed) == [10]
    assert list(eq.pnl_fixed) == [100_000]
    assert eq.total_pnl_fixed == pytest.approx(100_000)
    # Compound walk: 1M + 10*10_000 = 1.1M
    assert eq.final_equity_compound == pytest.approx(1_100_000)
    assert eq.total_pnl_compound == pytest.approx(100_000)
    assert eq.years == pytest.approx(0.25)


# ── ten-trade frame: compounding, non-compounding, drawdown ────────────────


def _ten_trade_frame() -> pd.DataFrame:
    """Ten trades with mixed outcomes and at least one losing trade."""
    return pd.DataFrame({
        "buying_power": [10_000.0] * 10,
        # mix of wins and losses; index 4 is a big loss to force drawdown
        "pnl_contract": [
            1_000.0, 500.0, 800.0, 600.0,
            -2_500.0,  # big loss moves peak→trough
            400.0, 700.0, 300.0, 900.0, 1_100.0,
        ],
    })


def test_ten_trade_non_compounding_constant_budget():
    df = _ten_trade_frame()
    eq = compute_equity_curves(df, capital_spec=_spec(capital=100_000), years=1.0)
    # Non-compounding always deploys the fixed 100k budget → 10 lots on each.
    assert list(eq.lots_fixed) == [10] * 10


def test_ten_trade_compounding_equity_grows():
    df = _ten_trade_frame()
    eq = compute_equity_curves(df, capital_spec=_spec(capital=100_000), years=1.0)
    # Compounding should end at a different final equity than starting capital.
    assert eq.final_equity_compound != 100_000
    # Sum of all pnl × 10 (fixed) = (1000+500+800+600-2500+400+700+300+900+1100) × 10
    # = 3800 × 10 = 38_000 under fixed. Compound differs because lot-sizing varies.
    assert eq.total_pnl_fixed == pytest.approx(38_000)


def test_ten_trade_drawdown_moves_peak_to_trough():
    df = _ten_trade_frame()
    eq = compute_equity_curves(df, capital_spec=_spec(capital=100_000), years=1.0)
    # Peak occurs right before the -2,500 loss; max_drawdown_pct must be > 0.
    assert eq.max_drawdown_pct > 0.0


# ── lot rounding mode ───────────────────────────────────────────────────────


def test_lot_rounding_floor_is_default():
    """Default mode='floor' floors fractional lots. 100k / 30k = 3.333 → 3 lots."""
    df = pd.DataFrame({
        "buying_power": [30_000.0],
        "pnl_contract": [500.0],
    })
    eq = compute_equity_curves(
        df, capital_spec=_spec(capital=100_000, lot_rounding_mode="floor"), years=1.0,
    )
    assert list(eq.lots_fixed) == [3]  # floor(100_000 / 30_000) = 3


def test_lot_rounding_round_mode_rounds_to_nearest():
    """mode='round' rounds to nearest. 100k / 30k = 3.333 → 3 lots.
       100k / 40k = 2.5 → 2 under banker's rounding, or 3 under arith rounding;
       we just assert it behaves differently than floor where applicable."""
    # 100k / 29_400 = 3.40 → floor=3, round=3 (both agree)
    # 100k / 28_000 = 3.57 → floor=3, round=4 (diverges)
    df = pd.DataFrame({
        "buying_power": [28_000.0],
        "pnl_contract": [500.0],
    })
    eq_floor = compute_equity_curves(
        df, capital_spec=_spec(capital=100_000, lot_rounding_mode="floor"), years=1.0,
    )
    eq_round = compute_equity_curves(
        df, capital_spec=_spec(capital=100_000, lot_rounding_mode="round"), years=1.0,
    )
    assert list(eq_floor.lots_fixed) == [3]
    assert list(eq_round.lots_fixed) == [4]


# ── Sharpe ──────────────────────────────────────────────────────────────────


def test_sharpe_zero_when_std_is_zero():
    """All identical trades → std=0 → sharpe=0."""
    df = pd.DataFrame({
        "buying_power": [10_000.0] * 5,
        "pnl_contract": [500.0] * 5,  # identical → std=0
    })
    eq = compute_equity_curves(df, capital_spec=_spec(capital=100_000), years=1.0)
    assert eq.sharpe == 0.0


def test_sharpe_nonzero_when_std_is_nonzero():
    """Non-zero std with positive mean → non-zero sharpe."""
    df = pd.DataFrame({
        "buying_power": [10_000.0] * 5,
        "pnl_contract": [1_000.0, 500.0, 800.0, 400.0, 1_200.0],  # varied
    })
    eq = compute_equity_curves(df, capital_spec=_spec(capital=100_000), years=1.0)
    assert eq.sharpe != 0.0
    assert math.isfinite(eq.sharpe)


# ── deployment fraction ─────────────────────────────────────────────────────


def test_deployment_fraction_halves_lots():
    """deployment_fraction=0.5 with 100k capital + 10k BP → 5 lots, not 10."""
    df = pd.DataFrame({
        "buying_power": [10_000.0, 10_000.0, 10_000.0],
        "pnl_contract": [500.0, 500.0, 500.0],
    })
    full = compute_equity_curves(
        df, capital_spec=_spec(capital=100_000, deployment_fraction=1.0), years=1.0,
    )
    half = compute_equity_curves(
        df, capital_spec=_spec(capital=100_000, deployment_fraction=0.5), years=1.0,
    )
    assert list(full.lots_fixed) == [10, 10, 10]
    assert list(half.lots_fixed) == [5, 5, 5]
