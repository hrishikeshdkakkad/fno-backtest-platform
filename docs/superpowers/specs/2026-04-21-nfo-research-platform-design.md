# NFO Research Platform — Master Design Specification

**Date:** 2026-04-21
**Status:** Master design. Source of truth for Phase 1–4 implementation plans.
**Inputs:** `docs/plans/2026-04-21-nfo-research-platform-ultraplan.md` (the ultraplan), `docs/REVIEW.md`, `docs/v3-spec-frozen.md`.
**Audience:** Engineering agents executing the phased refactor. Sub-project plans (P1–P4) reference this doc for schemas, invariants, and acceptance criteria.

---

## 0. How to use this document

This design is the platform contract. It is authoritative on:
- Module layout (§3)
- Pydantic schemas (§4)
- Canonical identifiers (§5)
- Evaluation semantics (§6)
- Dataset pipeline contracts (§7)
- Reporting layout and methodology headers (§8)
- Monitor schemas and state machine (§9)
- Master acceptance criteria (§12)

Phase plans (P1–P4) MUST NOT contradict this doc. If a phase plan needs a different contract, first revise this doc, then the plan. Every phase plan has a "References master design §X" section that cites the authoritative subsections.

Cross-references to the ultraplan use the form "ultraplan §N.N". Where this doc tightens or makes concrete an ultraplan choice, it says so explicitly.

---

## 1. Platform goals and motivation

The current NFO code under `src/nfo/`, `scripts/nfo/`, `results/nfo/` is three overlapping systems:

1. A day-matched research flow (`historical_backtest.py`, `redesign_variants.py`, `time_split_validate.py`).
2. A cycle-matched research flow (`robustness.py`, `v3_capital_analysis.py`, `v3_robustness.py`, `v3_falsification.py`).
3. A live/regime-monitor flow (`regime_watch.py`, `v3_live_rule_backtest.py`).

Each flow answers a slightly different research question, shares partial logic with the others, and writes outputs that can disagree about the "same" strategy. The platform replaces all three with a single system that:

- runs every study from a validated strategy spec,
- writes manifest-backed, run-scoped artifacts with declared methodology,
- shares one execution engine between historical research and live monitoring,
- treats `day_matched`, `cycle_matched`, and `live_rule` as first-class, named evaluation modes rather than implicit behaviors of different scripts,
- makes stale artifacts impossible or automatically detected.

Live/regime monitoring becomes a consumer of the research platform, not a parallel codepath. See ultraplan §1 and §3 for the original motivation.

---

## 2. Decomposition, phasing, migration strategy

### 2.1 Decomposition

This design document is the **master spec**. Implementation is split into four phase-level sub-projects, each with its own `superpowers:writing-plans` plan:

| Phase | Name | Ultraplan § | Scope |
|---|---|---|---|
| P1 | Foundation & Contracts | §13.1 | Git init, package rename, specs models, manifest models, run-dir writer, index, canonical ids, test scaffold |
| P2 | Engine Extraction | §13.2 | `datasets/` + `engine/` modules, scripts become thin wrappers, parity tests vs legacy |
| P3 | Canonical Live-Valid Execution | §13.3 | `live_rule` as first-class selection mode, centralize `entry_date >= first_fire_date`, deprecate look-ahead reports |
| P4 | Reporting Cleanup & Monitor Convergence | §13.4 | Master summary generator, full `regime_watch` migration, monitor/research parity, legacy artifact archival |

Each phase plan:
1. References this master doc for schemas and invariants.
2. Defines concrete files, build order, tests, and acceptance bar.
3. Deletes legacy code only after parity gates pass.

### 2.2 Phase ordering and migration style

**Ordering:** strictly P1 → P2 → P3 → P4, matching ultraplan §18.

**Migration style:** strangler-fig per phase. New modules are built alongside existing code. Phase N's new modules coexist with Phase N−1 scripts. A legacy path is deleted only after the new path passes its parity gate.

Rationale: ultraplan §13.2 explicitly requires parity tests against legacy behavior during engine extraction. That is only possible if old code still runs.

### 2.3 Prerequisite step (P1 step 1): git bootstrap

The repo is currently not under git. Phase 1 begins with:

1. `git init` at repo root.
2. `.gitignore` additions for `.venv/`, `__pycache__/`, `data/nfo/parallel_cache/`, large parquets under `data/nfo/rolling/`, etc.
3. First commit: `chore: import NFO research platform current state (pre-refactor)`.
4. Create branch `main`.

After this, the `code_version` field in `RunManifest` is `git rev-parse --short HEAD` + a `-dirty` suffix when `git status --porcelain` is non-empty.

---

## 3. Repository layout (target)

