# NFO Platform Phase 4 — Monitor Convergence & Reporting Cleanup Implementation Plan

> **For agentic workers:** `superpowers:subagent-driven-development` with fresh subagent per bundle. TDD enforced.

**Goal:** Complete master design §13.4 — `regime_watch.py` becomes a consumer of `engine.triggers`, `MonitorSnapshot` emits per run, monitor↔research parity verified, master summary auto-generated, legacy narrative reports archived under `results/nfo/legacy/`.

**Architecture:** Strangler-fig. `engine/triggers.py` is already the SoT for firing decisions (P2). In P4, `scripts/nfo/regime_watch.py` swaps its `_compute_v3_gate` call for `engine.triggers.TriggerEvaluator` on the loaded V3 spec. TUI/Parallel/Dhan/interactive layers in regime_watch are UNCHANGED — the migration is surgical at the decision layer only.

**Master design reference:** `docs/superpowers/specs/2026-04-21-nfo-research-platform-design.md` §9 (MonitorSnapshot schema, state machine), §10.3 (transitions), §10.4 (parity), §13.4, §14 (cleanup policy).

**Tech stack:** Python 3.14 via `.venv/bin/python`. Pydantic v2. pandas. pytest.

## Scope boundaries

**In scope for P4:**
- `src/nfo/monitor/snapshot.py` — `MonitorSnapshot` producer
- `src/nfo/monitor/transitions.py` — pure state machine
- `src/nfo/monitor/store.py` — JSONL per-day storage
- `src/nfo/monitor/parity.py` — `compare_monitor_vs_research`
- `scripts/nfo/regime_watch.py` — migrate V3 gate decision through `engine.triggers`; emit `MonitorSnapshot` JSONL
- `src/nfo/reporting/master_summary.py` — generated summary across runs
- Legacy artifact archival under `results/nfo/legacy/`

**Deferred to P5 (separate phase):**
- Full dataset pipeline (`datasets/{raw,normalized,features,trade_universe,study_inputs}.py`)
- Legacy body replacement for `v3_capital_analysis`, `v3_robustness`, `v3_falsification`, `time_split_validate`, `v3_live_rule_backtest`

---

## Execution conventions

- TDD: failing test → observe fail → implement → observe pass → commit.
- Pydantic v2; `model_config = ConfigDict(extra="forbid")` on every model.
- `from __future__ import annotations` on every new module.
- Commit style: Conventional Commits.

---

## Bundle A — Monitor schemas + state machine (Tasks P4-A1, P4-A2)

### Task P4-A1: `monitor/__init__.py` + `MonitorSnapshot` Pydantic model

**Files:**
- Create: `src/nfo/monitor/__init__.py` (docstring)
- Create: `src/nfo/monitor/snapshot.py` — contains `MonitorSnapshot` Pydantic model ONLY in this task (producer fn ships in Bundle B)
- Create: `tests/nfo/monitor/__init__.py` (empty)
- Create: `tests/nfo/monitor/test_snapshot_model.py`

**Contract (master design §9.1):**
```python
class MonitorSnapshot(BaseModel):
    # snapshot_id = sha1(canonical_json({
    #     "strategy_id": ..., "strategy_version": ...,
    #     "underlying": ..., "timestamp": iso_string,
    # })).hexdigest()[:16]
    snapshot_id: str
    timestamp: datetime                 # UTC
    strategy_spec_id: str               # strategy_id
    strategy_version: str
    strategy_spec_hash: str
    underlying: Literal["NIFTY","BANKNIFTY","FINNIFTY"]
    cycle_id: str
    target_expiry: date
    current_state: Literal["idle","watch","fire","entered","invalidated","expired"]
    first_fire_date: date | None
    current_grade: str                  # "A+"/"A"/"B"/...
    trigger_passed: bool
    trigger_details: dict[str, Any]     # per-gate pass/fail + raw values
    selection_preview: dict[str, Any] | None
    proposed_trade: dict[str, Any] | None
    reason_codes: list[str]
```

Also add a `build_snapshot_id(...)` helper function deterministically producing the 16-hex id.

**Steps:**
1. Write `tests/nfo/monitor/test_snapshot_model.py` covering:
   - model roundtrip (model_dump_json → model_validate_json)
   - `build_snapshot_id` is 16 hex chars and deterministic across field-order
   - `current_state` rejects unknown literals
   - `underlying` rejects unknown literals
   - `target_expiry` serializes as ISO date
2. Observe failures.
3. Implement `src/nfo/monitor/__init__.py` with a module docstring only: `"""Monitor: live regime snapshots, state machine, research parity (master design §9)."""`
4. Implement `src/nfo/monitor/snapshot.py` with `MonitorSnapshot` model and `build_snapshot_id()`.
5. Run, green.
6. Commit: `feat(monitor): add MonitorSnapshot schema and snapshot_id helper`.

### Task P4-A2: `monitor/transitions.py` — pure state machine

