# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`nfo-platform` — spec-driven research platform for NIFTY/BANKNIFTY F&O credit-spread strategies backed by Dhan v2 for historical + live data. The platform is the product of a 6-phase refactor (P1–P6). The master design at `docs/superpowers/specs/2026-04-21-nfo-research-platform-design.md` is the authoritative contract — read it first when working on anything architectural.

## Commands

- Install: `.venv/bin/pip install -e .` (editable, pyproject `name = "nfo-platform"`)
- Run full test suite: `.venv/bin/python -m pytest tests/nfo/ -q` (~2 min; includes smoke)
- Run a single test file: `.venv/bin/python -m pytest tests/nfo/engine/test_triggers.py -v`
- Run a single test: `.venv/bin/python -m pytest tests/nfo/engine/test_triggers.py::test_evaluator_fires_when_all_gates_pass -v`
- Skip slow smoke tests: `.venv/bin/python -m pytest tests/nfo/ -q --ignore=tests/nfo/smoke`
- Regenerate run index + master summary: `.venv/bin/python -m nfo.reporting`
- Seed the canonical datasets (one-shot): `.venv/bin/python scripts/nfo/p6_seed_datasets.py`
- Run a study (all take `wrap_legacy_run` + emit `results/nfo/runs/<run_id>/`):
  - `.venv/bin/python scripts/nfo/v3_capital_analysis.py --pt-variant hte`
  - `.venv/bin/python scripts/nfo/v3_robustness.py`
  - `.venv/bin/python scripts/nfo/v3_falsification.py`
  - `.venv/bin/python scripts/nfo/v3_live_rule_backtest.py`
  - `.venv/bin/python scripts/nfo/redesign_variants.py`
  - `.venv/bin/python scripts/nfo/time_split_validate.py`
- Live regime TUI: `.venv/bin/python scripts/nfo/regime_watch.py --tui` (requires Dhan + Parallel creds in `.env`)
- Python runtime on this machine is 3.14 via `.venv/`.

## Architecture

Read the master design for the full story. The two-minute version:

**Spec → engine → studies → scripts → manifest-backed runs.** Every study runs from a validated Pydantic `StrategySpec` (`configs/nfo/strategies/*.yaml`) through `src/nfo/engine/` primitives composed into `src/nfo/studies/*.py`, called by a thin `_legacy_main()` in `scripts/nfo/*.py` wrapped by `nfo.reporting.wrap_legacy_run` which writes `results/nfo/runs/<run_id>/{manifest.json, metrics.json, report.md, tables/, logs/}`.

**Layer responsibilities:**
- `src/nfo/specs/` — Pydantic v2 models (`StrategySpec` + 7 nested, `StudySpec`, `RunManifest`, `DatasetManifest`) + canonical JSON hashing + YAML loader with `StrategyDriftError` drift detection.
- `src/nfo/engine/` — eight modules (`triggers`, `cycles`, `entry`, `selection`, `exits`, `execution`, `capital`, `metrics`) that own all trade/firing/cycle/entry/exit business logic. Two are platform invariants (master §12 item 3): `engine.triggers.TriggerEvaluator` is the ONLY code path that decides whether a day fires; `engine.entry.resolve_entry_date` is the ONLY code path that resolves entry dates in `live_rule` mode. Do not bypass them.
- `src/nfo/studies/` — six orchestration modules (`variant_comparison`, `live_replay`, `capital_analysis`, `time_split`, `robustness`, `falsification`) composing engine primitives end-to-end.
- `src/nfo/datasets/` — `features.py` + `trade_universe.py` ingest existing parquet/CSV → `data/nfo/datasets/<stage>/<dataset_id>/{dataset.parquet, manifest.json}`. Drift detected via `staleness.is_run_stale` + `reporting.hash_sources.filesystem_hash_sources`.
- `src/nfo/reporting/` — `artifacts.RunDirectory` writer, `methodology_header.build_header` (auto-prepended to every report.md), `index.generate_index` + `master_summary.generate_master_summary`, and `wrap_legacy_run` which glues strategy loading, run-id construction, manifest assembly, legacy artifact mirroring, and dataset-hash population.
- `src/nfo/monitor/` — `MonitorSnapshot` Pydantic model, pure-function state machine (`idle/watch/fire/entered/invalidated/expired`), per-day JSONL store, and `compare_monitor_vs_research` parity.
- `scripts/nfo/` — thin CLI wrappers. Each V3-era script has two top-level functions: `_legacy_main()` (≤120 active lines) which calls into a studies module, and `main()` which invokes `wrap_legacy_run(...)` around it. `regime_watch.py` is intentionally left at ~2400 LOC (TUI-heavy, out of scope per master design §11.1) but its V3 gate decision routes through `engine.triggers`.

**Three evaluation modes** (`SelectionSpec.mode`): `day_matched` (all trades entered on firing days), `cycle_matched` (one canonical trade per cycle), `live_rule` (one trade per cycle with `entry_date >= first_fire_date`, forward-snap only). These are named first-class modes, not implicit behaviors — always declare explicitly in the strategy YAML.