```text
nfo-platform/                           # (pyproject.toml name = "nfo-platform")
├── src/nfo/
│   ├── __init__.py
│   ├── config.py                       # RESULTS_DIR, DATA_DIR, REPO_ROOT, git helpers
│   ├── specs/
│   │   ├── __init__.py
│   │   ├── strategy.py                 # StrategySpec, UniverseSpec, TriggerSpec, SelectionSpec,
│   │   │                               #   EntrySpec, ExitSpec, CapitalSpec, SlippageSpec
│   │   ├── study.py                    # StudySpec, StudyType, DatasetRef
│   │   ├── manifest.py                 # RunManifest, DatasetManifest
│   │   ├── hashing.py                  # canonical_json, spec hash helpers
│   │   └── loader.py                   # load_strategy, load_study (YAML → validated models)
│   ├── datasets/
│   │   ├── __init__.py
│   │   ├── raw.py                      # underlying bars, VIX, option rolling, events
│   │   ├── normalized.py               # standardized symbol/date/tz
│   │   ├── features.py                 # daily feature table per underlying
│   │   ├── trade_universe.py           # candidate trades with metadata + realized outcomes
│   │   ├── study_inputs.py             # joins: features × cycles × fires × trade_universe
│   │   ├── manifests.py                # DatasetManifest writers
│   │   └── staleness.py                # detect upstream changes
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── triggers.py                 # TriggerEvaluator, evaluate_day, evaluate_window
│   │   ├── cycles.py                   # CycleIndex, group_fires_by_cycle, cycle_id
│   │   ├── selection.py                # select_day_matched, select_cycle_matched, select_live_rule
│   │   ├── entry.py                    # resolve_entry_date, snap_forward, enforce_live_rule
│   │   ├── exits.py                    # resolve_exit_config, PT50, PT25, PT75, HTE, DTE2
│   │   ├── execution.py                # simulate_trade, simulate_cycle (pure function)
│   │   ├── capital.py                  # size_lots, equity_curve (ports from robustness.py)
│   │   └── metrics.py                  # summary_stats, per_trade_stats, per_cycle_stats
│   ├── studies/
│   │   ├── __init__.py
│   │   ├── variant_comparison.py
│   │   ├── time_split.py
│   │   ├── capital_analysis.py
│   │   ├── robustness.py               # replaces current src/nfo/robustness.py
│   │   ├── falsification.py
│   │   ├── live_replay.py
│   │   └── monitor_snapshot.py
│   ├── monitor/
│   │   ├── __init__.py
│   │   ├── snapshot.py                 # MonitorSnapshot producer (Dhan + engine.triggers consumer)
│   │   ├── transitions.py              # pure state machine: next_state(current, evidence)
│   │   ├── parity.py                   # compare_monitor_vs_research
│   │   └── store.py                    # JSONL storage + query
│   ├── reporting/
│   │   ├── __init__.py
│   │   ├── artifacts.py                # RunDirectory writer, manifest.json/metrics.json
│   │   ├── markdown.py                 # Report(methodology_header + body)
│   │   ├── tables.py                   # CSV/parquet tables under runs/<run_id>/tables/
│   │   ├── index.py                    # results/nfo/index.md + latest.json generator
│   │   └── methodology_header.py       # build_header(manifest) → str
│   │
│   ├── # Retained (refactored to import engine/, not scripts/):
│   ├── bsm.py
│   ├── calendar_nfo.py
│   ├── cache.py
│   ├── client.py                       # DhanClient
│   ├── data.py
│   ├── costs.py
│   ├── events.py                       # will share trigger logic with engine/
│   ├── calibrate.py                    # may move into engine/metrics.py in P2
│   ├── enrich.py
│   ├── instruments.py
│   ├── parallel_client.py
│   ├── signals.py                      # math primitives; engine/triggers.py orchestrates
│   ├── spread.py
│   ├── strategy.py                     # short-leg picker; orchestrated by engine/execution.py
│   └── universe.py
├── scripts/nfo/                        # thin CLI wrappers (business logic empty by P2 end)
│   ├── historical_backtest.py
│   ├── redesign_variants.py            # thin wrapper over studies.variant_comparison
│   ├── time_split_validate.py          # thin wrapper over studies.time_split
│   ├── v3_capital_analysis.py          # thin wrapper over studies.capital_analysis
│   ├── v3_robustness.py                # thin wrapper over studies.robustness
│   ├── v3_falsification.py             # thin wrapper over studies.falsification
│   ├── v3_live_rule_backtest.py        # thin wrapper over studies.live_replay
│   ├── entry_perturbation_backtest.py
│   ├── exit_sweep_backtest.py
│   ├── regime_watch.py                 # thin wrapper over monitor.snapshot
│   ├── refresh_events.py
│   ├── refresh_vix_cache.py
│   ├── tune_thresholds.py
│   ├── recost_trades.py
│   ├── v3_fill_gaps.py
│   ├── backtest_one.py
│   ├── backtest_grid.py
│   └── probe.py
├── configs/nfo/
│   ├── strategies/
│   │   ├── v3_frozen.yaml              # from docs/v3-spec-frozen.md, selection_mode=cycle_matched
│   │   └── v3_live_rule.yaml           # same strategy, selection_mode=live_rule (P3)
│   └── studies/
│       ├── variant_comparison_default.yaml
│       ├── capital_analysis_10L.yaml
│       ├── robustness_default.yaml
│       ├── falsification_default.yaml
│       ├── time_split_default.yaml
│       └── live_replay_default.yaml
├── tests/nfo/
│   ├── conftest.py
│   ├── specs/                          # schema unit tests
│   ├── datasets/
│   ├── engine/
│   ├── studies/
│   ├── reporting/
│   ├── monitor/
│   ├── parity/                         # P2 only; deleted when legacy paths deleted
│   ├── cross_report/                   # cross-study consistency tests
│   ├── golden/                         # frozen fixtures (parquets + JSON snapshots)
│   └── (legacy test files retained until P2 wrapper migration ships)
├── data/nfo/
│   ├── datasets/                       # new in P2
│   │   ├── raw/<dataset_id>/…
│   │   ├── normalized/<dataset_id>/…
│   │   ├── features/<dataset_id>/…
│   │   ├── trade_universe/<dataset_id>/…
│   │   └── study_inputs/<dataset_id>/…
│   ├── monitor_snapshots/<YYYY-MM-DD>.jsonl   # new in P4
│   └── (existing raw caches remain: index/, rolling/, parallel_cache/, events.parquet, etc.)
├── results/nfo/
│   ├── runs/<run_id>/                  # canonical run outputs
│   ├── index.md                        # generated
│   ├── latest.json                     # generated
│   └── legacy/                         # pre-platform artifacts, moved in P4
├── legacy/
│   └── csp/                            # archived CSP backtester (from src/csp/ + scripts/*.py)
├── docs/
│   ├── plans/
│   │   └── 2026-04-21-nfo-research-platform-ultraplan.md
│   ├── superpowers/
│   │   ├── specs/
│   │   │   └── 2026-04-21-nfo-research-platform-design.md   # this file
│   │   └── plans/                      # phase-level implementation plans land here
│   ├── options-reading-material.md
│   ├── india-fno-nuances.md
│   ├── REVIEW.md
│   └── v3-spec-frozen.md
└── pyproject.toml                      # name = "nfo-platform"
```

