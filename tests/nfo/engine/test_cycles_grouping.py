"""Tests for engine.cycles.group_fires_by_cycle (master design §6, §12)."""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from nfo.engine.cycles import CycleFires, group_fires_by_cycle
from nfo.engine.triggers import TriggerEvaluator
from nfo.specs.loader import load_strategy, reset_registry_for_tests


REPO_ROOT = Path(__file__).resolve().parents[3]
SIGNALS = REPO_ROOT / "results" / "nfo" / "historical_signals.parquet"


def test_group_fires_by_cycle_basic():
    features = pd.DataFrame([
        {"date": pd.Timestamp("2025-03-24"), "target_expiry": "2025-04-24"},
        {"date": pd.Timestamp("2025-03-25"), "target_expiry": "2025-04-24"},
        {"date": pd.Timestamp("2025-04-10"), "target_expiry": "2025-05-29"},
    ])
    fires = [
        (date(2025, 3, 24), {}),
        (date(2025, 3, 25), {}),
        (date(2025, 4, 10), {}),
    ]
    cycles = group_fires_by_cycle(
        fires, features, underlying="NIFTY", strategy_version="3.0.0"
    )
    assert len(cycles) == 2
    april = cycles["NIFTY:2025-04-24:3.0.0"]
    assert april.first_fire_date == date(2025, 3, 24)
    assert april.target_expiry == date(2025, 4, 24)
    assert april.fire_dates == [date(2025, 3, 24), date(2025, 3, 25)]
    may = cycles["NIFTY:2025-05-29:3.0.0"]
    assert may.first_fire_date == date(2025, 4, 10)
    assert may.fire_dates == [date(2025, 4, 10)]


def test_group_fires_by_cycle_skips_missing_features():
    features = pd.DataFrame([
        {"date": pd.Timestamp("2025-03-24"), "target_expiry": "2025-04-24"},
    ])
    fires = [
        (date(2025, 3, 24), {}),
        (date(2025, 3, 25), {}),  # not in features
    ]
    cycles = group_fires_by_cycle(
        fires, features, underlying="NIFTY", strategy_version="3.0.0"
    )
    assert len(cycles) == 1
    assert cycles["NIFTY:2025-04-24:3.0.0"].fire_dates == [date(2025, 3, 24)]


def test_group_fires_by_cycle_empty():
    features = pd.DataFrame(columns=["date", "target_expiry"])
    cycles = group_fires_by_cycle(
        [], features, underlying="NIFTY", strategy_version="3.0.0"
    )
    assert cycles == {}


def _import_legacy_v3_cycles():
    path = REPO_ROOT / "scripts" / "nfo" / "v3_live_rule_backtest.py"
    spec = importlib.util.spec_from_file_location("_legacy_v3lrb", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_legacy_v3lrb"] = mod
    spec.loader.exec_module(mod)
    return mod._v3_cycles


def _import_legacy_redesign_variants():
    path = REPO_ROOT / "scripts" / "nfo" / "redesign_variants.py"
    spec = importlib.util.spec_from_file_location("_legacy_rv_cycles", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_legacy_rv_cycles"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def _iso_registry(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")


@pytest.mark.skipif(not SIGNALS.exists(), reason="requires cached historical_signals.parquet")
def test_group_fires_parity_v3(_iso_registry):
    strat_path = REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml"
    spec, _ = load_strategy(strat_path)
    df = pd.read_parquet(SIGNALS)
    df["date"] = pd.to_datetime(df["date"])

    rv = _import_legacy_redesign_variants()
    variant_v3 = next(v for v in rv.make_variants() if v.name == "V3")
    atr = rv.load_nifty_atr(df["date"])

    # Engine path: TriggerEvaluator + group_fires_by_cycle
    def _legacy_event_pass(entry, dte):
        return "high" if not rv._event_pass(
            entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"}, window_days=10
        ) else "none"

    ev = TriggerEvaluator(spec, event_resolver=_legacy_event_pass)
    engine_fires = ev.fire_dates(df, atr)
    engine_cycles = group_fires_by_cycle(
        engine_fires, df, underlying="NIFTY", strategy_version=spec.strategy_version,
    )

    # Legacy path: _v3_cycles
    legacy_v3_cycles_fn = _import_legacy_v3_cycles()
    # _v3_cycles returns [(first_fire_date, target_expiry_date), ...]
    legacy_list = legacy_v3_cycles_fn(df)
    legacy_pairs = {(f, e) for f, e in legacy_list}

    engine_pairs = {
        (c.first_fire_date, c.target_expiry) for c in engine_cycles.values()
    }

    assert engine_pairs == legacy_pairs, (
        f"engine∖legacy={engine_pairs - legacy_pairs}; "
        f"legacy∖engine={legacy_pairs - engine_pairs}"
    )
