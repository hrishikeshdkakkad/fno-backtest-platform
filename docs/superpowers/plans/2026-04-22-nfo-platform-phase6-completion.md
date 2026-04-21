# Phase 6 Completion Report

**Completed:** 2026-04-22
**Bundles:** A (features ingest) → B (trade_universe ingest) → C (wrap_legacy_run + scripts wiring) → D (drift E2E test) → E (acceptance)
**Commits since p5-complete:** 9
**Test count:** 1438 passed

## Summary

Dataset drift detection activated end-to-end. Two canonical datasets now have manifested provenance; all 6 wrapper scripts declare their DatasetRefs; RunManifest.dataset_hashes populates automatically; index generator flags stale runs when upstream dataset hashes drift.

## Shipped

- `src/nfo/datasets/_hashing.py` — sha256_file + schema_fingerprint (stable, streamed, order-independent).
- `src/nfo/datasets/features.py::ingest_features_parquet` — copy + manifest.
- `src/nfo/datasets/trade_universe.py::ingest_trade_universe_csv` — concat + manifest.
- `src/nfo/reporting/wrap_legacy_run.py` — accepts `dataset_refs: list[DatasetRef]`, resolves hashes from each manifest.json.
- `scripts/nfo/p6_seed_datasets.py` — one-shot to ingest the two canonical datasets (features + trade_universe).
- All 6 wrapper scripts declare `_DATASET_REFS` and pass through.
- `tests/nfo/datasets/test_staleness_e2e.py` — drift scenario proves index flags stale runs.

## Canonical datasets

| dataset_id | type | sha256 prefix |
|---|---|---|
| `historical_features_2024-01_2026-04` | features | `c69df78125ad` |
| `trade_universe_nifty_2024-01_2026-04` | trade_universe | `f1e78a53524a` |

## Master design §12 item 7

Stale reports are impossible (new-run-on-drift) or automatically flagged in index. Verified end-to-end.

## Deferrals to P7+

- `datasets/raw.py` + `datasets/normalized.py` + `datasets/study_inputs.py`
- Legacy reshape helper extraction into `scripts/nfo/_legacy_helpers/`
- File-moves of pre-platform narrative reports into `results/nfo/legacy/archive/`
- Utility script migrations (`v3_fill_gaps.py`, `recost_trades.py`)
