# Phase 1 Completion Report

**Completed:** 2026-04-21
**Bundles executed:** A (bootstrap) -> B (specs) -> C (ids) -> D (reporting) -> E (configs) -> F (wrap_legacy_run) -> G (script wrappers) -> H (smoke + cross-report) -> I (index CLI) -> J (acceptance)
**Commits:** 32 (initial SHA: `61b1dc5`)
**Test count:** 284 collected / 284 passed (278 unit+wiring + 6 smoke)
**Smoke tests:** 6 passed (all wrapper scripts validated end-to-end with cached data)

## Summary

- Git bootstrapped (initial SHA: `61b1dc5`)
- Package renamed from `csp` to `nfo-platform`
- Legacy CSP archived under `legacy/csp/`, non-NFO scripts under `legacy/scripts_csp/`, legacy test under `legacy/tests/`, with top-level `legacy/README.md` explaining contents and PYTHONPATH usage
- Pydantic v2 schemas shipped: `StrategySpec` + 7 nested models, `StudySpec`, `RunManifest`, `DatasetManifest`
- Canonical id helpers shipped (`feature_day_id`, `cycle_id`, `fire_id`, `trade_id`, `selection_id`, `build_run_id`)
- Reporting infrastructure shipped: `RunDirectory`, methodology header, top-level index generator, filesystem HashSources, git `code_version` helper
- 6 legacy scripts wrapped via `wrap_legacy_run` (v3_capital_analysis, v3_robustness, v3_falsification, v3_live_rule_backtest, redesign_variants, time_split_validate)
- First two strategy specs shipped: `v3_frozen@3.0.0` (cycle_matched), `v3_live_rule@3.0.1` (live_rule)
- 6 default study YAMLs validated against StudySpec
- CLI entrypoint: `python -m nfo.reporting` regenerates `results/nfo/index.md` + `latest.json`

## Test coverage

- Unit / wiring tests: 278 passed (specs, engine, reporting, datasets, configs, scripts)
- Smoke tests: 6 passed (all wrappers emit valid run dirs against cached data)
- Cross-report consistency: 54 parametrized tests (3 checks x 18 runs in the current workspace) - all passed

## Acceptance checklist (master design §10.1 / plan §P1)

- [x] Each wrapper script emits a `results/nfo/runs/<run_id>/` directory -- 12 run directories covering all 6 study types (`capital_analysis`, `falsification`, `live_replay`, `robustness`, `time_split`, `variant_comparison`).
- [x] Every manifest validates against RunManifest -- covered by `tests/nfo/cross_report/test_manifest_schema.py`.
- [x] Every `report.md` contains the methodology header -- covered by `tests/nfo/cross_report/test_manifest_header_consistency.py`.
- [x] `results/nfo/index.md` lists every run with stale markers where applicable.
- [x] `results/nfo/latest.json` points to the newest run per study_type (6 entries, one per study_type).
- [x] `configs/nfo/.registry.json` contains entries for `v3@3.0.0` and `v3@3.0.1`.
- [x] `StrategyDriftError` demo worked (Step 3 below).
- [x] `src/csp/` is gone; `legacy/csp/` exists and `legacy/README.md` documents it.
- [x] `pyproject.toml` name == `nfo-platform`.
- [x] Full test suite green (284/284).
- [x] Git log has 32 commits with conventional-commit style messages.

## StrategyDriftError demo

Modified `event_window_days: 10 -> 7` in `configs/nfo/strategies/v3_frozen.yaml` without bumping `strategy_version`.

```
Modified event_window_days (no version bump).
OK: StrategyDriftError raised:
  strategy_id='v3' version='3.0.0' content hash changed (b1371c192cbc -> 458aeed94ef9). Bump strategy_version before editing spec content.
OK: v3 loads cleanly after revert. hash=b1371c192cbc
```

Reverted after demo; v3 loads cleanly with the same `b1371c...` hash.

## Known deferrals (P2+)

- Engine extraction (trigger evaluation, cycle grouping, selection, entry, exits, execution, capital, metrics)
- Dataset manifests for existing `data/nfo/index/`, `data/nfo/rolling/`, `data/nfo/events.parquet`
- Parity tests comparing new engine output vs legacy script output
- Legacy script body replacement (currently `_legacy_main` still contains full business logic)
- Monitor snapshot capture (`src/nfo/monitor/`)
- Master summary generator

## Next: Phase 2 plan

- Master design: `docs/superpowers/specs/2026-04-21-nfo-research-platform-design.md` (§6 Engine extraction, §7 Datasets, §10 Migration §10.1 P2 parity gates)
- Plan doc: to be written at `docs/superpowers/plans/<date>-nfo-platform-phase2-plan.md`
