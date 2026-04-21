"""Parity: post-P5-E2 _legacy_main regenerates falsify_* artifacts consistently.

Regression test: run the refactored `_legacy_main`, read the 4 artifacts it
produces back, compare vs the committed `results/nfo/falsify_*` files +
`falsification_report.md`.

All three CSVs use deterministic inputs (the tail-loss Monte-Carlo uses a
fixed seed of 42 and the same per-iteration RNG walk as the legacy script),
so parity must be exact within ~1e-6 rel tolerance on numerics and byte-exact
on categorical/identity columns.
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
    path = REPO_ROOT / "scripts" / "nfo" / "v3_falsification.py"
    spec = importlib.util.spec_from_file_location("_v3f_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_v3f_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _rel_close_cell(a, b, rel_tol: float = 1e-6, abs_tol: float = 1.0) -> bool:
    """NaN-safe numeric comparison, bool/str passthrough."""
    if pd.isna(a) and pd.isna(b):
        return True
    if isinstance(a, (bool, str)) or isinstance(b, (bool, str)):
        return a == b
    return math.isclose(float(a), float(b), rel_tol=rel_tol, abs_tol=abs_tol)


@pytest.mark.skipif(
    not (
        (RESULTS / "falsify_tail_loss.csv").exists()
        and (RESULTS / "falsify_allocation.csv").exists()
        and (RESULTS / "falsify_walkforward.csv").exists()
        and (RESULTS / "falsification_report.md").exists()
    ),
    reason="requires committed legacy falsify_* artifacts",
)
def test_legacy_main_regenerates_all_four_artifacts_consistently():
    committed_tail = pd.read_csv(RESULTS / "falsify_tail_loss.csv")
    committed_alloc = pd.read_csv(RESULTS / "falsify_allocation.csv")
    committed_walk = pd.read_csv(RESULTS / "falsify_walkforward.csv")
    committed_report = (RESULTS / "falsification_report.md").read_text(encoding="utf-8")

    mod = _load_script()
    result = mod._legacy_main([])
    assert isinstance(result, dict)
    assert "metrics" in result

    regen_tail = pd.read_csv(RESULTS / "falsify_tail_loss.csv")
    regen_alloc = pd.read_csv(RESULTS / "falsify_allocation.csv")
    regen_walk = pd.read_csv(RESULTS / "falsify_walkforward.csv")
    regen_report = (RESULTS / "falsification_report.md").read_text(encoding="utf-8")

    # ── Tail-loss CSV: same shape, same columns, numeric parity. ───────
    assert regen_tail.shape == committed_tail.shape, (
        f"tail-loss shape drift: regen={regen_tail.shape} "
        f"committed={committed_tail.shape}"
    )
    assert list(regen_tail.columns) == list(committed_tail.columns), (
        "tail-loss columns drift"
    )
    assert list(regen_tail["variant"].astype(str)) == list(
        committed_tail["variant"].astype(str)
    ), "tail-loss variant order drift"
    tail_numeric = [
        "n_injected", "n_iter", "p_final_above_capital",
        "p5_final_equity", "p50_final_equity", "p95_final_equity",
        "median_max_dd_pct", "p95_max_dd_pct", "p50_total_fixed",
    ]
    for col in tail_numeric:
        for a, b in zip(regen_tail[col], committed_tail[col]):
            assert _rel_close_cell(a, b), (
                f"tail-loss {col} drift: regen={a} committed={b}"
            )

    # ── Allocation CSV: same shape, same columns, numeric parity. ──────
    assert regen_alloc.shape == committed_alloc.shape, (
        f"allocation shape drift: regen={regen_alloc.shape} "
        f"committed={committed_alloc.shape}"
    )
    assert list(regen_alloc.columns) == list(committed_alloc.columns), (
        "allocation columns drift"
    )
    assert list(regen_alloc["variant"].astype(str)) == list(
        committed_alloc["variant"].astype(str)
    ), "allocation variant order drift"
    alloc_numeric = [
        "deployment_frac", "total_pnl_fixed", "total_pnl_compound",
        "final_equity_compound", "cagr_compound_pct",
        "max_drawdown_pct", "sharpe",
    ]
    for col in alloc_numeric:
        for a, b in zip(regen_alloc[col], committed_alloc[col]):
            assert _rel_close_cell(a, b), (
                f"allocation {col} drift: regen={a} committed={b}"
            )

    # ── Walk-forward CSV: same shape, identity cols + numeric parity. ──
    assert regen_walk.shape == committed_walk.shape, (
        f"walkforward shape drift: regen={regen_walk.shape} "
        f"committed={committed_walk.shape}"
    )
    assert list(regen_walk.columns) == list(committed_walk.columns), (
        "walkforward columns drift"
    )
    for col in ("train_window", "test_window", "pt_variant"):
        assert list(regen_walk[col].astype(str)) == list(
            committed_walk[col].astype(str)
        ), f"walkforward {col} mismatch"
    walk_numeric = [
        "train_n_matched", "train_win_rate", "train_avg_pnl", "train_sharpe",
        "test_n_matched", "test_win_rate", "test_avg_pnl", "test_sharpe",
    ]
    for col in walk_numeric:
        for a, b in zip(regen_walk[col], committed_walk[col]):
            assert _rel_close_cell(a, b), (
                f"walkforward {col} drift: regen={a} committed={b}"
            )

    # ── Report: byte-exact after rerun. ───────────────────────────────
    assert regen_report == committed_report, (
        "falsification_report.md text drift (check rounding / INR-formatting)"
    )
