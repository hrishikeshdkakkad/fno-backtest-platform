"""Wiring test: redesign_variants.main() calls wrap_legacy_run correctly.

This test does NOT run the legacy logic; it monkeypatches wrap_legacy_run to
capture the call arguments and asserts they match the plan.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _restore_real_registry(monkeypatch):
    # Earlier tests may leave nfo.specs.loader._REGISTRY_PATH pointing at a
    # tmp path that no longer exists; force it back to the real registry so
    # `load_strategy` inside _shadow_v3_via_engine reads the committed file.
    from nfo.specs import loader
    monkeypatch.setattr(
        loader, "_REGISTRY_PATH",
        REPO_ROOT / "configs" / "nfo" / ".registry.json",
        raising=True,
    )


def _load_script_module(name: str):
    path = REPO_ROOT / "scripts" / "nfo" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_scripts_nfo_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"_scripts_nfo_{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_redesign_variants_calls_wrap_legacy_run(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_wrap(**kwargs):
        captured.update(kwargs)

        class _R:
            class run_dir:
                path = tmp_path / "runs" / "fake_run"

        (tmp_path / "runs" / "fake_run").mkdir(parents=True, exist_ok=True)
        return _R

    monkeypatch.setattr(
        "nfo.reporting.wrap_legacy_run.wrap_legacy_run", fake_wrap, raising=True
    )
    mod = _load_script_module("redesign_variants")
    # redesign_variants legacy_main takes no argv.
    monkeypatch.setattr(
        mod,
        "_legacy_main",
        lambda: {"metrics": {}, "body_markdown": "", "warnings": []},
        raising=False,
    )
    ret = mod.main()
    assert ret == 0
    assert captured["study_type"] == "variant_comparison"
    assert captured["strategy_path"].name == "v3_frozen.yaml"
    assert captured["study_path"].name == "variant_comparison_default.yaml"
    names = [str(p) for p in captured["legacy_artifacts"]]
    assert any("redesign_comparison.csv" in p for p in names)
    assert any("redesign_comparison.md" in p for p in names)
    assert any("redesign_winner.json" in p for p in names)


def test_v3_engine_shadow_helper_exists():
    """P2-E2: redesign_variants exposes _shadow_v3_via_engine helper.

    The helper's existence IS the wiring — `_legacy_main` calls it after
    computing V3's legacy metrics so the engine path runs alongside legacy
    on every invocation and any drift surfaces in the logs.
    """
    mod = _load_script_module("redesign_variants")
    assert hasattr(mod, "_shadow_v3_via_engine"), (
        "redesign_variants should expose _shadow_v3_via_engine helper (P2-E2)"
    )


def test_v3_engine_shadow_invokes_studies(monkeypatch):
    """P2-E2: `_shadow_v3_via_engine` dispatches to the studies module.

    We monkeypatch `nfo.studies.variant_comparison.run_variant_comparison_v3`
    with a fake and call the helper directly. This verifies the helper wires
    into the engine path without having to run the full legacy pipeline.
    """
    mod = _load_script_module("redesign_variants")

    calls: list[dict] = []

    from nfo.studies import variant_comparison as vc_mod

    def fake_run(**kwargs):
        calls.append(kwargs)
        return vc_mod.VariantResult(
            name="V3", n_fires=42, n_matched_trades=7,
            win_rate=0.9, sharpe=2.0, max_loss_rate=0.0,
            firing_rate_per_year=10.0,
        )

    monkeypatch.setattr(vc_mod, "run_variant_comparison_v3", fake_run, raising=True)

    import pandas as pd
    dummy_signals = pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01"]),
        "target_expiry": ["2025-02-27"],
        "dte": [35],
    })
    dummy_trades = pd.DataFrame()
    dummy_atr = pd.Series([10.0], index=pd.to_datetime(["2025-01-01"]))

    result = mod._shadow_v3_via_engine(
        dummy_signals, dummy_trades, dummy_atr, legacy_firing_days=42,
    )
    assert result is not None
    assert result.n_fires == 42
    assert len(calls) == 1
    # Spec must be loaded and passed through.
    assert calls[0]["spec"].strategy_id == "v3"


def test_v3_engine_shadow_logs_drift(monkeypatch, caplog):
    """P2-E2: when engine and legacy disagree on n_fires, helper warns."""
    import logging
    mod = _load_script_module("redesign_variants")

    from nfo.studies import variant_comparison as vc_mod

    def fake_run(**kwargs):
        return vc_mod.VariantResult(
            name="V3", n_fires=99, n_matched_trades=0,
            win_rate=0.0, sharpe=0.0, max_loss_rate=0.0,
            firing_rate_per_year=0.0,
        )

    monkeypatch.setattr(vc_mod, "run_variant_comparison_v3", fake_run, raising=True)

    import pandas as pd
    dummy_signals = pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01"]),
        "target_expiry": ["2025-02-27"],
        "dte": [35],
    })
    dummy_trades = pd.DataFrame()
    dummy_atr = pd.Series([10.0], index=pd.to_datetime(["2025-01-01"]))

    with caplog.at_level(logging.WARNING, logger="redesign_variants"):
        mod._shadow_v3_via_engine(
            dummy_signals, dummy_trades, dummy_atr, legacy_firing_days=23,
        )
    assert any("drift" in rec.message for rec in caplog.records), (
        f"expected drift warning, got {[r.message for r in caplog.records]}"
    )
