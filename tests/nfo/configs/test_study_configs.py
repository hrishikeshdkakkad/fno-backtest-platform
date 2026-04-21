"""Tests that default study YAMLs validate."""
from __future__ import annotations

from pathlib import Path

import pytest

from nfo.specs.loader import load_study


REPO_ROOT = Path(__file__).resolve().parents[3]
STUDIES = REPO_ROOT / "configs" / "nfo" / "studies"


@pytest.mark.parametrize("yaml_name,expected_type", [
    ("variant_comparison_default.yaml", "variant_comparison"),
    ("capital_analysis_10L.yaml", "capital_analysis"),
    ("robustness_default.yaml", "robustness"),
    ("falsification_default.yaml", "falsification"),
    ("time_split_default.yaml", "time_split"),
    ("live_replay_default.yaml", "live_replay"),
])
def test_study_yaml_loads(yaml_name: str, expected_type: str):
    spec, _ = load_study(STUDIES / yaml_name)
    assert spec.study_type == expected_type
