"""Tests for the wrap_legacy_run helper."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from nfo.reporting.wrap_legacy_run import WrappedRun, wrap_legacy_run
from nfo.specs.loader import reset_registry_for_tests


STRAT_YAML = """
strategy_id: v3
strategy_version: 3.0.0
description: V3
universe:
  underlyings: [NIFTY]
  delta_target: 0.30
  delta_tolerance: 0.05
  width_rule: fixed
  width_value: 100.0
  dte_target: 35
  dte_tolerance: 3
feature_set: [vix]
trigger_rule: {}
selection_rule: {mode: cycle_matched, preferred_exit_variant: hte}
entry_rule: {}
exit_rule: {variant: hte, profit_take_fraction: 1.0, manage_at_dte: null}
capital_rule: {fixed_capital_inr: 1000000}
slippage_rule: {flat_rupees_per_lot: 0.0}
"""


@pytest.fixture(autouse=True)
def _iso(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")


def test_wrap_legacy_run_writes_run_dir(tmp_path):
    strat_path = tmp_path / "v3.yaml"
    strat_path.write_text(STRAT_YAML)

    legacy_csv = tmp_path / "legacy_trades.csv"
    legacy_md = tmp_path / "legacy_report.md"

    def run_logic():
        legacy_csv.write_text("a,b\n1,2\n")
        legacy_md.write_text("## body\n")
        return {"metrics": {"total_pnl_inr": 42.0}, "body_markdown": "## body\n"}

    result: WrappedRun = wrap_legacy_run(
        study_type="capital_analysis",
        strategy_path=strat_path,
        study_path=None,
        legacy_artifacts=[legacy_csv, legacy_md],
        window=(date(2024, 2, 1), date(2026, 4, 18)),
        run_logic=run_logic,
        runs_root=tmp_path / "runs",
        code_version="testsha",
    )

    assert result.run_dir.path.exists()
    manifest = json.loads((result.run_dir.path / "manifest.json").read_text())
    assert manifest["study_type"] == "capital_analysis"
    assert manifest["selection_mode"] == "cycle_matched"
    metrics = json.loads((result.run_dir.path / "metrics.json").read_text())
    assert metrics["total_pnl_inr"] == 42.0
    assert (result.run_dir.path / "tables" / "legacy_trades.csv").exists()
    report = (result.run_dir.path / "report.md").read_text()
    assert "## body" in report
    assert "<!-- methodology:begin -->" in report


def test_wrap_legacy_run_sets_status_warnings(tmp_path):
    strat_path = tmp_path / "v3.yaml"
    strat_path.write_text(STRAT_YAML)

    def run_logic():
        return {"metrics": {}, "body_markdown": "", "warnings": ["data gap: 2025-01-06"]}

    result = wrap_legacy_run(
        study_type="robustness",
        strategy_path=strat_path,
        study_path=None,
        legacy_artifacts=[],
        window=(date(2024, 2, 1), date(2026, 4, 18)),
        run_logic=run_logic,
        runs_root=tmp_path / "runs",
        code_version="testsha",
    )
    manifest = json.loads((result.run_dir.path / "manifest.json").read_text())
    assert manifest["status"] == "warnings"
    assert manifest["warnings"] == ["data gap: 2025-01-06"]
