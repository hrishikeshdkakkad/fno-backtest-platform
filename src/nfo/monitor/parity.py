"""Monitor-research parity (master design §10.4).

Given a folder of stored MonitorSnapshot JSONL files + a features dataset,
re-evaluate the engine trigger for each snapshot and report mismatches.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from nfo.engine.triggers import TriggerEvaluator
from nfo.monitor.store import load_snapshots
from nfo.specs.strategy import StrategySpec


@dataclass
class ParityMismatch:
    snapshot_id: str
    timestamp: datetime
    monitor_trigger_passed: bool
    engine_trigger_passed: bool
    monitor_detail: dict[str, Any] = field(default_factory=dict)
    engine_detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParityReport:
    total_snapshots: int
    matched: int
    mismatches: list[ParityMismatch] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


def compare_monitor_vs_research(
    *,
    spec: StrategySpec,
    monitor_jsonl_root: Path,
    features_df: pd.DataFrame,
    atr_series: pd.Series,
    start: date | None = None,
    end: date | None = None,
    event_resolver: Callable | None = None,
) -> ParityReport:
    """Replay features_df through engine.triggers.TriggerEvaluator and compare
    the engine's fire decision per snapshot timestamp against the stored
    MonitorSnapshot's trigger_passed flag.

    Matching strategy:
      - Load snapshots in [start, end] from monitor_jsonl_root via load_snapshots.
      - For each snapshot:
          - Find the features row matching snapshot.timestamp.date() in features_df.
          - Compute engine FireRow via TriggerEvaluator.evaluate_row.
          - If features row missing: record as mismatch with engine_detail={"missing": True}.
          - Compare trigger_passed. If different, record mismatch.
      - Return ParityReport.

    Note: master design §10.4 specifies 1e-9 tolerance on raw gate floats; for
    P4 we compare the boolean trigger_passed exactly. Per-gate float parity
    is a future concern when trigger_details include raw numerics.
    """
    snaps = load_snapshots(root=monitor_jsonl_root, start=start, end=end)
    if not snaps:
        return ParityReport(total_snapshots=0, matched=0)

    ev = TriggerEvaluator(spec, event_resolver=event_resolver)

    # Build an index: features row lookup by date
    features = features_df.copy()
    features["_date"] = pd.to_datetime(features["date"]).dt.date

    # Precompute atr-by-date dict for evaluator
    atr_by_date: dict[date, float] = {}
    for d, v in atr_series.items():
        atr_by_date[pd.Timestamp(d).date()] = float(v) if np.isfinite(v) else float("nan")

    mismatches: list[ParityMismatch] = []
    matched = 0
    for snap in snaps:
        snap_day = snap.timestamp.date()
        row_match = features[features["_date"] == snap_day]
        if row_match.empty:
            mismatches.append(ParityMismatch(
                snapshot_id=snap.snapshot_id,
                timestamp=snap.timestamp,
                monitor_trigger_passed=snap.trigger_passed,
                engine_trigger_passed=False,
                monitor_detail=snap.trigger_details,
                engine_detail={"missing": True, "features_date": snap_day.isoformat()},
            ))
            continue
        row = row_match.iloc[0]
        atr_val = atr_by_date.get(snap_day, float("nan"))
        result = ev.evaluate_row(row, atr_value=atr_val)
        if result.fired != snap.trigger_passed:
            mismatches.append(ParityMismatch(
                snapshot_id=snap.snapshot_id,
                timestamp=snap.timestamp,
                monitor_trigger_passed=snap.trigger_passed,
                engine_trigger_passed=result.fired,
                monitor_detail=snap.trigger_details,
                engine_detail=result.detail,
            ))
        else:
            matched += 1

    return ParityReport(
        total_snapshots=len(snaps), matched=matched, mismatches=mismatches,
    )
