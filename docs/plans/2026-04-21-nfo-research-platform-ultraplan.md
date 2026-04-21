# NFO Research Platform Ultraplan

Date: 2026-04-21
Authoring context: prepared from the current repo state under `src/nfo`, `scripts/nfo`, `results/nfo`, `tests/nfo`, and existing review/spec docs.
Audience: engineering agent / implementation engineer.
Status: handoff-ready platform refactor plan.

## 1. Executive summary

The current NFO system is no longer a single backtester. It is three overlapping systems:

1. A broad day-matched research flow built around `historical_backtest.py`, `redesign_variants.py`, and `time_split_validate.py`.
2. A frozen-spec cycle-matched research flow built around `robustness.py`, `v3_capital_analysis.py`, `v3_robustness.py`, and `v3_falsification.py`.
3. A live/regime-monitor flow built around `regime_watch.py` and `v3_live_rule_backtest.py`.

Each of those flows answers a slightly different question. They share some logic, but the semantics are not centralized, the output locations overlap, and report conclusions can conflict because they are not all running the same evaluation contract.

The goal of this refactor is to convert the repo into a real research platform with:

- one canonical strategy spec system
- one canonical execution contract
- one canonical dataset pipeline
- one canonical run/artifact registry
- one canonical way to distinguish research questions
- one canonical bridge between research and live regime monitoring

This plan prioritizes **research platform quality first**, while making live/regime monitoring a first-class consumer of the same platform rather than a parallel codepath.

## 2. What is wrong today

### 2.1 Semantic fragmentation

The repo currently mixes multiple meanings of "strategy performance":

- `redesign_variants.py` evaluates all trades whose `entry_date` matches a V3 firing day.
- `robustness.py` / `v3_capital_analysis.py` select one canonical trade per V3 cycle.
- `v3_live_rule_backtest.py` forces entry on or after the first fire date and therefore uses live-valid entry semantics.
- `regime_watch.py` computes live advisory state but is not yet a thin consumer of the same canonical execution engine.

This means "V3 performance" is currently overloaded. The same strategy name can refer to different trade-selection semantics depending on which script is used.

### 2.2 Logic duplication

Critical business logic is duplicated or semi-duplicated across scripts:

- cycle grouping
- firing-date selection
- canonical trade selection
- live-valid entry-date enforcement
- report rendering
- gap-trade generation
- scenario-specific metrics tables

That duplication is why behavior drifts and reports disagree.

### 2.3 Artifact disorder

`results/nfo/` currently contains:

- current reports
- stale reports
- narrative reports from old methodology
- unsuffixed mutable reports
- study outputs that are not grouped by run

This makes it too easy to quote the wrong file. The repo already contains both current and stale capital reports, old plain-language reports, and old tier-1 summaries that no longer match the current dataset or semantics.

### 2.4 Scripts are carrying platform responsibilities

Many scripts under `scripts/nfo/` are doing too much:

- loading data
- defining business semantics
- selecting trade subsets
- running scenario studies
- rendering markdown
- writing canonical outputs

They should instead be thin CLI wrappers over reusable engine modules.

### 2.5 Research/live split is incomplete

`v3_live_rule_backtest.py` is the start of a correct live-valid rulebook, but it is still a sidecar study rather than the canonical execution contract. `regime_watch.py` is rich and useful, but it still contains substantial business logic that should be shared with backtests.

### 2.6 Testing is better than before, but still uneven

The repo now has good targeted tests for many research helpers, including falsification helpers, but there is still too much confidence coming from end-to-end reruns and too little from explicit platform-contract tests.

### 2.7 The project root still reflects the old CSP shape

`README.md` and `pyproject.toml` still describe a CSP backtester first. The repo now contains a significant NFO research system that deserves its own platform shape, documentation hierarchy, and operator workflow.

## 3. Desired end-state

The end-state system should answer these questions unambiguously:

1. What exact strategy spec was run?
2. What exact data version and date window were used?
3. What exact trade-selection semantics were used?
4. What exact execution semantics were used?
5. What exact reports and tables were generated from that run?
6. Can the same strategy be replayed through live and historical paths identically?
7. Are stale reports impossible or at least automatically marked as stale?

The platform should support three first-class evaluation contracts:

1. `day_matched`
   - Research question: "Are trades entered on signal days generally good?"
   - Trade set: all trades whose `entry_date` falls on a firing date and match the declared universe constraints.

2. `cycle_matched`
   - Research question: "If I force one canonical trade per cycle, how does that trade family behave?"
   - Trade set: one selected trade per cycle, using explicit selection rules.

3. `live_rule`
   - Research question: "What would a literal live system have done with only information available on that date?"
   - Trade set: selected trade per cycle, with `entry_date >= first_fire_date`, session snapping rules, and no look-ahead.

These are different, valid research questions. The platform must make them explicit instead of letting them hide inside script behavior.

## 4. Platform principles

### 4.1 Strategy semantics must be explicit

No more hidden strategy definitions inside scripts. Every study must run from a validated spec object.

### 4.2 One strategy name, one versioned contract

If the meaning changes, the spec version changes. Do not let "V3" silently mean different things in different scripts.

### 4.3 Results must be run-scoped

Canonical outputs must live under a run directory and be linked by a manifest. Top-level convenience summaries are allowed, but they must point to a run, not act as the source of truth.

### 4.4 Live and backtest must share the same rule engine

Only the data source should differ. The rule implementation must be shared.

### 4.5 Reports must declare methodology

Every rendered report must declare:

- spec id and version
- evaluation mode
- entry rule
- selection rule
- exit rule
- universe
- date window
- dataset versions
- run id

### 4.6 Manual markdown edits are not acceptable

If a report needs curation, that curation must be encoded as logic or as a derived report step, not as hand edits inside `results/`.

## 5. Canonical contracts and schemas

### 5.1 StrategySpec

Implement as Pydantic models under `src/nfo/specs/`.

Required top-level structure:

- `strategy_id`
- `strategy_version`
- `description`
- `universe`
- `feature_set`
- `trigger_rule`
- `selection_rule`
- `entry_rule`
- `exit_rule`
- `capital_rule`
- `slippage_rule`
- `report_defaults`

Required nested models:

- `UniverseSpec`
  - underlyings
  - delta target and tolerance
  - width rule
  - DTE target and tolerance
  - allowed contract families

- `TriggerSpec`
  - score gates
  - specific-pass gates
  - event window semantics
  - feature thresholds
  - missing-data policy

- `SelectionSpec`
  - mode: `day_matched | cycle_matched | live_rule`
  - one-trade-per-cycle flag
  - preferred exit variant
  - canonical-trade chooser
  - width handling rule
  - tie-breaker ordering

- `EntrySpec`
  - earliest allowed entry relative to first fire
  - session snapping rule
  - exact entry timestamp convention
  - allowed pre-fire behavior (must be false for live-valid specs)

- `ExitSpec`
  - PT50 / PT25 / PT75 / HTE / DTE2 or generalized exit config
  - manage-at-DTE semantics
  - expiry-settlement semantics

- `CapitalSpec`
  - fixed capital
  - deployment fraction
  - compounding yes/no
  - lot rounding mode

- `SlippageSpec`
  - flat rupees per lot
  - or future extensible model type

### 5.2 StudySpec

Separate study configuration from strategy configuration.

Required fields:

- `study_id`
- `study_type`
  - `variant_comparison`
  - `time_split`
  - `capital_analysis`
  - `robustness`
  - `falsification`
  - `live_replay`
  - `monitor_snapshot`
- `strategy_spec_ref`
- `dataset_refs`
- `parameters`
- `output_profile`

### 5.3 RunManifest

Every run must write `manifest.json`.

Required fields:

- `run_id`
- `created_at`
- `code_version`
- `study_spec_hash`
- `strategy_spec_hash`
- `dataset_hashes`
- `window_start`
- `window_end`
- `artifacts`
- `status`
- `warnings`
- `stale_inputs_detected`

### 5.4 Canonical identifiers

Introduce the following stable identifiers:

- `feature_day_id`
  - `<underlying>:<date>`
- `cycle_id`
  - `<underlying>:<target_expiry>:<strategy_version>`
- `fire_id`
  - `<cycle_id>:<fire_date>`
- `trade_id`
  - stable hash or deterministic composite from underlying, expiry, width, delta target, exit family, entry date
- `selection_id`
  - `<cycle_id>:<selection_mode>:<exit_variant>`

