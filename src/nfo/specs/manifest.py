"""RunManifest + DatasetManifest (master design §4.4, §4.5)."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nfo.specs.study import DatasetType, StudyType

SelectionMode = Literal["day_matched", "cycle_matched", "live_rule"]
RunStatus = Literal["ok", "failed", "warnings"]


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: datetime
    code_version: str
    study_spec_hash: str
    strategy_spec_hash: str
    strategy_id: str
    strategy_version: str
    study_type: StudyType
    selection_mode: SelectionMode
    dataset_hashes: dict[str, str] = Field(default_factory=dict)
    window_start: date
    window_end: date
    artifacts: list[str] = Field(default_factory=list)
    status: RunStatus
    warnings: list[str] = Field(default_factory=list)
    stale_inputs_detected: list[str] = Field(default_factory=list)
    duration_seconds: float


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    dataset_type: DatasetType
    source_paths: list[Path] = Field(default_factory=list)
    date_window: tuple[date, date] | None = None
    row_count: int
    build_time: datetime
    code_version: str
    upstream_datasets: list[str] = Field(default_factory=list)
    parquet_sha256: str
    schema_fingerprint: str
