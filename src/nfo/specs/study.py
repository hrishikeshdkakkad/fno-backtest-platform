"""StudySpec + DatasetRef (master design §4.3)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

StudyType = Literal[
    "variant_comparison",
    "time_split",
    "capital_analysis",
    "robustness",
    "falsification",
    "live_replay",
    "monitor_snapshot",
]

DatasetType = Literal["raw", "normalized", "features", "trade_universe", "study_inputs"]


class DatasetRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    dataset_type: DatasetType
    path: Path


class StudySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_id: str
    study_type: StudyType
    strategy_spec_ref: Path
    dataset_refs: list[DatasetRef]
    parameters: dict[str, Any] = Field(default_factory=dict)
    output_profile: Literal["default", "compact", "full"] = "default"

    @field_validator("parameters")
    @classmethod
    def _parameters_json_serializable(cls, v: dict) -> dict:
        try:
            json.dumps(v)
        except (TypeError, ValueError) as e:
            raise ValueError(f"parameters must be JSON-serializable: {e}") from e
        return v