Rules:
- `datasets/` loads and normalizes data but does not define strategy semantics.
- `engine/` owns all trade/firing/cycle/entry/exit behavior.
- `studies/` composes engine + datasets into analysis jobs.
- `reporting/` renders structured outputs.
- `scripts/nfo/` become thin CLI wrappers only (by end of P2).
- Nothing in `src/nfo/` imports from `scripts/`. If a script needs helper logic, the logic moves to `src/`.

---

## 4. Schema contracts

All schemas are Pydantic v2 models under `src/nfo/specs/`. Field-level types use `Literal`, `conint`, `confloat`, `Annotated`, and explicit `None` defaults. Models forbid extra fields (`model_config = ConfigDict(extra="forbid")`). Dates are `datetime.date`; datetimes are timezone-aware UTC (IST conversion at edges).

Where the ultraplan (§5.1, §5.2, §5.3) lists nested fields in prose, this section gives them concrete types. Anything in this subsection supersedes the ultraplan on typing details.

Code blocks in §4 are implementation sketches: type signatures and validators are authoritative, but `...` bodies are filled in during P1 implementation.

### 4.1 StrategySpec

```python
class UniverseSpec(BaseModel):
    underlyings: list[Literal["NIFTY", "BANKNIFTY", "FINNIFTY"]]
    delta_target: confloat(gt=0, lt=1)
    delta_tolerance: confloat(ge=0, lt=0.5)
    width_rule: Literal["fixed", "formula", "risk_budget"]
    width_value: float | None                    # required when width_rule == "fixed"
    dte_target: conint(ge=1, le=60)
    dte_tolerance: conint(ge=0, le=14)
    allowed_contract_families: list[Literal["PE", "CE"]] = ["PE"]

class TriggerSpec(BaseModel):
    score_gates: dict[str, conint(ge=0)] = {}
    specific_pass_gates: list[str] = []          # e.g. ["s3_iv_rv", "s6_trend", "s8_events"]
    event_window_days: conint(ge=0, le=30) = 10
    feature_thresholds: dict[str, float] = {}    # e.g. {"vix_abs_min": 20.0, "iv_rank_min": 0.60}
    missing_data_policy: Literal["skip_day", "treat_as_fail", "treat_as_pass"] = "skip_day"

class SelectionSpec(BaseModel):
    mode: Literal["day_matched", "cycle_matched", "live_rule"]
    one_trade_per_cycle: bool = True
    preferred_exit_variant: Literal["pt25","pt50","pt75","hte","dte2"]
    canonical_trade_chooser: Literal["first_fire", "best_delta_match", "earliest_entry"] = "first_fire"
    width_handling: Literal["strict_fixed", "allow_alternate"] = "strict_fixed"
    tie_breaker_order: list[str] = ["delta_err_asc", "width_exact", "entry_date_asc"]

class EntrySpec(BaseModel):
    earliest_entry_relative_to_first_fire: conint(ge=0) = 0   # must be 0 for live_rule
    session_snap_rule: Literal["forward_only", "forward_or_backward", "no_snap"] = "forward_only"
    entry_timestamp_convention: Literal["session_close", "session_open", "mid_session"] = "session_close"
    allow_pre_fire_entry: bool = False           # must be False for live_rule

    @model_validator(mode="after")
    def _live_rule_no_pre_fire(self) -> "EntrySpec":
        ...

class ExitSpec(BaseModel):
    variant: Literal["pt25", "pt50", "pt75", "hte", "dte2"]
    profit_take_fraction: float | None           # derived from variant, validated for consistency
    manage_at_dte: Annotated[int, Field(ge=0, le=60)] | None   # None means HTE; 21 is standard pt50 management
    expiry_settlement: Literal["cash_settled_to_spot", "held_to_expiry_intrinsic"] = "cash_settled_to_spot"

class CapitalSpec(BaseModel):
    fixed_capital_inr: confloat(gt=0)
    deployment_fraction: confloat(gt=0, le=1.0) = 1.0
    compounding: bool = False
    lot_rounding_mode: Literal["floor", "round"] = "floor"

class SlippageSpec(BaseModel):
    model: Literal["flat_rupees_per_lot", "percent_of_premium"] = "flat_rupees_per_lot"
    flat_rupees_per_lot: float = 0.0
    percent_of_premium: float = 0.0

class StrategySpec(BaseModel):
    strategy_id: str                              # e.g. "v3"
    strategy_version: str                         # SemVer, e.g. "3.0.0"
    description: str
    universe: UniverseSpec
    feature_set: list[str]                        # names of features this strategy depends on
    trigger_rule: TriggerSpec
    selection_rule: SelectionSpec
    entry_rule: EntrySpec
    exit_rule: ExitSpec
    capital_rule: CapitalSpec
    slippage_rule: SlippageSpec
    report_defaults: dict[str, Any] = {}

    model_config = ConfigDict(extra="forbid")
```

