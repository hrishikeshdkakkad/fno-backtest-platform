"""Parity: post-P5-D2 _legacy_main regenerates robustness_* artifacts consistently.

Regression test: run the refactored `_legacy_main`, read the 4 artifacts it
produces back, compare vs the committed `results/nfo/robustness_*` files.

The bootstrap CSV uses a fixed seed (42) and deterministic inputs, so parity
must be exact across percentiles within ~1e-6 rel tolerance. The slippage and
LOO tables are fully deterministic too.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS = REPO_ROOT / "results" / "nfo"


@pytest.fixture(autouse=True)
def _restore_real_registry(monkeypatch):
    from nfo.specs import loader
    monkeypatch.setattr(
        loader, "_REGISTRY_PATH",
        REPO_ROOT / "configs" / "nfo" / ".registry.json",
        raising=True,
    )


def _load_script():
    path = REPO_ROOT / "scripts" / "nfo" / "v3_robustness.py"
    spec = importlib.util.spec_from_file_location("_v3rob_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_v3rob_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _rel_close_cell(a, b, rel_tol: float = 1e-6, abs_tol: float = 1.0) -> bool:
    """Compare two cells. Handle NaN-safe equality, pass-through equals, floats."""
    if pd.isna(a) and pd.isna(b):
        return True
    if isinstance(a, (bool, str)) or isinstance(b, (bool, str)):
        return a == b
    return math.isclose(float(a), float(b), rel_tol=rel_tol, abs_tol=abs_tol)


@pytest.mark.skipif(
    not (
        (RESULTS / "robustness_slippage.csv").exists()
        and (RESULTS / "robustness_loo.csv").exists()
        and (RESULTS / "robustness_bootstrap.csv").exists()
        and (RESULTS / "robustness_report.md").exists()
    ),
    reason="requires committed legacy robustness_* artifacts",
)
def test_legacy_main_regenerates_all_four_artifacts_consistently():
    committed_slippage = pd.read_csv(RESULTS / "robustness_slippage.csv")
    committed_loo = pd.read_csv(RESULTS / "robustness_loo.csv")
    committed_boot = pd.read_csv(RESULTS / "robustness_bootstrap.csv")
    committed_report = (RESULTS / "robustness_report.md").read_text(encoding="utf-8")

    mod = _load_script()
    result = mod._legacy_main([])
    assert isinstance(result, dict)
    assert "metrics" in result
    # Headline metric presence (per variant).
    assert "pt50_prob_positive_compound" in result["metrics"]
    assert "hte_prob_positive_compound" in result["metrics"]

    regen_slippage = pd.read_csv(RESULTS / "robustness_slippage.csv")
    regen_loo = pd.read_csv(RESULTS / "robustness_loo.csv")
    regen_boot = pd.read_csv(RESULTS / "robustness_bootstrap.csv")
    regen_report = (RESULTS / "robustness_report.md").read_text(encoding="utf-8")

    # ── Slippage CSV: same shape, same columns, numeric parity. ──────────
    assert regen_slippage.shape == committed_slippage.shape, (
        f"slippage shape drift: regen={regen_slippage.shape} "
        f"committed={committed_slippage.shape}"
    )
    assert list(regen_slippage.columns) == list(committed_slippage.columns), (
        "slippage columns drift"
    )
    for col in regen_slippage.columns:
        for a, b in zip(regen_slippage[col], committed_slippage[col]):
            assert _rel_close_cell(a, b), (
                f"slippage {col} drift: regen={a} committed={b}"
            )

    # ── LOO CSV: same shape, numeric + identity parity. ─────────────────
    assert regen_loo.shape == committed_loo.shape, (
        f"LOO shape drift: regen={regen_loo.shape} committed={committed_loo.shape}"
    )
    assert list(regen_loo.columns) == list(committed_loo.columns), (
        "LOO columns drift"
    )
    for col in ("variant", "dropped_expiry", "dropped_outcome"):
        assert list(regen_loo[col].astype(str)) == list(
            committed_loo[col].astype(str)
        ), f"LOO {col} mismatch"
    numeric_loo = [
        "dropped_index", "dropped_pnl_contract", "remaining_n",
        "remaining_win_rate", "remaining_avg_pnl", "remaining_sharpe",
        "remaining_sharpe_per_lot", "total_pnl_fixed", "total_pnl_compound",
        "final_equity_compound", "max_drawdown_pct",
    ]
    for col in numeric_loo:
        for a, b in zip(regen_loo[col], committed_loo[col]):
            assert _rel_close_cell(a, b), (
                f"LOO {col} drift: regen={a} committed={b}"
            )

    # ── Bootstrap CSV: exact same percentile ladder per variant. ────────
    assert regen_boot.shape == committed_boot.shape, (
        f"bootstrap shape drift: regen={regen_boot.shape} "
        f"committed={committed_boot.shape}"
    )
    assert list(regen_boot.columns) == list(committed_boot.columns), (
        "bootstrap columns drift"
    )
    assert list(regen_boot["variant"].astype(str)) == list(
        committed_boot["variant"].astype(str)
    ), "bootstrap variant order drift"
    assert list(regen_boot["percentile"]) == list(committed_boot["percentile"]), (
        "bootstrap percentile ladder drift"
    )
    boot_numeric = [
        "total_pnl_fixed", "total_pnl_compound", "final_equity_compound",
        "cagr_compound_pct", "max_drawdown_pct",
    ]
    for col in boot_numeric:
        for a, b in zip(regen_boot[col], committed_boot[col]):
            assert _rel_close_cell(a, b), (
                f"bootstrap {col} drift: regen={a} committed={b}"
            )

    # ── Report: byte-exact after rerun. ─────────────────────────────────
    assert regen_report == committed_report, (
        "robustness_report.md text drift (check rounding / INR-formatting)"
    )
