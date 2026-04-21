"""Tests for StudySpec + DatasetRef (master design §4.3)."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from nfo.specs.study import DatasetRef, StudySpec


def _ref() -> DatasetRef:
    return DatasetRef(
        dataset_id="historical_features_2024-01_2026-04",
        dataset_type="features",
        path=Path("data/nfo/datasets/features/historical_features_2024-01_2026-04"),
    )


def _spec(**overrides) -> dict:
    base = dict(
        study_id="capital_analysis_10l",
        study_type="capital_analysis",
        strategy_spec_ref=Path("configs/nfo/strategies/v3_frozen.yaml"),
        dataset_refs=[_ref()],
        parameters={"capital_inr": 1_000_000, "variant": "hte"},
    )
    base.update(overrides)
    return base


def test_study_roundtrip():
    s = StudySpec.model_validate(_spec())
    assert s.study_id == "capital_analysis_10l"


def test_study_rejects_bad_type():
    with pytest.raises(ValidationError):
        StudySpec.model_validate(_spec(study_type="not_a_real_type"))


def test_study_rejects_extra():
    with pytest.raises(ValidationError):
        StudySpec.model_validate({**_spec(), "extra": 1})


def test_parameters_must_be_json_serializable():
    class _NotJson:
        pass

    with pytest.raises(ValidationError):
        StudySpec.model_validate(_spec(parameters={"bad": _NotJson()}))


def test_parameters_accept_nested():
    s = StudySpec.model_validate(_spec(parameters={"nested": {"a": [1, 2], "b": "x"}}))
    assert s.parameters["nested"]["a"] == [1, 2]


def test_dataset_ref_literal_type():
    with pytest.raises(ValidationError):
        DatasetRef(
            dataset_id="x",
            dataset_type="bogus",
            path=Path("."),
        )