These ids are required so reports can reconcile rows across datasets and studies.

## 6. Target package structure

The NFO system should be reorganized under `src/nfo/` like this:

```text
src/nfo/
  specs/
    strategy.py
    study.py
    manifest.py
  datasets/
    raw.py
    normalized.py
    features.py
    trade_universe.py
    manifests.py
  engine/
    triggers.py
    cycles.py
    selection.py
    entry.py
    exits.py
    execution.py
    capital.py
    metrics.py
  studies/
    variant_comparison.py
    time_split.py
    capital_analysis.py
    robustness.py
    falsification.py
    live_replay.py
  monitor/
    snapshot.py
    transitions.py
    parity.py
  reporting/
    markdown.py
    tables.py
    artifacts.py
    index.py
```

Rules for this structure:

- `datasets/` may load and normalize data, but must not define strategy semantics.
- `engine/` owns all trade/firing/cycle/entry/exit behavior.
- `studies/` compose engine + datasets into analysis jobs.
- `reporting/` renders structured study outputs.
- `scripts/nfo/` become thin CLI wrappers only.

## 7. Dataset pipeline

### 7.1 Dataset stages

Standardize the NFO research pipeline into these stages:

1. `raw`
   - cached underlying bars
   - VIX bars
   - option rolling parquets
   - event cache

2. `normalized`
   - standardized symbol/date/time columns
   - deduped and sorted
   - explicit timezone/date semantics

3. `features`
   - daily feature table per underlying
   - VIX, percentile, IV-RV, IV rank, ATR, trend, event flags, skew, term structure

4. `trade_universe`
   - all candidate trades with metadata and realized outcomes
   - no strategy filtering yet

5. `study_inputs`
   - joins between features, cycles, fire dates, trade universe, and study parameters

### 7.2 Dataset manifests

Each dataset build must produce:

- parquet output
- manifest JSON
- summary markdown

Required dataset manifest fields:

- `dataset_id`
- `dataset_type`
- `source_paths`
- `date_window`
- `row_count`
- `build_time`
- `code_version`
- `upstream_datasets`

### 7.3 Staleness rules

A report is stale if:

- its strategy spec hash differs from current canonical hash for that named strategy
- its dataset manifests are older than the datasets it claims to consume
- its declared inputs do not exist

The platform should not silently overwrite semantic mismatches. It should either:

- write a new run directory, or
- mark prior runs stale in the index

## 8. Canonical execution semantics

### 8.1 Required platform rule

The live-valid rule is:

- `entry_date >= first_fire_date`

If the first fire date is not a trading session:

- snap forward to the next valid session
- never snap backward

This must be a shared engine contract, not a script-specific rule.

### 8.2 Selection modes

Implement exactly these modes:

- `day_matched`
  - select all trades satisfying spec universe whose `entry_date` is a fire date

- `cycle_matched`
  - group fires by `cycle_id`
  - choose one trade using declared selection rule
  - entry may be canonical grid entry only if spec explicitly permits it

- `live_rule`
  - group fires by `cycle_id`
  - derive entry from `first_fire_date`
  - run full trade simulation from that date
  - no precomputed trade rows that violate entry timing are allowed

### 8.3 Width handling

Width must be explicit in the strategy spec:

- fixed width
- width chosen by formula
- width chosen by risk budget

Mixing widths inside the same study is allowed only if the strategy spec explicitly defines the rule.

### 8.4 Underlying handling

The platform must make underlying inclusion explicit. A report must not silently broaden from NIFTY to BANKNIFTY due only to entry-date coincidence unless the universe spec explicitly allows it.

## 9. Reporting platform

### 9.1 Run-scoped layout

Canonical output directory:

```text
results/nfo/runs/<run_id>/
```

Minimum contents:

- `manifest.json`
- `metrics.json`
- `tables/`
- `report.md`
- `logs/`

Optional:

- plots
- parquet snapshots
- debug traces

### 9.2 Top-level `results/nfo/`

Top-level `results/nfo/` should no longer contain canonical mutable outputs except:

- `index.md`
- `latest.json`
- optional symlinks or small pointer files to latest run by study family

### 9.3 Standard report families

Define these canonical report families:

