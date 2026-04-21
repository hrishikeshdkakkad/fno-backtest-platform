"""Smoke tests for P1 script wrappers.

Each wrapper must emit a run directory that validates against RunManifest.
Requires cached data under data/nfo/ and results/nfo/; skipped if missing.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from nfo.specs.manifest import RunManifest


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = REPO_ROOT / "results" / "nfo" / "runs"


WRAPPER_SCRIPTS = [
    ("v3_capital_analysis.py", ["--pt-variant", "hte"]),
    ("v3_robustness.py", []),
    ("v3_falsification.py", []),
    ("v3_live_rule_backtest.py", []),
    ("redesign_variants.py", []),
    ("time_split_validate.py", []),
]


def _cache_ready() -> bool:
    return (REPO_ROOT / "results" / "nfo" / "historical_signals.parquet").exists() and \
           (REPO_ROOT / "results" / "nfo" / "spread_trades.csv").exists()


@pytest.mark.skipif(not _cache_ready(), reason="requires cached signals/trades")
@pytest.mark.parametrize("script_name,extra_args", WRAPPER_SCRIPTS)
def test_wrapper_emits_valid_run_dir(script_name, extra_args):
    before = set(RUNS_DIR.iterdir()) if RUNS_DIR.exists() else set()
    env = os.environ.copy()
    result = subprocess.run(
        [".venv/bin/python", f"scripts/nfo/{script_name}", *extra_args],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env, timeout=600,
    )
    assert result.returncode == 0, f"script failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    after = set(RUNS_DIR.iterdir())
    new = after - before
    assert len(new) >= 1, f"expected a new run directory. stdout={result.stdout!r}"
    run_dir = sorted(new, key=lambda p: p.stat().st_mtime)[-1]
    manifest = RunManifest.model_validate_json((run_dir / "manifest.json").read_text())
    assert manifest.status in ("ok", "warnings")
    report = (run_dir / "report.md").read_text()
    assert "<!-- methodology:begin -->" in report
