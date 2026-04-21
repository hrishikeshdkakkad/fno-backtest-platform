"""Tests for the V3 robustness helpers (`src/nfo/robustness.py`)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nfo import robustness


@pytest.fixture
def synthetic_trades() -> pd.DataFrame:
    """4 trades with fixed buying power and known P&L."""
    return pd.DataFrame({
        "expiry_date": ["2024-01-30", "2024-02-27", "2024-03-26", "2024-04-30"],
        "outcome": ["expired_worthless", "profit_take", "managed", "expired_worthless"],
        "buying_power": [10_000.0, 10_000.0, 10_000.0, 10_000.0],
        "pnl_contract": [1_000.0, 500.0, -800.0, 1_200.0],
        "gross_pnl_contract": [1_080.0, 600.0, -720.0, 1_280.0],
        "txn_cost_contract": [80.0, 100.0, 80.0, 80.0],
    })


# ── apply_slippage ───────────────────────────────────────────────────────────


def test_apply_slippage_subtracts_flat_rupees(synthetic_trades: pd.DataFrame) -> None:
    out = robustness.apply_slippage(synthetic_trades, 100.0)
    assert (out["pnl_contract"] == synthetic_trades["pnl_contract"] - 100.0).all()


def test_apply_slippage_bumps_cost_column(synthetic_trades: pd.DataFrame) -> None:
    out = robustness.apply_slippage(synthetic_trades, 100.0)
    assert (out["txn_cost_contract"] == synthetic_trades["txn_cost_contract"] + 100.0).all()


def test_apply_slippage_rejects_negative() -> None:
    df = pd.DataFrame({"pnl_contract": [0.0], "buying_power": [1.0]})
    with pytest.raises(ValueError):
        robustness.apply_slippage(df, -50.0)


def test_apply_slippage_leaves_gross_untouched(synthetic_trades: pd.DataFrame) -> None:
    out = robustness.apply_slippage(synthetic_trades, 500.0)
    assert (out["gross_pnl_contract"] == synthetic_trades["gross_pnl_contract"]).all()


# ── compute_equity_curves ────────────────────────────────────────────────────


def test_equity_curves_known_values(synthetic_trades: pd.DataFrame) -> None:
    # Capital = ₹100k, BP per lot = ₹10k → 10 lots fixed size.
    # Per-trade fixed P&L: [10,000, 5,000, -8,000, 12,000]; total = ₹19,000.
    eq = robustness.compute_equity_curves(synthetic_trades, capital=100_000, years=1.0)
    assert list(eq.lots_fixed) == [10, 10, 10, 10]
    assert list(eq.pnl_fixed) == [10_000, 5_000, -8_000, 12_000]
    assert eq.total_pnl_fixed == pytest.approx(19_000)
    # Compound walk:
    #  start 100k, +10k → 110k (11 lots)
    #  110k + 11*500=5,500 → 115.5k (11 lots; 115.5//10 = 11)
    #  115.5k + 11*(-800)=-8,800 → 106.7k (10 lots)
    #  106.7k + 10*1,200=12,000 → 118.7k
    assert eq.final_equity_compound == pytest.approx(118_700)
    assert eq.total_pnl_compound == pytest.approx(18_700)
    # Max drawdown: peak 115.5k, trough 106.7k → 7.62%
    assert eq.max_drawdown_pct == pytest.approx(7.6190, abs=1e-3)


def test_equity_curves_empty_is_safe() -> None:
    empty = pd.DataFrame(columns=["buying_power", "pnl_contract"])
    eq = robustness.compute_equity_curves(empty, capital=100_000, years=1.0)
    assert eq.total_pnl_fixed == 0.0
    assert eq.final_equity_compound == 100_000


def test_equity_curves_zero_bp_is_safe() -> None:
    df = pd.DataFrame({"buying_power": [0.0], "pnl_contract": [999.0]})
    eq = robustness.compute_equity_curves(df, capital=100_000, years=1.0)
    # No valid lot sizing possible → 0 lots → 0 P&L.
    assert list(eq.lots_fixed) == [0]
    assert eq.total_pnl_fixed == 0.0


# ── leave_one_out ────────────────────────────────────────────────────────────


def test_leave_one_out_drops_each_row(synthetic_trades: pd.DataFrame) -> None:
    rows = robustness.leave_one_out(synthetic_trades, capital=100_000, years=1.0)
    assert len(rows) == len(synthetic_trades)
    # Each LooRow's summary should reflect n-1 trades.
    assert all(r.summary.n == len(synthetic_trades) - 1 for r in rows)


def test_leave_one_out_flips_win_rate_when_dropping_loss(
    synthetic_trades: pd.DataFrame,
) -> None:
    # Drop the only loss (index 2) → remaining win rate must be 100%.
    rows = robustness.leave_one_out(synthetic_trades, capital=100_000, years=1.0)
    dropped_loss_row = next(r for r in rows if r.dropped_index == 2)
    assert dropped_loss_row.summary.win_rate == pytest.approx(1.0)


# ── block_bootstrap ──────────────────────────────────────────────────────────


def test_bootstrap_reproducible_with_seed(synthetic_trades: pd.DataFrame) -> None:
    a = robustness.block_bootstrap(
        synthetic_trades, capital=100_000, years=1.0, n_iter=500, seed=42,
    )
    b = robustness.block_bootstrap(
        synthetic_trades, capital=100_000, years=1.0, n_iter=500, seed=42,
    )
    np.testing.assert_array_equal(a.total_pnl_fixed, b.total_pnl_fixed)
    np.testing.assert_array_equal(a.max_drawdown_pct, b.max_drawdown_pct)


def test_bootstrap_different_seeds_differ(synthetic_trades: pd.DataFrame) -> None:
    a = robustness.block_bootstrap(
        synthetic_trades, capital=100_000, years=1.0, n_iter=500, seed=42,
    )
    b = robustness.block_bootstrap(
        synthetic_trades, capital=100_000, years=1.0, n_iter=500, seed=123,
    )
    assert not np.array_equal(a.total_pnl_fixed, b.total_pnl_fixed)


def test_bootstrap_percentiles_shape(synthetic_trades: pd.DataFrame) -> None:
    result = robustness.block_bootstrap(
        synthetic_trades, capital=100_000, years=1.0, n_iter=1_000, seed=0,
    )
    pct = result.percentiles(ps=(5, 50, 95))
    assert list(pct["percentile"]) == [5, 50, 95]
    assert set(pct.columns) == {
        "percentile",
        "total_pnl_fixed",
        "total_pnl_compound",
        "final_equity_compound",
        "cagr_compound_pct",
        "max_drawdown_pct",
    }
    # P5 < P50 < P95 monotonicity on final_equity_compound.
    equity_vals = pct.sort_values("percentile")["final_equity_compound"].tolist()
    assert equity_vals[0] <= equity_vals[1] <= equity_vals[2]


def test_bootstrap_empty_returns_empty() -> None:
    empty = pd.DataFrame(columns=["buying_power", "pnl_contract"])
    result = robustness.block_bootstrap(empty, capital=100_000, years=1.0, n_iter=10)
    assert result.n_iter == 0
    assert result.total_pnl_fixed.size == 0


def test_bootstrap_compound_and_fixed_prob_diverge() -> None:
    """Fixed-size total P&L can be positive while compound final equity is
    below starting capital — a draw that makes money early then loses it
    after equity has grown builds magnified losses. The two probabilities
    should be computed separately and reported as such.
    """
    # Two trades: big early win grows lots, then a loss at amplified size.
    df = pd.DataFrame({
        "buying_power": [10_000.0, 10_000.0],
        "pnl_contract": [5_000.0, -4_500.0],
    })
    # 100k / 10k = 10 lots. First trade: +50k → 150k. Second: 15 lots × -4,500
    # = -67,500 → final 82,500 (below 100k). Fixed: 10 × 5,000 - 10 × 4,500 =
    # +5,000. Bootstrap resamples this pair; the "HL" ordering draws the win
    # then the loss (compound below start), the "LH" ordering draws the loss
    # then the win (compound above start).
    result = robustness.block_bootstrap(
        df, capital=100_000, years=1.0, n_iter=2_000, seed=7,
    )
    p_fixed = result.prob_positive_fixed()
    p_compound = result.prob_positive_compound()
    # Two resampled "HH" draws always win; "LL" always lose; "HL"/"LH" differ.
    # We only need to assert the compound probability is strictly lower than
    # the fixed probability — proving the divergence exists so the report
    # must not mix them.
    assert p_fixed > p_compound, (
        f"expected prob_positive_fixed {p_fixed} > prob_positive_compound {p_compound}"
    )


def test_loo_row_exposes_capital_sharpe(synthetic_trades: pd.DataFrame) -> None:
    rows = robustness.leave_one_out(synthetic_trades, capital=100_000, years=1.0)
    # Both the per-lot summary Sharpe and the capital-aware equity Sharpe are
    # exposed on each row; they use different annualisation conventions and
    # should diverge when lot-sizing varies across trades.
    for row in rows:
        assert hasattr(row, "equity_sharpe")
        assert hasattr(row.summary, "sharpe")


# ── synthetic_max_loss_row / inject_tail_losses ─────────────────────────────


def test_synthetic_max_loss_row_uses_width_minus_credit() -> None:
    tmpl = pd.Series({
        "net_credit": 50.0,
        "buying_power": 8_500.0,
        "pnl_contract": 1_000.0,
        "gross_pnl_contract": 1_080.0,
        "txn_cost_contract": 80.0,
        "outcome": "expired_worthless",
    })
    out = robustness.synthetic_max_loss_row(tmpl, width=100.0)
    # Max-loss per-share = net_credit − width = 50 − 100 = −50.
    # Per-contract gross = −50 × 65 = −3,250. Net = gross − cost (80) = −3,330.
    assert out["pnl_per_share"] == pytest.approx(-50.0)
    assert out["gross_pnl_contract"] == pytest.approx(-3_250.0)
    assert out["pnl_contract"] == pytest.approx(-3_330.0)
    assert out["outcome"] == "max_loss"
    assert out["synthetic_max_loss"] is True
    # Buying power is preserved — the same margin was held to enter.
    assert out["buying_power"] == tmpl["buying_power"]


def test_inject_tail_losses_zero_is_idempotent_but_annotates() -> None:
    df = pd.DataFrame({
        "net_credit": [50.0, 40.0],
        "buying_power": [8_500.0, 8_500.0],
        "pnl_contract": [1_000.0, 500.0],
        "gross_pnl_contract": [1_080.0, 580.0],
        "txn_cost_contract": [80.0, 80.0],
        "outcome": ["expired_worthless", "profit_take"],
    })
    rng = np.random.default_rng(0)
    out = robustness.inject_tail_losses(df, n_injections=0, rng=rng)
    assert (out["pnl_contract"] == df["pnl_contract"]).all()
    # The `synthetic_max_loss` column is added so downstream code can group
    # by it; zero-injection case fills it with False.
    assert (out["synthetic_max_loss"] == False).all()


def test_inject_tail_losses_replaces_requested_count() -> None:
    df = pd.DataFrame({
        "net_credit": [50.0] * 5,
        "buying_power": [8_500.0] * 5,
        "pnl_contract": [1_000.0] * 5,
        "gross_pnl_contract": [1_080.0] * 5,
        "txn_cost_contract": [80.0] * 5,
        "outcome": ["expired_worthless"] * 5,
    })
    rng = np.random.default_rng(7)
    out = robustness.inject_tail_losses(df, n_injections=2, rng=rng)
    assert int(out["synthetic_max_loss"].sum()) == 2


def test_inject_tail_losses_rng_is_deterministic() -> None:
    df = pd.DataFrame({
        "net_credit": [50.0] * 5,
        "buying_power": [8_500.0] * 5,
        "pnl_contract": [1_000.0] * 5,
        "gross_pnl_contract": [1_080.0] * 5,
        "txn_cost_contract": [80.0] * 5,
        "outcome": ["expired_worthless"] * 5,
    })
    a = robustness.inject_tail_losses(df, n_injections=2, rng=np.random.default_rng(42))
    b = robustness.inject_tail_losses(df, n_injections=2, rng=np.random.default_rng(42))
    np.testing.assert_array_equal(
        a["synthetic_max_loss"].values, b["synthetic_max_loss"].values,
    )


def test_inject_tail_losses_rejects_negative() -> None:
    df = pd.DataFrame({"net_credit": [50.0], "buying_power": [1.0],
                       "pnl_contract": [1.0], "gross_pnl_contract": [1.0],
                       "txn_cost_contract": [0.0], "outcome": ["x"]})
    with pytest.raises(ValueError):
        robustness.inject_tail_losses(df, n_injections=-1, rng=np.random.default_rng())


def test_inject_tail_losses_caps_at_sample_size() -> None:
    """Requesting more injections than rows should replace every row."""
    df = pd.DataFrame({
        "net_credit": [50.0] * 3,
        "buying_power": [8_500.0] * 3,
        "pnl_contract": [1_000.0] * 3,
        "gross_pnl_contract": [1_080.0] * 3,
        "txn_cost_contract": [80.0] * 3,
        "outcome": ["x"] * 3,
    })
    rng = np.random.default_rng(0)
    out = robustness.inject_tail_losses(df, n_injections=10, rng=rng)
    assert int(out["synthetic_max_loss"].sum()) == 3


# ── compute_equity_curves deployment_frac ────────────────────────────────────


def test_deployment_frac_scales_lots_linearly(synthetic_trades: pd.DataFrame) -> None:
    full = robustness.compute_equity_curves(
        synthetic_trades, capital=100_000, years=1.0, deployment_frac=1.0,
    )
    half = robustness.compute_equity_curves(
        synthetic_trades, capital=100_000, years=1.0, deployment_frac=0.5,
    )
    assert list(half.lots_fixed) == [5, 5, 5, 5]
    # Full deploy should carry twice the lots under non-compounding.
    assert list(full.lots_fixed) == [10, 10, 10, 10]


def test_deployment_frac_rejects_out_of_range(synthetic_trades: pd.DataFrame) -> None:
    with pytest.raises(ValueError):
        robustness.compute_equity_curves(
            synthetic_trades, capital=100_000, years=1.0, deployment_frac=0.0,
        )
    with pytest.raises(ValueError):
        robustness.compute_equity_curves(
            synthetic_trades, capital=100_000, years=1.0, deployment_frac=1.5,
        )


# ── equity-curve bankruptcy clamp ───────────────────────────────────────────


def test_equity_curve_clamps_drawdown_at_100_pct() -> None:
    """A trade big enough to blow the account out must register as 100 % DD,
    not >100 %. Subsequent cycles must not take negative positions."""
    # BP 10k → 10 lots. First trade pnl = −20_000 per lot → −200k total,
    # equity drops from +100k to −100k. Clamp says max DD = 100 %, lots on
    # the second trade = 0 (no negative sizing).
    df = pd.DataFrame({
        "buying_power": [10_000.0, 10_000.0],
        "pnl_contract": [-20_000.0, 5_000.0],
    })
    eq = robustness.compute_equity_curves(df, capital=100_000, years=1.0)
    assert eq.max_drawdown_pct == pytest.approx(100.0)
    assert list(eq.lots_compound) == [10, 0]


def test_equity_curve_bootstrap_never_goes_below_100_pct_dd(
    synthetic_trades: pd.DataFrame,
) -> None:
    """Bootstrap with tail losses can push equity negative. The result must
    still report finite max_drawdown_pct ≤ 100 %."""
    rng = np.random.default_rng(1)
    injected = robustness.inject_tail_losses(synthetic_trades, n_injections=3, rng=rng)
    result = robustness.block_bootstrap(
        injected, capital=100_000, years=1.0, n_iter=200, seed=2,
    )
    assert (result.max_drawdown_pct <= 100.0 + 1e-9).all()


# ── pick_trade_for_expiry ────────────────────────────────────────────────────


def test_pick_trade_prefers_pt50_variant() -> None:
    df = pd.DataFrame({
        "param_delta": [0.30, 0.30],
        "param_width": [100.0, 100.0],
        "expiry_date": ["2024-05-30", "2024-05-30"],
        "param_pt": [1.00, 0.50],
        "pnl_contract": [500.0, 250.0],
    })
    row = robustness.pick_trade_for_expiry(df, "2024-05-30", "pt50")
    assert row is not None
    assert row["param_pt"] == 0.50


def test_pick_trade_prefers_hte_variant() -> None:
    df = pd.DataFrame({
        "param_delta": [0.30, 0.30],
        "param_width": [100.0, 100.0],
        "expiry_date": ["2024-05-30", "2024-05-30"],
        "param_pt": [1.00, 0.50],
        "pnl_contract": [500.0, 250.0],
    })
    row = robustness.pick_trade_for_expiry(df, "2024-05-30", "hte")
    assert row is not None
    assert row["param_pt"] == 1.00


def test_pick_trade_rejects_bad_variant() -> None:
    df = pd.DataFrame({
        "param_delta": [0.30], "param_width": [100.0],
        "expiry_date": ["2024-05-30"], "param_pt": [0.50],
    })
    with pytest.raises(ValueError):
        robustness.pick_trade_for_expiry(df, "2024-05-30", "nonsense")


def test_pick_trade_returns_none_when_no_match() -> None:
    df = pd.DataFrame({
        "param_delta": [0.25], "param_width": [100.0],
        "expiry_date": ["2024-05-30"], "param_pt": [0.50],
    })
    assert robustness.pick_trade_for_expiry(df, "2024-05-30", "pt50") is None
