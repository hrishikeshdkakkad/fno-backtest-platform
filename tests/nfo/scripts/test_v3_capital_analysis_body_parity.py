"""Parity: post-P5-B2 _legacy_main regenerates v3_capital_trades_hte.csv consistently.

This is a regression test: run the refactored _legacy_main, read the CSV it
produced, compare vs the committed `results/nfo/v3_capital_trades_hte.csv`.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _restore_real_registry(monkeypatch):
    from nfo.specs import loader
    monkeypatch.setattr(
        loader, "_REGISTRY_PATH",
        REPO_ROOT / "configs" / "nfo" / ".registry.json",
        raising=True,
    )


def _load_script():
    path = REPO_ROOT / "scripts" / "nfo" / "v3_capital_analysis.py"
    spec = importlib.util.spec_from_file_location("_v3ca_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_v3ca_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(
    not (REPO_ROOT / "results" / "nfo" / "v3_capital_trades_hte.csv").exists(),
    reason="requires committed legacy CSV",
)
def test_legacy_main_regenerates_hte_csv_consistently():
    committed = pd.read_csv(REPO_ROOT / "results" / "nfo" / "v3_capital_trades_hte.csv")
    mod = _load_script()
    result = mod._legacy_main(["--pt-variant", "hte"])
    assert isinstance(result, dict)
    assert "metrics" in result

    # After _legacy_main, the hte CSV is regenerated
    regen = pd.read_csv(REPO_ROOT / "results" / "nfo" / "v3_capital_trades_hte.csv")

    # Legacy column schema must be present (subset of regenerated columns OK).
    legacy_cols = [
        "v3_first_fire", "entry_date", "expiry", "trade_found", "outcome",
        "bp_per_lot", "pnl_per_lot", "lots_fixed", "pnl_fixed",
        "lots_compound", "pnl_compound", "equity_after_compound",
    ]
    for col in legacy_cols:
        assert col in regen.columns, f"missing legacy column {col!r}"

    # Same number of rows + ordering (cycles identified identically).
    assert len(regen) == len(committed), (
        f"row count mismatch: regen={len(regen)} committed={len(committed)}"
    )

    # Byte-exact identity columns.
    for col in ("v3_first_fire", "entry_date", "expiry", "outcome"):
        assert list(regen[col].astype(str)) == list(committed[col].astype(str)), (
            f"mismatch on {col}"
        )

    # Numeric columns within 1 INR — legacy rounds to 0 dp on pnl_fixed/compound.
    numeric_cols = [
        "bp_per_lot", "pnl_per_lot",
        "lots_fixed", "pnl_fixed",
        "lots_compound", "pnl_compound", "equity_after_compound",
    ]
    for col in numeric_cols:
        for a, b in zip(regen[col], committed[col]):
            if pd.isna(a) and pd.isna(b):
                continue
            assert math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1.0), (
                f"{col} drift: regen={a} committed={b}"
            )


@pytest.mark.skipif(
    not (REPO_ROOT / "results" / "nfo" / "v3_capital_trades_pt50.csv").exists(),
    reason="requires committed legacy pt50 CSV",
)
def test_legacy_main_regenerates_pt50_csv_consistently():
    committed = pd.read_csv(REPO_ROOT / "results" / "nfo" / "v3_capital_trades_pt50.csv")
    mod = _load_script()
    result = mod._legacy_main(["--pt-variant", "pt50"])
    assert isinstance(result, dict)

    regen = pd.read_csv(REPO_ROOT / "results" / "nfo" / "v3_capital_trades_pt50.csv")

    assert len(regen) == len(committed), (
        f"row count mismatch: regen={len(regen)} committed={len(committed)}"
    )

    for col in ("v3_first_fire", "entry_date", "expiry", "outcome"):
        assert list(regen[col].astype(str)) == list(committed[col].astype(str)), (
            f"mismatch on {col}"
        )

    for col in ("bp_per_lot", "pnl_per_lot", "lots_fixed", "pnl_fixed",
                "lots_compound", "pnl_compound", "equity_after_compound"):
        for a, b in zip(regen[col], committed[col]):
            if pd.isna(a) and pd.isna(b):
                continue
            assert math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1.0), (
                f"{col} drift: regen={a} committed={b}"
            )
