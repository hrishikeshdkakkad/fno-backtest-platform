"""Parity: engine.TriggerEvaluator must reproduce legacy V3 firing dates.

Uses the cached `results/nfo/historical_signals.parquet` so the test runs
deterministically (no Dhan calls). The assertion is exact set equality of
firing dates between legacy `redesign_variants.get_firing_dates` and the
new engine TriggerEvaluator loaded from `configs/nfo/strategies/v3_frozen.yaml`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from nfo.engine.triggers import TriggerEvaluator
from nfo.specs.loader import load_strategy, reset_registry_for_tests


REPO_ROOT = Path(__file__).resolve().parents[3]
SIGNALS = REPO_ROOT / "results" / "nfo" / "historical_signals.parquet"


def _import_legacy():
    path = REPO_ROOT / "scripts" / "nfo" / "redesign_variants.py"
    spec = importlib.util.spec_from_file_location("_legacy_rv", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_legacy_rv"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def _iso_registry(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")


@pytest.mark.skipif(not SIGNALS.exists(), reason="requires cached historical_signals.parquet")
def test_v3_firing_dates_match_legacy(_iso_registry):
    strat_path = REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml"
    spec, _ = load_strategy(strat_path)
    df = pd.read_parquet(SIGNALS)
    df["date"] = pd.to_datetime(df["date"])

    rv = _import_legacy()
    variant_v3 = next(v for v in rv.make_variants() if v.name == "V3")
    atr = rv.load_nifty_atr(df["date"])

    legacy_fires = rv.get_firing_dates(variant_v3, df, atr)
    legacy_dates = {d for d, _ in legacy_fires}

    # The cached parquet does NOT carry an `event_risk_v3` column under V3
    # semantics (it was computed under V0: all event kinds "high"). To reach
    # parity for the V3 gate logic we wire legacy's `_event_pass` as the
    # engine's event_resolver. This preserves the master-design contract
    # (engine is THE source of truth for gate logic) while letting the data
    # layer evolve separately — the resolver function is the explicit seam.
    def _resolver(entry_date, dte):
        ok = rv._event_pass(
            entry_date,
            dte,
            severity_high_kinds=variant_v3.severity_high_kinds,
            window_days=variant_v3.event_window_days,
        )
        return "none" if ok else "high"

    ev = TriggerEvaluator(spec, event_resolver=_resolver)
    engine_fires = ev.fire_dates(df, atr)
    engine_dates = {d for d, _ in engine_fires}

    missing_in_engine = legacy_dates - engine_dates
    extra_in_engine = engine_dates - legacy_dates
    assert engine_dates == legacy_dates, (
        f"engine\\legacy={sorted(extra_in_engine)}; "
        f"legacy\\engine={sorted(missing_in_engine)}"
    )
