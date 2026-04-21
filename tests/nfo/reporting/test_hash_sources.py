"""Tests for filesystem-backed HashSources factory."""
from __future__ import annotations

import textwrap

from nfo.reporting.hash_sources import filesystem_hash_sources
from nfo.specs.loader import load_strategy, reset_registry_for_tests


STRAT = textwrap.dedent("""
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
""")


def test_strategy_hash_fn_loads_from_configs(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir()
    (strat_dir / "v3.yaml").write_text(STRAT)
    load_strategy(strat_dir / "v3.yaml")

    sources = filesystem_hash_sources(
        strategies_root=strat_dir,
        datasets_root=tmp_path / "datasets",
    )
    h = sources.strategy_hash_fn("v3", "3.0.0")
    assert h is not None and len(h) == 64


def test_strategy_hash_fn_returns_none_when_missing(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")
    sources = filesystem_hash_sources(
        strategies_root=tmp_path / "strategies",
        datasets_root=tmp_path / "datasets",
    )
    assert sources.strategy_hash_fn("nope", "1.0.0") is None


def test_dataset_hash_fn_reads_manifest(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")
    ds_dir = tmp_path / "datasets" / "features" / "ds_x"
    ds_dir.mkdir(parents=True)
    (ds_dir / "manifest.json").write_text(
        '{"dataset_id":"ds_x","dataset_type":"features","source_paths":[],'
        '"date_window":null,"row_count":0,"build_time":"2026-04-21T00:00:00Z",'
        '"code_version":"a","upstream_datasets":[],"parquet_sha256":"HHHH",'
        '"schema_fingerprint":"SSSS"}'
    )
    sources = filesystem_hash_sources(
        strategies_root=tmp_path / "strategies",
        datasets_root=tmp_path / "datasets",
    )
    assert sources.dataset_hash_fn("ds_x") == "HHHH"
    assert sources.dataset_hash_fn("unknown") is None
