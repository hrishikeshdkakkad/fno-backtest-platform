"""End-to-end drift detection (master design §7.3, §12 item 7)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from nfo.datasets.features import ingest_features_parquet
from nfo.reporting.artifacts import open_run_directory
from nfo.reporting.hash_sources import filesystem_hash_sources
from nfo.reporting.index import generate_index
from nfo.specs.manifest import RunManifest


def _make_run(runs_root: Path, run_id: str, *, dataset_hashes: dict[str, str]) -> None:
    rd = open_run_directory(root=runs_root, run_id=run_id)
    m = RunManifest(
        run_id=run_id,
        created_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
        code_version="abc",
        study_spec_hash="x" * 64,
        strategy_spec_hash="s" * 64,
        strategy_id="v3", strategy_version="3.0.0",
        study_type="capital_analysis",
        selection_mode="cycle_matched",
        dataset_hashes=dataset_hashes,
        window_start=date(2024, 1, 1), window_end=date(2025, 1, 1),
        artifacts=[], status="ok", duration_seconds=1.0,
    )
    rd.write_manifest(m)


def _seed_dataset(tmp_path: Path) -> tuple[Path, str]:
    df = pd.DataFrame({"date": pd.to_datetime(["2025-01-01", "2025-01-02"]), "x": [1, 2]})
    parquet_path = tmp_path / "seed.parquet"
    df.to_parquet(parquet_path)
    datasets_root = tmp_path / "datasets"
    manifest = ingest_features_parquet(
        parquet_path=parquet_path, dataset_id="ds_test",
        datasets_root=datasets_root,
    )
    return datasets_root, manifest.parquet_sha256


def test_fresh_run_is_not_stale(tmp_path):
    datasets_root, h = _seed_dataset(tmp_path)
    runs_root = tmp_path / "runs"
    _make_run(runs_root, "r-ok", dataset_hashes={"ds_test": h})
    # Seed a strategy YAML so filesystem_hash_sources can resolve strategy_id.
    strats = tmp_path / "strategies"; strats.mkdir()
    (strats / "v3.yaml").write_text("""
strategy_id: v3
strategy_version: 3.0.0
description: t
universe: {underlyings: [NIFTY], delta_target: 0.30, delta_tolerance: 0.05,
           width_rule: fixed, width_value: 100.0, dte_target: 35, dte_tolerance: 3}
feature_set: [vix]
trigger_rule: {}
selection_rule: {mode: cycle_matched, preferred_exit_variant: hte}
entry_rule: {}
exit_rule: {variant: hte, profit_take_fraction: 1.0, manage_at_dte: null}
capital_rule: {fixed_capital_inr: 1000000}
slippage_rule: {flat_rupees_per_lot: 0.0}
""".strip())
    # The run's strategy_spec_hash is "s"*64, but we don't care about strategy
    # drift here — the test focuses on DATASET drift. The strategy hash mismatch
    # will show up, but we assert on dataset-drift specifically.
    sources = filesystem_hash_sources(
        strategies_root=strats, datasets_root=datasets_root,
    )
    res = generate_index(runs_root=runs_root, out_root=tmp_path, sources=sources)
    assert res.total_runs == 1
    # Dataset fresh → index output should NOT contain dataset_hash_changed
    idx = (tmp_path / "index.md").read_text()
    assert "dataset_hash_changed" not in idx


def test_dataset_drift_marks_run_stale(tmp_path):
    datasets_root, original_hash = _seed_dataset(tmp_path)
    runs_root = tmp_path / "runs"
    _make_run(runs_root, "r-drift", dataset_hashes={"ds_test": original_hash})

    strats = tmp_path / "strategies"; strats.mkdir()
    (strats / "v3.yaml").write_text("""
strategy_id: v3
strategy_version: 3.0.0
description: t
universe: {underlyings: [NIFTY], delta_target: 0.30, delta_tolerance: 0.05,
           width_rule: fixed, width_value: 100.0, dte_target: 35, dte_tolerance: 3}
feature_set: [vix]
trigger_rule: {}
selection_rule: {mode: cycle_matched, preferred_exit_variant: hte}
entry_rule: {}
exit_rule: {variant: hte, profit_take_fraction: 1.0, manage_at_dte: null}
capital_rule: {fixed_capital_inr: 1000000}
slippage_rule: {flat_rupees_per_lot: 0.0}
""".strip())

    # Simulate drift by rewriting the dataset's manifest with a new hash.
    ds_manifest = datasets_root / "features" / "ds_test" / "manifest.json"
    raw = json.loads(ds_manifest.read_text())
    raw["parquet_sha256"] = "DR1FTED" + "0" * (64 - 7)
    ds_manifest.write_text(json.dumps(raw))

    sources = filesystem_hash_sources(
        strategies_root=strats, datasets_root=datasets_root,
    )
    res = generate_index(runs_root=runs_root, out_root=tmp_path, sources=sources)
    assert res.stale_runs == 1
    idx = (tmp_path / "index.md").read_text()
    assert "dataset_hash_changed:ds_test" in idx