**Contract (master design §9.2):**
```python
State = Literal["idle","watch","fire","entered","invalidated","expired"]


@dataclass
class Evidence:
    trigger_passed: bool
    is_entered: bool         # whether a live trade placement was confirmed
    is_expired: bool         # cycle past target_expiry
    is_invalidated: bool     # spec-driven invalidation


def next_state(current: State, evidence: Evidence) -> tuple[State, list[str]]:
    """Pure state transition. Returns (new_state, reason_codes).

    Transitions (master design §10.3):
      idle → watch        : cycle begins (first session in the pre-expiry window)
      watch → fire        : trigger passes
      fire → entered      : live placement confirmed (not modeled by the monitor
                            itself; set externally or via replay when the CSV
                            shows the trade actually happened)
      fire → invalidated  : trigger no longer valid OR spec-defined invalidation
      entered → expired   : cycle reached target_expiry / exited
      {fire, watch} → expired : cycle past expiry without entering
    """
```

**Steps:**
1. Write `tests/nfo/monitor/test_transitions.py`:
   - idle→watch on new cycle start (no trigger required)
   - watch→fire when trigger_passed=True
   - fire→entered when is_entered=True
   - fire→invalidated when trigger_passed=False (drop-off)
   - entered→expired when is_expired=True
   - watch→expired when is_expired=True without trigger firing
   - fire→expired when is_expired=True without entry
   - reason_codes list non-empty on every transition
2. Implement `src/nfo/monitor/transitions.py`.
3. Commit: `feat(monitor): add pure-function state machine for cycle transitions`.

**Acceptance for Bundle A:** tests/nfo/monitor/ green (model + transitions tests). Full suite green.

---

## Bundle B — Snapshot producer + JSONL store

### Task P4-B1: `monitor/snapshot.py::capture_snapshot` + `monitor/store.py`

**Contract (master design §9.1 storage):**
- `capture_snapshot(*, spec, features_row, ...)` produces a `MonitorSnapshot` using `engine.triggers.TriggerEvaluator(spec).evaluate_row(features_row)`.
- `store.append_snapshot(snapshot, root=Path(...))` appends to `data/nfo/monitor_snapshots/<YYYY-MM-DD>.jsonl` (one file per snapshot day).
- `store.load_snapshots(root, *, start=None, end=None)` returns a list of snapshots for replay.

**Files:**
- Modify: `src/nfo/monitor/snapshot.py` (append `capture_snapshot`)
- Create: `src/nfo/monitor/store.py`
- Create: `tests/nfo/monitor/test_snapshot_capture.py`
- Create: `tests/nfo/monitor/test_store.py`

**Steps:**
1. Write failing tests.
2. Implement `capture_snapshot` that:
   - Runs `TriggerEvaluator(spec, event_resolver=...).evaluate_row(features_row, atr_value=...)`
   - Constructs cycle_id via `engine.cycles.cycle_id(...)`
   - Computes `build_snapshot_id(...)`
   - Populates trigger_details from FireRow.detail
   - Leaves selection_preview/proposed_trade as None in P4 (those depend on live Dhan lookup)
3. Implement `store.append_snapshot` / `store.load_snapshots`. Each day's JSONL is append-only; files never rewritten. Load is a simple `json.loads` per line across the date range.
4. Commits:
   - `feat(monitor): add capture_snapshot producer using engine.triggers`
   - `feat(monitor): add per-day JSONL snapshot store (append-only)`

---

## Bundle C — Monitor↔research parity

### Task P4-C1: `monitor/parity.py::compare_monitor_vs_research`

**Contract (master design §10.4):**
```python
@dataclass
class ParityReport:
    total_snapshots: int
    matched: int
    mismatches: list[dict]   # {date, engine_trigger, monitor_trigger, diff_detail}
    within_tolerance: bool


def compare_monitor_vs_research(
    *,
    spec: StrategySpec,
    monitor_jsonl_root: Path,
    features_df: pd.DataFrame,
    atr_series: pd.Series,
    tolerance: float = 1e-9,
) -> ParityReport:
    """Replay historical features through engine.triggers.TriggerEvaluator and
    compare fire/no-fire decisions against stored monitor snapshots.

    Disagreements are bugs. 1e-9 float tolerance on raw gate values; exact
    equality on trigger_passed boolean.
    """
```

**Files:**
- Create: `src/nfo/monitor/parity.py`
- Create: `tests/nfo/monitor/test_parity.py`

**Steps:**
1. Write failing tests using synthetic features + hand-built JSONL snapshots — happy path (all match) and mismatch path (one disagreement → mismatches list non-empty).
2. Implement `compare_monitor_vs_research`.
3. Commit: `feat(monitor): add monitor↔research parity (compare_monitor_vs_research)`.

---

## Bundle D — regime_watch.py migration

### Task P4-D1: swap `_compute_v3_gate` decision to `engine.triggers` + emit MonitorSnapshot