**Strategy drift detection.** `configs/nfo/.registry.json` records `(strategy_id, strategy_version) -> content_hash` for every loaded strategy. Editing a YAML's content without bumping `strategy_version` raises `StrategyDriftError` at load time. Bump version before editing. The two shipped strategies are `v3@3.0.0` (cycle_matched), `v3@3.0.1` (live_rule hte), `v3@3.0.2` (live_rule pt50).

**Dataset drift detection.** Every run's `RunManifest.dataset_hashes` records sha256 of each referenced dataset's parquet. When index regenerates, any run whose declared hash differs from the current dataset manifest is flagged stale with reason code `dataset_hash_changed:<dataset_id>`.

**Strangler-fig history.** The codebase preserves legacy script bodies/helpers at module scope alongside the new engine/studies path. Parity tests (under `tests/nfo/scripts/test_*_body_parity.py` and `tests/nfo/engine/test_*_parity.py`) assert byte-exact equality on categorical columns and 1e-6 relative tolerance on floats. These tests are load-bearing — when modifying engine logic, they are the regression guard. Never loosen tolerance without documenting why.

## Working in this codebase

- **TDD is the enforced rhythm** for everything added during P1–P6: write failing test → observe fail → implement → observe pass → commit. Follow the same pattern when extending.
- **Subagents expect the test file first.** When asked to add logic, always write tests first and run them to confirm red before implementing.
- **Commit style:** Conventional Commits (`feat:`, `test:`, `refactor:`, `docs:`, `chore:`, `style:`). Most commits touch one subsystem and ship one parity or unit proof.
- **Tagged phase checkpoints** exist: `p1-complete` through `p6-complete`. Use `git log --oneline <prev-tag>..<next-tag>` to see what each phase shipped. Each phase also has a completion report under `docs/superpowers/plans/`.
- **Test registry fixture.** Many tests use `reset_registry_for_tests(tmp_path/"registry.json")` to isolate the drift-detection registry. If a new test fails with `StrategyDriftError`, it probably lacks this fixture. Pattern: `@pytest.fixture(autouse=True) def _iso_registry(tmp_path): reset_registry_for_tests(tmp_path/"registry.json")`.
- **Event resolver seam.** `TriggerEvaluator(spec, event_resolver=...)` accepts an optional `(entry_date, dte) -> "high"|"medium"|"low"|"none"` callable so the engine's trigger logic stays data-layer-agnostic. The features parquet doesn't currently carry `event_risk_v3`; legacy tests + studies import `scripts/nfo/redesign_variants._event_pass` via `importlib` and wrap it as a resolver.
- **`scripts/nfo/` isn't a Python package.** Legacy scripts import sibling scripts via `importlib.util.spec_from_file_location`. Don't try to `from scripts.nfo.X import Y` — it won't work.
- **`tests/nfo/conftest.py`** injects `scripts/nfo/` onto `sys.path` so tests can `import redesign_variants` directly.
- **Timezone convention:** all dates are IST-anchored (see `src/nfo/config.py`). `run_id` timestamps are UTC-formatted (`build_run_id` in `engine.cycles`). Don't round-trip through UTC-naive timestamps.
- **Legacy CSV schema parity.** Three scripts (`v3_robustness`, `v3_falsification`, `time_split_validate`) carry module-level reshape helpers that project engine results back into the legacy wide CSV schema. These helpers are intentional — they keep pre-platform consumers of the CSV working while the platform produces long-form data internally.

## Results directory layout

- `results/nfo/runs/<run_id>/` — canonical run outputs. Each run dir has `manifest.json`, `metrics.json`, `tables/*.csv`, `report.md` (with methodology header), `logs/`. **Gitignored** — runs are ephemeral, regenerate from spec.
- `results/nfo/index.md` — generated index of all runs grouped by `study_type` with stale markers. Gitignored.
- `results/nfo/latest.json` — generated `{study_type: {run_id, path, created_at}}`. Gitignored.
- `results/nfo/master_summary.md` — generated cross-study summary. Gitignored.
- `results/nfo/legacy/README.md` — deprecation list for pre-platform narrative reports.
- `results/nfo/{spread_trades.csv, historical_signals.parquet, …}` — tracked inputs used by `scripts/nfo/p6_seed_datasets.py` to build `data/nfo/datasets/`.

## Things not to touch without strong reason

- `src/nfo/engine/triggers.py` + `src/nfo/engine/entry.py` — platform invariant modules. Changes require a master-design change and parity-test update.
- `scripts/nfo/regime_watch.py` TUI/Parallel/Dhan layers — only the V3 gate decision layer (~`_compute_v3_gate`) was migrated in P4. The rest is intentionally out of refactor scope.
- `legacy/csp/` — archived predecessor project (US-equity cash-secured puts). Not installed, not maintained.
- Parity test tolerances (1e-6 / 1e-9) — document any loosening in the commit body.

## Phase plans + completion reports

The refactor's full history lives in `docs/superpowers/plans/`:
- Plans: `2026-04-21-nfo-platform-phase1-plan.md` through `2026-04-22-nfo-platform-phase6-plan.md`
- Completion reports: same dates, `-completion.md` suffix
- Master spec: `docs/superpowers/specs/2026-04-21-nfo-research-platform-design.md`

When asked to extend the platform (add a new study, new selection mode, new strategy spec version), read the master design first + the relevant phase completion report to see how similar work was sequenced.