Validators enforce:
- `selection_rule.mode == "live_rule"` → `entry_rule.allow_pre_fire_entry is False` and `entry_rule.earliest_entry_relative_to_first_fire == 0`.
- `universe.width_rule == "fixed"` → `universe.width_value is not None`.
- `exit_rule.variant == "hte"` → `exit_rule.manage_at_dte is None` and `profit_take_fraction == 1.0`.
- `strategy_version` matches `^\d+\.\d+\.\d+$`.

### 4.2 Spec hashing

```python
def canonical_json(model: BaseModel) -> bytes:
    """Pydantic dump, mode='json', by_alias, exclude_none, sorted keys, no whitespace."""
    ...

def spec_hash(model: BaseModel) -> str:
    return hashlib.sha256(canonical_json(model)).hexdigest()
```

Drift detection at load time:

```python
def load_strategy(path: Path) -> tuple[StrategySpec, str]:
    spec = StrategySpec.model_validate(yaml.safe_load(path.read_text()))
    current_hash = spec_hash(spec)
    registry_entry = _strategy_registry.get((spec.strategy_id, spec.strategy_version))
    if registry_entry and registry_entry.hash != current_hash:
        raise StrategyDriftError(
            f"strategy_id={spec.strategy_id!r} version={spec.strategy_version!r} "
            f"content hash changed ({registry_entry.hash[:12]} → {current_hash[:12]}). "
            f"Bump strategy_version before editing spec content."
        )
    _strategy_registry[(spec.strategy_id, spec.strategy_version)] = RegistryEntry(
        path=path, hash=current_hash, loaded_at=now_iso(),
    )
    return spec, current_hash
```

Registry is a JSON file at `configs/nfo/.registry.json`, committed to git.

### 4.3 StudySpec

```python
class DatasetRef(BaseModel):
    dataset_id: str                               # e.g. "historical_features_2024-01_2026-04"
    dataset_type: Literal["raw","normalized","features","trade_universe","study_inputs"]
    path: Path                                    # under data/nfo/datasets/<type>/<dataset_id>/

StudyType = Literal[
    "variant_comparison",
    "time_split",
    "capital_analysis",
    "robustness",
    "falsification",
    "live_replay",
    "monitor_snapshot",
]

class StudySpec(BaseModel):
    study_id: str
    study_type: StudyType
    strategy_spec_ref: Path                       # path to strategies/*.yaml
    dataset_refs: list[DatasetRef]
    parameters: dict[str, Any] = {}               # must be JSON-serializable
    output_profile: Literal["default", "compact", "full"] = "default"

    model_config = ConfigDict(extra="forbid")

    @field_validator("parameters")
    @classmethod
    def _parameters_json_serializable(cls, v: dict) -> dict:
        json.dumps(v)   # raises TypeError on non-serializable values
        return v
```

### 4.4 RunManifest

```python
class RunManifest(BaseModel):
    run_id: str                                   # see §5 for format
    created_at: datetime                          # UTC ISO
    code_version: str                             # "<shortsha>" or "<shortsha>-dirty"
    study_spec_hash: str
    strategy_spec_hash: str
    strategy_id: str
    strategy_version: str
    study_type: StudyType
    selection_mode: Literal["day_matched","cycle_matched","live_rule"]
    # selection_mode is copied from strategy_spec.selection_rule.mode at run time
    # and validated against it; storing it here makes manifests self-describing
    # without a join against the spec file.
    dataset_hashes: dict[str, str]                # dataset_id -> sha256
    window_start: date
    window_end: date
    artifacts: list[str]                          # relative paths under the run dir
    status: Literal["ok", "failed", "warnings"]
    warnings: list[str] = []
    stale_inputs_detected: list[str] = []
    duration_seconds: float
```

Written as `runs/<run_id>/manifest.json`. Any code outside `reporting/artifacts.py` constructing a RunManifest is a bug.

### 4.5 DatasetManifest

```python
class DatasetManifest(BaseModel):
    dataset_id: str
    dataset_type: Literal["raw","normalized","features","trade_universe","study_inputs"]
    source_paths: list[Path]
    date_window: tuple[date, date] | None
    row_count: int
    build_time: datetime
    code_version: str
    upstream_datasets: list[str] = []             # dataset_ids this one consumed
    parquet_sha256: str
    schema_fingerprint: str                       # sha256 of sorted column names + dtypes
```