- `dataset_health`
- `historical_features`
- `variant_comparison`
- `time_split`
- `capital_analysis`
- `robustness`
- `falsification`
- `live_replay`
- `monitor_history`
- `master_summary`

### 9.4 Report header contract

Every report must declare:

- run id
- study type
- strategy spec id/version
- selection mode
- entry rule
- exit rule
- universe
- date window
- dataset ids/hashes
- created time

### 9.5 Narrative reports

Narrative markdown is allowed only if it is generated from canonical metrics and manifests.

Manual reports like:

- `tier1_report.md`
- `backtest_rerun_plain.md`
- unsuffixed capital reports

must be marked deprecated and eventually removed or regenerated from the canonical report layer.

## 10. Regime monitor integration

### 10.1 Goal

`regime_watch.py` must become a consumer of the same feature and trigger engine used in research.

### 10.2 MonitorSnapshot schema

Introduce a structured snapshot model:

- `snapshot_id`
- `timestamp`
- `strategy_spec_id`
- `underlying`
- `cycle_id`
- `target_expiry`
- `current_state`
- `first_fire_date`
- `current_grade`
- `trigger_passed`
- `trigger_details`
- `selection_preview`
- `proposed_trade`
- `reason_codes`

### 10.3 Monitor state machine

State transitions should be explicit:

- `idle`
- `watch`
- `fire`
- `entered`
- `invalidated`
- `expired`

### 10.4 Parity requirements

Given the same feature inputs and strategy spec:

- `regime_watch.py` and historical replay must produce the same fire/non-fire decision
- monitor snapshots must be replayable into study artifacts

## 11. Script migration plan

### 11.1 Keep script names, shrink responsibilities

The following scripts remain as CLI entrypoints, but stop owning business logic:

- `historical_backtest.py`
- `redesign_variants.py`
- `time_split_validate.py`
- `v3_capital_analysis.py`
- `v3_robustness.py`
- `v3_falsification.py`
- `v3_live_rule_backtest.py`
- `entry_perturbation_backtest.py`
- `exit_sweep_backtest.py`
- `regime_watch.py`

### 11.2 CLI pattern

Each script should:

1. parse args
2. load a `StudySpec`
3. invoke a `src/nfo/studies/...` function
4. write run-scoped artifacts through `reporting/`
5. print the run location

### 11.3 No direct report writing from business logic

Business logic may return structured tables, metrics, and warnings. Rendering and filesystem layout must happen in the reporting layer.

## 12. Testing strategy

### 12.1 Unit tests

Add or expand tests for:

- spec validation
- canonical ids
- fire-date generation
- cycle grouping
- `day_matched` selection
- `cycle_matched` selection
- `live_rule` selection and entry-date enforcement
- width and underlying filtering
- report manifest generation
- stale detection

### 12.2 Golden tests

Create golden fixtures for:

- frozen V3 cycles
- known trigger windows
- live-rule replay outputs
- monitor snapshots for known dates

### 12.3 Cross-report consistency tests

For a single run id:

- capital analysis selected trades must reconcile with robustness selected trades
- falsification matched set must reconcile with live-replay matched set when using the same strategy and selection mode
- report headers must match manifest metadata exactly

### 12.4 CLI smoke tests

Add smoke tests that:

- run each study from cached data
- emit a run directory
- verify required artifact set exists

## 13. Migration phases

### Phase 1 — Foundation and contracts

Deliverables:

- `specs/` models
- run manifests
- run directory layout
- top-level results index
- canonical ids

Behavior:

- no major semantic change yet
- existing scripts may continue to call old logic
- they must start writing run-scoped outputs

Acceptance:

- every regenerated study creates a run directory with manifest
- top-level `results/nfo/index.md` lists latest runs

### Phase 2 — Engine extraction

Deliverables:

- shared trigger, cycle, selection, entry, exit, capital modules
- script wrappers updated to use engine modules

Behavior:

- preserve current outputs where semantics are intentionally unchanged
- add parity tests against legacy behavior for transitional scripts

Acceptance:

- no selection logic duplicated in scripts
- scripts import studies/engine only

### Phase 3 — Canonical live-valid execution

Deliverables:

- `live_rule` as first-class selection mode
- `entry_date >= first_fire_date` centralized
- live replay integrated into platform studies

Behavior:

