"""Parity: post-P5 _legacy_main regenerates v3_live_trades_hte.csv consistently.

This is a regression test: run the refactored _legacy_main, read the CSV it
produced, compare vs the committed `results/nfo/v3_live_trades_hte.csv`.
"""
from __future__ import annotations

import importlib.util
import sys
import math
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
    path = REPO_ROOT / "scripts" / "nfo" / "v3_live_rule_backtest.py"
    spec = importlib.util.spec_from_file_location("_v3lrb_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_v3lrb_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(
    not (REPO_ROOT / "results" / "nfo" / "v3_live_trades_hte.csv").exists(),
    reason="requires committed legacy CSV",
)
def test_legacy_main_regenerates_hte_csv_consistently():
    committed = pd.read_csv(REPO_ROOT / "results" / "nfo" / "v3_live_trades_hte.csv")
    mod = _load_script()
    result = mod._legacy_main()
    assert isinstance(result, dict)

    # After _legacy_main, the hte CSV is regenerated
    regen = pd.read_csv(REPO_ROOT / "results" / "nfo" / "v3_live_trades_hte.csv")

    # Key columns must match within tolerance
    for col in ("entry_date", "expiry_date", "outcome"):
        assert list(regen[col]) == list(committed[col]), f"mismatch on {col}"

    for a, b in zip(regen["pnl_contract"], committed["pnl_contract"]):
        assert math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6), f"pnl_contract drift: {a} vs {b}"