Dataset parquet hash: `sha256` of the parquet file bytes after rewriting via pyarrow with sorted row order and stripped creation metadata (see `src/nfo/datasets/manifests.py::canonical_parquet_bytes`).

---

## 5. Canonical identifiers

All identifiers are stable, deterministic, and content-derived where possible.

```python
feature_day_id = f"{underlying}:{date.isoformat()}"
# e.g. "NIFTY:2025-03-24"

cycle_id       = f"{underlying}:{target_expiry.isoformat()}:{strategy_version}"
# e.g. "NIFTY:2025-04-24:3.0.0"

fire_id        = f"{cycle_id}:{fire_date.isoformat()}"
# e.g. "NIFTY:2025-04-24:3.0.0:2025-03-24"

trade_id       = sha1(canonical_json({
    "underlying": ..., "expiry_date": ..., "short_strike": ...,
    "long_strike": ..., "width": ..., "delta_target": ...,
    "exit_variant": ..., "entry_date": ...,
})).hexdigest()[:16]
# e.g. "7a3f9b2e1c9d8a6f"

selection_id   = f"{cycle_id}:{selection_mode}:{exit_variant}"
# e.g. "NIFTY:2025-04-24:3.0.0:live_rule:hte"

run_id         = f"{created_at:%Y%m%dT%H%M%S}-{study_id}-{strategy_hash_short}"
# e.g. "20260421T143000-capital_analysis-7a3f9b"
# strategy_hash_short = strategy_spec_hash[:6]
```

The identifier helpers live in `src/nfo/engine/cycles.py` and `src/nfo/specs/hashing.py`. Reports, artifact filenames, and manifest fields use these identifiers verbatim.

---

## 6. Evaluation semantics

Three first-class selection modes, owned by `src/nfo/engine/selection.py`.

### 6.1 `day_matched`

- **Question answered:** "Are trades entered on signal days generally good?"
- **Trade set:** every candidate trade in `trade_universe` whose `entry_date` is a firing date and whose metadata satisfies `UniverseSpec`.
- **Implementation:** `select_day_matched(trade_universe, firing_dates, universe_spec) -> DataFrame`.

### 6.2 `cycle_matched`

- **Question answered:** "If I force one canonical trade per cycle, how does that trade family behave?"
- **Trade set:** one row per cycle, chosen by `SelectionSpec.tie_breaker_order`. Entry may be the canonical 35-DTE grid date only if `EntrySpec.allow_pre_fire_entry == True`; otherwise enforced to `first_fire_date` or later.
- **Implementation:** `select_cycle_matched(trade_universe, cycle_index, strategy_spec) -> DataFrame`.

### 6.3 `live_rule`

- **Question answered:** "What would a literal live system have done using only information available on that date?"
- **Trade set:** one row per cycle, with strict `entry_date >= first_fire_date` enforcement. Session-snap is forward-only. No pre-fire entries. Dependent on live-valid trade simulation (re-walks the tape from the fire date).
- **Implementation:** `select_live_rule(fire_index, simulator, strategy_spec) -> DataFrame`.
- **Invariant (single source of truth):** `engine.entry.resolve_entry_date(first_fire_date, sessions, spec)` is the only function allowed to produce an entry date when `selection_mode == "live_rule"`. Any other function producing an entry date in live-rule mode is a bug.

### 6.4 Width handling

`UniverseSpec.width_rule`:
- `"fixed"`: platform rejects candidate trades whose `width != width_value`.
- `"formula"`: platform computes expected width per cycle from a formula on entry features. Deferred — formula shape decided when the first strategy needing it is added.
- `"risk_budget"`: width sized so max loss per lot stays within a budget. Deferred — rule shape decided when needed.

P1 only supports `"fixed"`; `"formula"` and `"risk_budget"` are Pydantic-validated as valid values but the engine raises `NotImplementedError` if selected.

### 6.5 Underlying handling

Reports never silently broaden the universe. `UniverseSpec.underlyings` is explicit. `engine.selection` filters on underlying membership before any other gate.

---

## 7. Dataset pipeline

### 7.1 Stages

Five stages, each a pure function over manifest-declared inputs:

1. `raw` — cached underlying bars, VIX bars, option rolling parquets, events cache.
2. `normalized` — standardized schema (same columns, dtypes, sort order, timezone).
3. `features` — daily feature table per underlying.
4. `trade_universe` — candidate trades with metadata + realized outcomes (no strategy filtering).
5. `study_inputs` — joined inputs for a specific study.

Each stage's build function is:

```python
def build_<stage>(inputs: list[DatasetRef], params: dict) -> DatasetManifest:
    ...
```

Dataset manifests are written as `data/nfo/datasets/<stage>/<dataset_id>/manifest.json` alongside the parquet.

### 7.2 Staleness detection

Implemented in `src/nfo/datasets/staleness.py`:

```python
def is_run_stale(manifest: RunManifest) -> list[str]:
    """Return a list of stale reasons; empty means fresh."""
    reasons = []
    # 1. Strategy spec hash drift
    if current_strategy_hash(manifest.strategy_id, manifest.strategy_version) != manifest.strategy_spec_hash:
        reasons.append(f"strategy_spec_hash_changed:{manifest.strategy_id}@{manifest.strategy_version}")
    # 2. Dataset manifest hash drift
    for dataset_id, expected in manifest.dataset_hashes.items():
        current = current_dataset_hash(dataset_id)
        if current is None:
            reasons.append(f"dataset_missing:{dataset_id}")
        elif current != expected:
            reasons.append(f"dataset_hash_changed:{dataset_id}")
    return reasons
```

The index generator (`reporting/index.py`) calls `is_run_stale` for every run and marks stale entries.

### 7.3 No overwriting

Datasets and runs are never mutated in place. Building a new dataset_id when inputs change is mandatory; the old dataset remains on disk until explicitly deleted.

---

## 8. Reporting platform

### 8.1 Run directory

```text
results/nfo/runs/<run_id>/
├── manifest.json               # RunManifest
├── metrics.json                # flat dict of numeric headline metrics
├── tables/
│   ├── selected_trades.csv
│   ├── per_cycle_summary.csv
│   └── … (study-specific)
├── report.md                   # human-readable report (methodology header + body)
└── logs/
    └── run.log
```

Optional:
- `plots/*.png`
- `parquet_snapshots/*.parquet`
- `debug/*.json`

### 8.2 Top-level pointers

Only two files under `results/nfo/` change per run:
- `results/nfo/index.md` — table of recent runs by study family, with stale markers.
- `results/nfo/latest.json` — `{study_family: {run_id, path, created_at}}`.

All historical mutable reports (`tier1_report.md`, `backtest_rerun_plain.md`, unsuffixed capital reports, `v3_master_analysis.md`, etc.) are moved to `results/nfo/legacy/` in P4 with pointer stubs that redirect to canonical runs.

### 8.3 Report families

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

### 8.4 Methodology header

Every `report.md` begins with an auto-generated block:

```markdown
<!-- methodology:begin -->
## Methodology
- **Run ID:** `20260421T143000-capital_analysis-7a3f9b`
- **Study type:** capital_analysis
- **Strategy:** `v3` version `3.0.0` (hash `7a3f9b2e1c9d8a6f`)
- **Selection mode:** live_rule
- **Entry rule:** entry_date >= first_fire_date, forward-snap
- **Exit rule:** pt_variant=hte, manage_at_dte=None
- **Universe:** underlyings=[NIFTY], delta_target=0.30±0.05, width=100 (fixed), DTE=35±3
- **Date window:** 2024-02-01 → 2026-04-18
- **Datasets:**
  - `historical_features_2024-01_2026-04` (sha256 `4c2d…a1f3`)
  - `trade_universe_nifty_2024-01_2026-04` (sha256 `9e71…b28c`)
- **Code version:** `a1b2c3d` (clean)
- **Created:** 2026-04-21T14:30:00Z
<!-- methodology:end -->
```

`reporting/methodology_header.py::build_header(manifest)` is the only function allowed to produce this block. Reports fail to write if the header is missing or contains placeholders.

### 8.5 No manual curation

Narrative markdown must be generated from canonical metrics and manifests. Hand-edits under `results/nfo/runs/` are a lint failure (detected via commit hook once git is bootstrapped).

---

## 9. Regime monitor integration

### 9.1 MonitorSnapshot

```python
class MonitorSnapshot(BaseModel):
    # snapshot_id = sha1(canonical_json({
    #     "strategy_id": ..., "strategy_version": ...,
    #     "underlying": ..., "timestamp": iso_string,
    # })).hexdigest()[:16]
    snapshot_id: str
    timestamp: datetime                           # UTC
    strategy_spec_id: str                         # strategy_id
    strategy_version: str
    strategy_spec_hash: str
    underlying: Literal["NIFTY","BANKNIFTY","FINNIFTY"]
    cycle_id: str
    target_expiry: date
    current_state: Literal["idle","watch","fire","entered","invalidated","expired"]
    first_fire_date: date | None
    current_grade: str                            # "A+"/"A"/"B"/...
    trigger_passed: bool
    trigger_details: dict[str, Any]               # per-gate pass/fail + raw values
    selection_preview: dict[str, Any] | None      # what the platform would select right now
    proposed_trade: dict[str, Any] | None         # legs, strikes, deltas, buying_power
    reason_codes: list[str]
```

Stored one-JSONL-per-day at `data/nfo/monitor_snapshots/<YYYY-MM-DD>.jsonl`. Appended, never rewritten.

### 9.2 State machine

Implemented in `src/nfo/monitor/transitions.py` as a pure function:

```python
def next_state(
    current: State,
    evidence: Evidence,
    spec: StrategySpec,
) -> tuple[State, list[str]]:
    ...
```

Transitions (ultraplan §10.3):
- `idle → watch`: cycle begins (first session in `target_expiry − dte_target` vicinity).
- `watch → fire`: trigger passes on a session.
- `fire → entered`: live execution places the trade (out of scope for this platform; UI-driven, simulated as "entered=True" in replay).
- `fire → invalidated`: trigger no longer valid + spec-defined invalidation rule.
- `entered → expired`: trade reaches expiry / exits.

All state changes emit a `MonitorSnapshot` with updated `current_state` + `reason_codes`.

