# Phase 4 Completion Report

**Completed:** 2026-04-22
**Bundles:** A (monitor schemas + transitions) → B (capture + store) → C (parity) → D (regime_watch migration) → E (master summary + legacy archival) → F (acceptance)
**Commits since p3-complete:** 8 (run `git rev-list p3-complete..HEAD --count`)
**Test count:** 1028 passed

## Summary

- `src/nfo/monitor/snapshot.py` — `MonitorSnapshot` Pydantic model + `build_snapshot_id` helper + `capture_snapshot` producer that consumes `engine.triggers.TriggerEvaluator`.
- `src/nfo/monitor/transitions.py` — pure 6-state machine (`idle`/`watch`/`fire`/`entered`/`invalidated`/`expired`) with deterministic `next_state(current, evidence) -> (new_state, reason_codes)`.
- `src/nfo/monitor/store.py` — append-only JSONL per day (`<YYYY-MM-DD>.jsonl`).
- `src/nfo/monitor/parity.py` — `compare_monitor_vs_research` re-evaluates features through `engine.triggers` and flags mismatches.
- `scripts/nfo/regime_watch.py::_compute_v3_gate` — replaced inline decision logic (module-level thresholds that drifted from the spec) with a call to `TriggerEvaluator(v3_frozen_spec).evaluate_row`; tuple shape preserved; MonitorSnapshot emitted after every gate computation.
- `src/nfo/reporting/master_summary.py` — generates `results/nfo/master_summary.md` aggregating latest run per study_type with headline metrics.
- `src/nfo/reporting/__main__.py` — CLI now emits three pointers: `index.md`, `latest.json`, `master_summary.md`.
- `results/nfo/legacy/README.md` — lists and explains deprecated pre-platform narrative reports.

## Platform invariants re-verified

- Master design §12 item 4 — live monitor and historical replay agree on firing decisions when using the same spec. `compare_monitor_vs_research` is the platform tooling that enforces this going forward.
- Master design §12 item 6 — index generator marks stale runs; master_summary uses latest-per-study; legacy narrative reports now have a dedicated deprecation README.
- Master design §13.4 deliverable — `regime_watch.py` is platform-backed (trigger decision goes through engine), monitor snapshots land in JSONL, master summary auto-generated.

## Parity proof

- `test_v3_gate_routes_to_engine_triggers` — verified `_compute_v3_gate` invokes `TriggerEvaluator.evaluate_row`.
- `test_v3_gate_engine_matches_direct_call` — verified that for the same inputs, regime_watch's gate returns the same boolean as a direct engine call.
- `test_v3_gate_emits_monitor_snapshot` — verified JSONL emission.
- `compare_monitor_vs_research` unit tests — verified happy-path, mismatch detection, missing-features-row fallback, date range filter.

## Threshold-drift fix

Pre-migration, `regime_watch.py` had module-level constants that disagreed with `v3_frozen.yaml`:

| Constant | Pre-P4 value | Spec value |
|---|---|---|
| `IV_RV_SPREAD_RICH` | 0.0 | -2.0 |
| `VIX_RICH` | 15.0 | 20.0 |
| `VIX_PCT_RICH` | 0.70 | 0.80 |

Post-P4, the spec wins — resolving the master design §12.4 live↔research contract violation that existed pre-P4.

## Deferrals (P5)

- Full dataset pipeline (`datasets/{raw,normalized,features,trade_universe,study_inputs}.py`)
- Legacy script body replacement for `v3_capital_analysis`, `v3_robustness`, `v3_falsification`, `time_split_validate`, `v3_live_rule_backtest` (currently emit manifests via wrap_legacy_run but still carry full legacy bodies)
- `scripts/nfo/v3_live_rule_backtest.py` could become a thin wrapper over `studies.live_replay.run_live_replay` (P3 shipped the study, the legacy body hasn't been swapped yet).
