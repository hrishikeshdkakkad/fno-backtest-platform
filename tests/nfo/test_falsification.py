"""Targeted tests for `scripts/nfo/v3_falsification.py` helpers.

The CLI itself hits real Dhan-cached data; unit coverage here focuses on
the pure tabular helpers: the tail-loss and allocation drivers, and the
walk-forward window-matching path. Synthetic inputs keep the tests
independent of `results/nfo/*` contents.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "nfo" / "v3_falsification.py"
)


@pytest.fixture(scope="module")
def v3f():
    """Load the falsification script as a module without executing its CLI."""
    import sys
    scripts_dir = str(_MODULE_PATH.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("_v3f", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_v3f"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def synthetic_matched() -> pd.DataFrame:
    """A minimal 'V3-matched' trade table: 3 trades, known PnL."""
    return pd.DataFrame({
        "expiry_date": ["2024-01-30", "2024-02-27", "2024-03-26"],
        "outcome": ["expired_worthless", "profit_take", "managed"],
        "buying_power": [10_000.0, 10_000.0, 10_000.0],
        "pnl_contract": [1_000.0, 500.0, -800.0],
        "gross_pnl_contract": [1_080.0, 580.0, -720.0],
        "txn_cost_contract": [80.0, 80.0, 80.0],
        "net_credit": [50.0, 40.0, 45.0],
    })


# ── Tail-loss runner ────────────────────────────────────────────────────────


def test_run_tail_loss_shapes(v3f, synthetic_matched: pd.DataFrame) -> None:
    """The CSV row shape should be (variants × injection_counts). Columns
    required by the markdown renderer must all be present.
    """
    matched = {"pt50": synthetic_matched, "hte": synthetic_matched}
    df = v3f.run_tail_loss_injection(
        matched, capital=100_000, years=1.0,
        injection_counts=[0, 1], n_iter=100, seed=42,
    )
    # 2 variants × 2 injection counts = 4 rows.
    assert len(df) == 4
    required = {
        "variant", "n_injected", "p_final_above_capital",
        "p5_final_equity", "p50_final_equity", "p95_final_equity",
        "median_max_dd_pct", "p95_max_dd_pct", "p50_total_fixed",
    }
    assert required.issubset(df.columns)


def test_tail_loss_zero_injection_matches_baseline_prob(v3f, synthetic_matched: pd.DataFrame) -> None:
    """With n_injected=0, the driver resamples the original trades only.
    On a synthetic sample that mixes wins and losses, P(final > capital)
    must fall strictly between 0 and 1 — not 1.0 by accident.
    """
    matched = {"hte": synthetic_matched}
    df = v3f.run_tail_loss_injection(
        matched, capital=100_000, years=1.0,
        injection_counts=[0], n_iter=500, seed=7,
    )
    p = float(df["p_final_above_capital"].iloc[0])
    assert 0.0 < p < 1.0


def test_tail_loss_monotone_in_injection_count(v3f, synthetic_matched: pd.DataFrame) -> None:
    """Adding more max-loss injections cannot improve survival probability.
    Some draws may be lucky, but the aggregate must not rise with k.
    """
    matched = {"hte": synthetic_matched}
    df = v3f.run_tail_loss_injection(
        matched, capital=100_000, years=1.0,
        injection_counts=[0, 1, 2, 3], n_iter=500, seed=11,
    )
    ps = df.sort_values("n_injected")["p_final_above_capital"].tolist()
    for a, b in zip(ps, ps[1:]):
        assert b <= a + 1e-9, (
            f"survival probability should not increase with injections: {ps}"
        )


# ── Allocation sweep ────────────────────────────────────────────────────────


def test_run_allocation_sweep_linear_fixed_pnl(v3f, synthetic_matched: pd.DataFrame) -> None:
    """Non-compounding total P&L scales linearly with deployment_frac
    (integer-lot rounding aside). 50 % should be ~half of 100 %.
    """
    matched = {"pt50": synthetic_matched}
    df = v3f.run_allocation_sweep(
        matched, capital=100_000, years=1.0,
        deployment_fracs=[0.5, 1.0],
    )
    half = df[df["deployment_frac"] == 0.5]["total_pnl_fixed"].iloc[0]
    full = df[df["deployment_frac"] == 1.0]["total_pnl_fixed"].iloc[0]
    # Allow a ±1 lot tolerance from flooring on the smaller budget.
    ratio = half / full
    assert 0.45 < ratio < 0.55


# ── Walk-forward window plumbing ────────────────────────────────────────────


def test_walk_forward_window_produces_row_per_window(v3f, monkeypatch) -> None:
    """`run_walk_forward` must emit exactly one row per (window, variant),
    regardless of whether V3 fires. We stub the V3 firing function so the
    test stays independent of `results/nfo/historical_signals.parquet`.
    """
    # Build tiny stand-ins that the real function will consume.
    signals_df = pd.DataFrame({
        "date": pd.to_datetime(["2024-02-20", "2024-03-20"]),
        "target_expiry": ["2024-03-26", "2024-04-30"],
    })
    trades = pd.DataFrame({
        "entry_date": ["2024-02-20", "2024-03-20"],
        "expiry_date": ["2024-03-26", "2024-04-30"],
        "param_delta": [0.30, 0.30],
        "param_width": [100.0, 100.0],
        "param_pt": [0.5, 0.5],
        "pnl_contract": [1_000.0, -500.0],
        "buying_power": [10_000.0, 10_000.0],
    })
    windows = [
        ("2024-01-01", "2024-02-29", "2024-03-01", "2024-04-30"),
    ]

    # Monkey-patch the imported redesign_variants module's symbols so
    # `get_firing_dates` always says "fire on every session". That exercises
    # the downstream grouping/matching logic without needing the real ATR
    # cache or signal parquet.
    import sys
    scripts_dir = str(_MODULE_PATH.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import redesign_variants as rv

    def fake_get_firing_dates(variant, df, atr):
        return [(d.date(), {}) for d in df["date"]]

    monkeypatch.setattr(rv, "get_firing_dates", fake_get_firing_dates)
    monkeypatch.setattr(
        rv, "load_nifty_atr",
        lambda s: pd.Series([100.0] * len(s), index=pd.to_datetime(s.values)),
    )

    df = v3f.run_walk_forward(signals_df, trades, windows=windows, pt_variant="pt50")
    assert len(df) == 1
    row = df.iloc[0]
    # Train window (Jan–Feb) contains the Feb-20 firing → 1 matched trade.
    assert row["train_n_matched"] == 1
    # Test window (Mar–Apr) contains the Mar-20 firing → 1 matched trade.
    assert row["test_n_matched"] == 1
    assert row["pt_variant"] == "pt50"


def test_walk_forward_returns_empty_when_no_fires(v3f, monkeypatch) -> None:
    """If V3 never fires, every row still has the window metadata and
    n_matched == 0 for both train and test."""
    signals_df = pd.DataFrame({
        "date": pd.to_datetime(["2024-02-20"]),
        "target_expiry": ["2024-03-26"],
    })
    trades = pd.DataFrame(columns=[
        "entry_date", "expiry_date", "param_delta", "param_width",
        "param_pt", "pnl_contract", "buying_power",
    ])
    windows = [("2024-01-01", "2024-02-29", "2024-03-01", "2024-04-30")]

    import sys
    scripts_dir = str(_MODULE_PATH.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import redesign_variants as rv
    monkeypatch.setattr(rv, "get_firing_dates", lambda *a, **k: [])
    monkeypatch.setattr(
        rv, "load_nifty_atr",
        lambda s: pd.Series([100.0] * len(s), index=pd.to_datetime(s.values)),
    )

    df = v3f.run_walk_forward(signals_df, trades, windows=windows, pt_variant="hte")
    assert len(df) == 1
    assert int(df.iloc[0]["train_n_matched"]) == 0
    assert int(df.iloc[0]["test_n_matched"]) == 0
    # Summary stats are None when matched set is empty.
    assert df.iloc[0]["train_sharpe"] is None
    assert df.iloc[0]["test_sharpe"] is None