### 9.3 Parity

`src/nfo/monitor/parity.py::compare_monitor_vs_research(spec, window)`:
- Replays historical `features` dataset through `engine.triggers` to get research fire dates.
- Loads monitor snapshots for the same window.
- Asserts: identical `fire` state transitions on identical dates, same `trigger_details` (tolerance on floats: 1e-9).
- Returns a `ParityReport` with mismatches enumerated.

Live and research differ only in data source. Any disagreement is a bug.

---

## 10. Migration strategy

### 10.1 Per-phase parity gates

**P1:** No parity gate. P1 is additive (new modules alongside old, plus git/pyproject rename). Acceptance: every existing script's output path is mirrored to a run-scoped directory via a P1 wrapper, manifests validate, index populates.

**P2:** Parity where the ultraplan says semantics are unchanged. Concretely:
- Non-numeric columns (trade_id, cycle_id, expiry_date, outcome string, selection_id, flags) must match legacy row-for-row, byte-exact.
- Numeric columns match within a declared tolerance per study:
  - `redesign_variants` (day-matched) floats: tolerance 1e-9 (expected exact; 1e-9 handles IEEE ordering).
  - `v3_capital_analysis` (cycle-matched) P&L and equity: 1e-6 relative.
  - `v3_robustness` bootstrap percentiles: 1e-6 relative with same seed.
  - `v3_falsification` metrics: 1e-6 relative.
- Legacy script removal gated on green parity tests.

**P3:** No parity with legacy for look-ahead-prone cycle reports. Instead, parity *between* `cycle_matched` and `live_rule` runs of the same strategy is reported as a comparison table, surfacing the look-ahead delta.

**P4:** Monitor/research parity as defined in §9.3.

### 10.2 Legacy removal order

1. P1: Archive `src/csp/` → `legacy/csp/`. Archive `scripts/run_grid.py`, `scripts/build_plan.py`, etc. (the non-nfo scripts) → `legacy/scripts_csp/`.
2. P2: Replace script bodies with thin CLI wrappers once the engine path passes parity. Old `src/nfo/robustness.py` merges into `src/nfo/studies/robustness.py` and `src/nfo/engine/capital.py`.
3. P3: Delete `scripts/nfo/v3_live_rule_backtest.py` old body; it becomes a wrapper over `studies.live_replay`.
4. P4: Move `results/nfo/*.md`, `results/nfo/*.csv`, `results/nfo/*.parquet` (all legacy artifacts) to `results/nfo/legacy/`. Regenerate top-level index.

### 10.3 Rollback strategy

Once git is bootstrapped (P1 step 1), every phase is on a feature branch merged back to `main` after tests pass. If a phase goes wrong, revert the merge commit. Strangler-fig ensures legacy scripts still run even while new modules are being built.

Before git bootstrap (the ~first 1–2 commits of P1), rollback is manual: the first action of P1 is committing the current tree so there is a safe checkpoint to revert to.

---

## 11. Testing philosophy

### 11.1 Layered tests

1. **Unit** (`tests/nfo/<module>/test_*.py`) — per-module, no I/O beyond fixtures.
2. **Golden** (`tests/nfo/golden/`) — frozen fixtures for fire dates, selected trades, monitor snapshots. Regenerated only via `make regenerate-golden`.
3. **Parity** (`tests/nfo/parity/`) — P2 only; compares new engine output vs legacy script output.
4. **Cross-report** (`tests/nfo/cross_report/`) — for one run_id, asserts methodology header matches manifest, selected trades are consistent across studies.
5. **Smoke** (`tests/nfo/smoke/`) — run each study from cached data, assert run directory shape and required artifacts exist.

### 11.2 Required coverage before phase close

"Coverage" here means functional coverage — the listed behaviors are tested — not strict line coverage.

- P1: every Pydantic model has schema tests (happy path + at least one validation failure per validator); `RunManifest` JSON roundtrip tests; `StrategyDriftError` raised on hash mismatch.
- P2: every engine function has at least one golden test; every parity test green.
- P3: `live_rule` mode has golden + parity (vs `v3_live_rule_backtest.py` pre-removal).
- P4: monitor parity green over a known-good window (2024-02 → 2026-04).

### 11.3 No mocks for broker/data clients

Tests use cached parquets under `tests/nfo/golden/data/` or `data/nfo/`, not mocked `DhanClient` calls. Dhan/Parallel calls are never made from tests.

### 11.4 CLI behavior

Every script under `scripts/nfo/` must:
- Exit 0 on success, 1 on spec-validation failure, 2 on data error, 3 on assertion failure.
- Emit `runs/<run_id>` on success, print the path as the last stdout line.
- Fail loudly if the spec loader raises `StrategyDriftError`.

---

## 12. Master acceptance criteria

The platform is "done" when all of the following hold:

