"""Wiring test: v3_capital_analysis.main() calls wrap_legacy_run correctly.

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


def test_v3_capital_analysis_calls_wrap_legacy_run(monkeypatch, tmp_path):
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
    mod = _load_script_module("v3_capital_analysis")
    monkeypatch.setattr(
        mod,
        "_legacy_main",
        lambda argv=None: {"metrics": {}, "body_markdown": "", "warnings": []},
        raising=False,
    )
    ret = mod.main(["--pt-variant", "hte"])
    assert ret == 0
    assert captured["study_type"] == "capital_analysis"
    assert captured["strategy_path"].name == "v3_frozen.yaml"
    assert captured["study_path"].name == "capital_analysis_10L.yaml"
    assert any(
        "v3_capital_trades_hte.csv" in str(p) for p in captured["legacy_artifacts"]
    )