- current look-ahead-prone cycle reports are deprecated
- new capital, robustness, and falsification runs must declare whether they are `cycle_matched` or `live_rule`

Acceptance:

- same spec can be run in both cycle-matched and live-rule modes
- differences are visible and traceable through manifests

### Phase 4 — Reporting cleanup and monitor convergence

Deliverables:

- generated master summary
- deprecated old narrative reports removed or replaced
- `regime_watch.py` fully platform-backed

Behavior:

- monitor and research parity checks added
- stale top-level files removed

Acceptance:

- no canonical report is maintained outside a run directory
- live monitor uses the same trigger engine as backtests

## 14. File and artifact cleanup policy

### 14.1 Deprecate immediately

Mark as deprecated in docs/index once replacements exist:

- `results/nfo/tier1_report.md`
- `results/nfo/backtest_rerun_plain.md`
- `results/nfo/v3_capital_report.md`
- `results/nfo/v3_capital_trades.csv`

### 14.2 Preserve only as historical snapshots

Old reports may remain only if:

- clearly tagged as legacy
- linked to the exact methodology they used
- never presented as latest

### 14.3 Package/test cleanup

Current test import behavior depends on extending `sys.path` into `scripts/nfo`. Long-term goal:

- all business logic importable from `src/nfo`
- tests only reach scripts to verify CLI wiring, not core logic

## 15. Operational workflow after refactor

Desired operator workflow:

1. Build or refresh datasets.
2. Run a named study against a named strategy spec.
3. Receive a run directory plus top-level index update.
4. Compare runs by spec id, window, and methodology.
5. Replay the same strategy through live-monitor snapshots.

Desired commands, conceptually:

```bash
python -m nfo.datasets.build --dataset historical_features --window 2024-01-15:2026-04-20
python -m nfo.studies.run --study variant_comparison --strategy configs/nfo/strategies/v3_frozen.json
python -m nfo.studies.run --study live_replay --strategy configs/nfo/strategies/v3_frozen.json
python -m nfo.monitor.run --strategy configs/nfo/strategies/v3_frozen.json
```

The exact CLI naming can differ, but this operator shape is the target.

## 16. Acceptance criteria for the full platform

The refactor is done only when all of the following are true:

- every study runs from validated spec objects
- every study writes a manifest-backed run directory
- every report declares its methodology explicitly
- no business-critical selection logic lives only in scripts
- `entry_date >= first_fire_date` is enforced by the shared live-rule engine
- live monitor and historical replay agree on trigger dates for the same spec
- stale reports are either impossible or automatically marked
- top-level `results/nfo/` is an index, not a dumping ground
- old narrative reports no longer masquerade as current truth

## 17. Defaults and assumptions chosen in this plan

- Primary priority is research-platform integrity, not shipping a production alerting stack first.
- Backward compatibility of old top-level report paths is not a hard requirement; pointer files are acceptable during migration.
- Pydantic is the correct schema mechanism because it is already a dependency.
- Existing script names should be preserved initially for continuity, but only as wrappers.
- The current NFO work should remain inside this repo; no immediate monorepo split is assumed.
- `v3_live_rule_backtest.py` is treated as proof that live-valid semantics are required and should be centralized, not kept as a side study forever.

## 18. Immediate implementation order for the engineering agent

Implement in this exact order:

1. Add spec and manifest models under `src/nfo/specs/`.
2. Add run directory writer and top-level index support under `src/nfo/reporting/`.
3. Centralize cycle ids and selection ids in `src/nfo/engine/`.
4. Extract trigger/cycle/selection/entry logic from current scripts into `src/nfo/engine/`.
5. Refactor `v3_live_rule_backtest.py` semantics into the shared engine.
6. Refactor `v3_capital_analysis.py`, `v3_robustness.py`, and `v3_falsification.py` to use the engine/studies path.
7. Refactor `redesign_variants.py` and `time_split_validate.py` to declare and use explicit selection mode instead of implicit date-matching.
8. Migrate `regime_watch.py` to the same trigger engine and structured snapshot model.
9. Add cross-report consistency and manifest tests.
10. Deprecate stale outputs and switch top-level results to index/pointer mode.

This order is important because it separates platform scaffolding from semantic changes and prevents the repo from entering another half-migrated state.
