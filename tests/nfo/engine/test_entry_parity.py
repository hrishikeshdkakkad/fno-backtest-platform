"""Parity: engine.entry.resolve_entry_date matches legacy _first_session_on_or_after.

Legacy function lives in scripts/nfo/v3_live_rule_backtest.py. It takes a target
date and a spot_daily DataFrame, returns the next NSE session date or None.
Our engine function is spec-driven; for live_rule mode, behavior should match.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from nfo.engine.entry import resolve_entry_date
from nfo.specs.loader import load_strategy, reset_registry_for_tests


REPO_ROOT = Path(__file__).resolve().parents[3]
SIGNALS = REPO_ROOT / "results" / "nfo" / "historical_signals.parquet"


def _import_legacy_v3lrb():
    path = REPO_ROOT / "scripts" / "nfo" / "v3_live_rule_backtest.py"
    spec = importlib.util.spec_from_file_location("_legacy_v3lrb_entry", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_legacy_v3lrb_entry"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def _iso_registry(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")


@pytest.mark.skipif(not SIGNALS.exists(), reason="requires cached historical_signals.parquet")
def test_live_rule_entry_matches_legacy(_iso_registry):
    strat_path = REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_live_rule.yaml"
    spec, _ = load_strategy(strat_path)

    df = pd.read_parquet(SIGNALS)
    df["date"] = pd.to_datetime(df["date"])
    sessions = [d.date() for d in pd.to_datetime(df["date"]).sort_values()]

    # Sample targets across the historical window
    targets = [
        date(2024, 3, 1),    # session
        date(2024, 3, 2),    # Saturday → next Mon
        date(2024, 3, 3),    # Sunday   → next Mon
        date(2024, 12, 25),  # holiday  → next session
        date(2025, 1, 1),    # holiday  → next session
        date(2025, 4, 10),   # session
        date(2026, 5, 1),    # far future (may be past cache)
    ]

    legacy = _import_legacy_v3lrb()
    # Build a spot_daily frame the legacy helper expects: columns ["date", ...]
    spot_daily = df[["date"]].copy()

    for tgt in targets:
        engine_out = resolve_entry_date(
            spec=spec, first_fire_date=tgt, sessions=sessions,
        )
        legacy_out = legacy._first_session_on_or_after(tgt, spot_daily)
        assert engine_out == legacy_out, (
            f"target={tgt}: engine={engine_out}, legacy={legacy_out}"
        )