**Approach (surgical):**
- `scripts/nfo/regime_watch.py::_compute_v3_gate` currently computes the V3 gate in-script. P4 keeps this function for backward-compatible returns (it produces the `V3Gate` namedtuple the TUI renders), but swaps its decision core for `engine.triggers.TriggerEvaluator(v3_spec).evaluate_row(...)`.
- After the gate decision, call `monitor.snapshot.capture_snapshot(...)` and `monitor.store.append_snapshot(...)`.
- Add a parity test `tests/nfo/scripts/test_regime_watch_gate_parity.py` that asserts: given the same features row, `_compute_v3_gate` (post-migration) and a direct `TriggerEvaluator.evaluate_row` call produce the same `passed` boolean.

**Files:**
- Modify: `scripts/nfo/regime_watch.py`
- Create: `tests/nfo/scripts/test_regime_watch_gate_parity.py`

**Steps:**
1. Read `scripts/nfo/regime_watch.py` lines 663–770 (`_compute_v3_gate`) to understand inputs + outputs.
2. Write failing parity test.
3. Replace the decision core of `_compute_v3_gate` with `TriggerEvaluator`. Preserve the outer V3Gate namedtuple and the logging that TUI depends on.
4. Add MonitorSnapshot emit at the end of the gate computation (wrapped in try/except — TUI must never crash on monitor store errors; log and continue).
5. Run parity test; verify other existing tests (`tests/nfo/test_regime_watch.py`) still pass.
6. Commit: `refactor(regime_watch): route V3 gate through engine.triggers; emit MonitorSnapshot`.

---

## Bundle E — Master summary + legacy archival

### Task P4-E1: `reporting/master_summary.py`

**Contract:**
- `generate_master_summary(*, runs_root, out_path, sources) -> MasterSummaryResult`
- Reads all runs, groups by strategy_id + selection_mode, writes a single `results/nfo/master_summary.md` with:
  - Latest run per (study_type, strategy, selection_mode)
  - Headline metrics from `metrics.json`
  - Stale markers

**Files:**
- Create: `src/nfo/reporting/master_summary.py`
- Create: `tests/nfo/reporting/test_master_summary.py`

**Steps:**
1. Write failing tests using synthetic run directories.
2. Implement generator.
3. Commit: `feat(reporting): add master_summary generator`.

### Task P4-E2: Legacy artifact archival + stale cleanup

**Approach:**
- Create `results/nfo/legacy/` directory.
- Move these files to `legacy/` (they are pre-platform narrative reports):
  - `results/nfo/tier1_report.md`
  - `results/nfo/backtest_rerun_plain.md`
  - `results/nfo/v3_capital_report.md` (unsuffixed legacy)
  - `results/nfo/v3_capital_trades.csv` (unsuffixed legacy)
  - `results/nfo/v3_master_analysis.md`
- Write pointer stub at each old location (markdown `See [legacy/…]` or nothing — rely on gitignore).

Actually a cleaner approach: leave the actual files in `results/nfo/` untouched (they are generated outputs), but add a `results/nfo/legacy/README.md` that lists which files are deprecated + why. The index generator marks their study-type (if any) as stale via the existing mechanism.

**Files:**
- Create: `results/nfo/legacy/README.md`
- Modify: `src/nfo/reporting/index.py` — add a "Deprecated Files" section to `index.md` listing items in `results/nfo/legacy/`

**Steps:**
1. Create the legacy README.
2. Add an optional `legacy_readme_path` parameter to `generate_index`; if present, embed a `## Deprecated` section listing the items.
3. Tests for the new index section.
4. Commit: `docs(results): mark pre-platform narrative reports as deprecated`.

---

## Bundle F — P4 Acceptance + tag

1. Run full suite (including smoke).
2. Regenerate the run index + master summary.
3. Acceptance checklist:
   - [ ] `src/nfo/monitor/{snapshot,transitions,store,parity}.py` all present with passing tests.
   - [ ] `scripts/nfo/regime_watch.py::_compute_v3_gate` uses `engine.triggers.TriggerEvaluator` internally; parity test green.
   - [ ] `src/nfo/reporting/master_summary.py` generates `results/nfo/master_summary.md`.
   - [ ] `results/nfo/legacy/README.md` exists and deprecates the listed pre-platform narrative reports.
   - [ ] Full test suite green.
   - [ ] Master design §12 acceptance items 4 + 6 re-verified.
4. Write `docs/superpowers/plans/2026-04-22-nfo-platform-phase4-completion.md`.
5. Commit + tag `p4-complete`.

## Deferrals (post-P4)

Explicitly out of P4 scope — deferred to a separate P5 phase:
- Full dataset pipeline (`datasets/{raw,normalized,features,trade_universe,study_inputs}.py`)
- Legacy body replacement for 5 scripts

---

*End of Phase 4 implementation plan.*