1. **Spec-driven.** Every study runs from a validated `StrategySpec` + `StudySpec`; no script defines strategy semantics inline.
2. **Manifest-backed.** Every run writes `runs/<run_id>/manifest.json` and `metrics.json`; every `report.md` has the methodology header.
3. **Single engine.** `engine/entry.py` is the only code path that resolves entry dates in `live_rule` mode. `engine/triggers.py` is the only code path that decides whether a day fires.
4. **Live ↔ research parity.** `regime_watch.py` and the platform's historical replay produce identical fire/no-fire decisions for the same strategy spec + feature dataset, within 1e-9 tolerance.
5. **Drift detection.** Same `strategy_id` + `strategy_version` with different content hash raises `StrategyDriftError` at load time.
6. **Staleness detection.** `reporting/index.py` marks runs stale when strategy spec hash or dataset hashes drift.
7. **Clean top-level.** `results/nfo/` contains only `runs/`, `index.md`, `latest.json`, and `legacy/`. No other canonical mutable outputs.
8. **Thin scripts.** Every script in `scripts/nfo/` is under 200 lines and delegates to `src/nfo/studies/` or `src/nfo/monitor/`.
9. **Test coverage.** Unit tests for every Pydantic model; golden tests for every engine function; cross-report consistency tests pass.
10. **Archived legacy.** `src/csp/` and non-NFO scripts live under `legacy/`. Old narrative reports (`tier1_report.md`, `backtest_rerun_plain.md`, unsuffixed `v3_capital_*`) live under `results/nfo/legacy/` with pointer stubs.
11. **Reproducible.** A fresh clone + `git checkout <sha>` + cached data reruns any study and produces byte-identical manifests (except for `created_at`).

---

## 13. Open / deferred decisions

Documented here so phase plans know what they may adjust without revisiting this doc.

- **Exact test harness layout per phase** — phase plans may add subdirectories under `tests/nfo/`.
- **CLI entrypoint naming** — both `python -m nfo.studies.<name>` and `scripts/nfo/<name>.py` wrappers are allowed; scripts may eventually become a `console_scripts` entry point.
- **`data/nfo/` subdirectory reorganization** — partial in P2 (new `datasets/` subdir); full cleanup in P4.
- **Monitor live-vs-research tolerance** — exact tolerance for IV source differences (live uses Dhan ATM chain, research uses parquet-stored short-strike IV) is resolved in P4 parity work.
- **Event-calendar data source** — currently hardcoded `HARD_EVENTS` in `historical_backtest.py`. Replacement via Parallel or manual YAML under `configs/nfo/events/` is a P2 dataset task.
- **Width `formula` and `risk_budget` rules** — stubbed in P1, implemented in P2 or later as strategies require.
- **Monitor → MonitorSnapshot migration of existing `regime_history.parquet`** — out of scope for P4; platform starts snapshot capture fresh.
- **Strategy catalog on day 1** — `v3_frozen.yaml` (cycle_matched) ships in P1 step 2 as the first validated spec; `v3_live_rule.yaml` ships in P3. Future strategies (`v4`, variants) added independently.

---

## 14. Appendix: ultraplan → this doc mapping

| Ultraplan § | This doc § | Notes |
|---|---|---|
| §1 Executive summary | §1 | Same intent, condensed |
| §2 What is wrong today | §1 | Summarized |
| §3 Desired end-state | §1, §6, §12 | Split into goals + semantics + acceptance |
| §4 Platform principles | §10, §12 | Embedded in migration and acceptance |
| §5 Canonical contracts and schemas | §4, §5 | Concrete Pydantic types added |
| §6 Target package structure | §3 | Extended with `tests/` layout and `legacy/` dir |
| §7 Dataset pipeline | §7 | Unchanged |
| §8 Canonical execution semantics | §6 | Three modes formalized; entry single-source-of-truth |
| §9 Reporting platform | §8 | Methodology header format is concrete |
| §10 Regime monitor integration | §9 | MonitorSnapshot model concrete; parity spec concrete |
| §11 Script migration plan | §3, §10 | Scripts keep names; become thin |
| §12 Testing strategy | §11 | Five-layer test taxonomy defined |
| §13 Migration phases | §2 | Master doc + 4 plans |
| §14 File and artifact cleanup policy | §10 | Legacy archival schedule in §10.2 |
| §15 Operational workflow | §13 | CLI shape as deferred decision |
| §16 Acceptance criteria | §12 | 11 explicit items |
| §17 Defaults and assumptions | throughout | With our 6 clarifying answers folded in |
| §18 Immediate implementation order | §2.2 | Strictly preserved |

---

## 15. Clarifying decisions applied in this doc

Decisions made during brainstorming and folded into the design above:

1. **Decomposition:** Master doc (this) + 4 phase-level plans (§2).
2. **code_version:** `git rev-parse --short HEAD` + `-dirty` suffix; git bootstrapped in P1 step 1 (§2.3, §4.4).
3. **Spec file format:** YAML for `configs/nfo/strategies/*.yaml` and `configs/nfo/studies/*.yaml`; JSON for `manifest.json`, `metrics.json`, `.registry.json` (§3, §4).
4. **Package identity:** `pyproject.toml` renamed to `nfo-platform`; `src/csp/` + non-nfo scripts moved to `legacy/` (§3, §10.2).
5. **Strategy versioning:** SemVer + SHA-256 content hash; `StrategyDriftError` on mismatch (§4.2).
6. **Engine scope:** Credit spreads only at day 1, underlying-agnostic via `UniverseSpec` (§4.1, §6.5, §13).

---

*End of master design. Phase plans live at `docs/superpowers/plans/2026-04-21-nfo-platform-phase<N>-plan.md`.*
