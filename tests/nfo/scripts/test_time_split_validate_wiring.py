"""Wiring test: time_split_validate.main() calls wrap_legacy_run correctly.

This test does NOT run the legacy logic; it monkeypatches wrap_legacy_run to
capture the call arguments and asserts they match the plan.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_script_module(name: str):
    path = REPO_ROOT / "scripts" / "nfo" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_scripts_nfo_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"_scripts_nfo_{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_time_split_validate_calls_wrap_legacy_run(monkeypatch, tmp_path):
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
    mod = _load_script_module("time_split_validate")
    monkeypatch.setattr(
        mod,
        "_legacy_main",
        lambda argv=None: {"metrics": {}, "body_markdown": "", "warnings": []},
        raising=False,
    )
    ret = mod.main([])
    assert ret == 0
    assert captured["study_type"] == "time_split"
    assert captured["strategy_path"].name == "v3_frozen.yaml"
    assert captured["study_path"].name == "time_split_default.yaml"
    # The legacy time_split_report.md is multi-variant (V3-V6 day-matched)
    # and intentionally NOT mirrored into this run — it would misrepresent
    # the cycle_matched V3 provenance the manifest declares.
    assert captured["legacy_artifacts"] == []
    assert "dataset_refs" in captured
    assert {r.dataset_id for r in captured["dataset_refs"]} == {
        "historical_features_2024-01_2026-04",
        "trade_universe_nifty_2024-01_2026-04",
    }


def test_non_canonical_args_skip_run_emission(monkeypatch, tmp_path, caplog):
    """Non-default --split-date or --variants must NOT emit a run dir.

    The canonical run is defined by configs/nfo/studies/time_split_default.yaml;
    CLI overrides break that provenance, so main() refuses to wrap and only
    refreshes the top-level legacy report.
    """
    import logging

    wrap_calls: list[dict] = []

    def fake_wrap(**kwargs):
        wrap_calls.append(kwargs)

        class _R:
            class run_dir:
                path = tmp_path / "runs" / "nope"

        return _R

    monkeypatch.setattr(
        "nfo.reporting.wrap_legacy_run.wrap_legacy_run", fake_wrap, raising=True
    )
    mod = _load_script_module("time_split_validate")

    legacy_calls: list = []

    def _fake_legacy(argv=None):
        legacy_calls.append(argv)
        return {"metrics": {}, "body_markdown": "", "warnings": []}

    monkeypatch.setattr(mod, "_legacy_main", _fake_legacy, raising=False)

    with caplog.at_level(logging.WARNING, logger="time_split_validate"):
        ret = mod.main(["--split-date", "2025-06-01"])
    assert ret == 0
    assert wrap_calls == [], "non-canonical args must not trigger wrap_legacy_run"
    assert legacy_calls == [["--split-date", "2025-06-01"]]
    assert any("Non-canonical" in rec.message for rec in caplog.records)


def test_non_canonical_variants_also_skip(monkeypatch, tmp_path):
    wrap_calls: list[dict] = []

    def fake_wrap(**kwargs):
        wrap_calls.append(kwargs)

        class _R:
            class run_dir:
                path = tmp_path / "runs" / "nope"

        return _R

    monkeypatch.setattr(
        "nfo.reporting.wrap_legacy_run.wrap_legacy_run", fake_wrap, raising=True
    )
    mod = _load_script_module("time_split_validate")
    monkeypatch.setattr(
        mod, "_legacy_main",
        lambda argv=None: {"metrics": {}, "body_markdown": "", "warnings": []},
        raising=False,
    )
    ret = mod.main(["--variants", "V4"])
    assert ret == 0
    assert wrap_calls == []
