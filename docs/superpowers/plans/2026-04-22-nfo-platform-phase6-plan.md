# NFO Platform Phase 6 — Dataset Pipeline (Minimum Scope)

> **For agentic workers:** `superpowers:subagent-driven-development` with fresh subagent per bundle. TDD enforced.

**Goal:** Activate end-to-end drift detection by formalizing the two datasets every V3 study consumes — the features parquet and the trade universe — as manifested datasets under `data/nfo/datasets/`. Studies start declaring `DatasetRef`s; `RunManifest.dataset_hashes` gets populated with real hashes; `is_run_stale` now flags real drift instead of always saying `dataset_missing`.

**Architecture:** Datasets are ingested (not rebuilt) from existing canonical files. Each dataset gets its own directory under `data/nfo/datasets/<stage>/<dataset_id>/` with `parquet/csv` + `manifest.json` containing `parquet_sha256` + `schema_fingerprint` + provenance fields. Studies pull the dataset hash via `filesystem_hash_sources` (P1) — no code changes to staleness logic itself.

**Master design reference:** §4.5 (DatasetManifest schema), §7 (pipeline stages), §7.2 (staleness), §12 item 7 (stale detection end-to-end).

**Tech stack:** Python 3.14 via `.venv/bin/python`. Pydantic v2. pandas + pyarrow. pytest.

## Scope (strict)

**In scope — 5 bundles:**
- Bundle A: `datasets/features.py` — ingest `historical_signals.parquet` → manifested dataset
- Bundle B: `datasets/trade_universe.py` — ingest `spread_trades.csv` + `spread_trades_v3_gaps.csv` → manifested dataset
- Bundle C: `wrap_legacy_run` accepts `dataset_refs` → populates `RunManifest.dataset_hashes`; wire all 6 wrapper scripts to pass their refs
- Bundle D: End-to-end staleness test (seed drift → index marks runs stale)
- Bundle E: Acceptance + `p6-complete`

**Deferred (P7+ if ever needed):**
- `datasets/raw.py` — raw stage codification (underlying bars, VIX, option rolling parquets)
- `datasets/normalized.py` — normalized schema layer
- `datasets/study_inputs.py` — joined frames per study
- Legacy reshape helper extraction into `scripts/nfo/_legacy_helpers/`
- Actual file-moves of pre-platform narrative reports into `results/nfo/legacy/archive/`
- Utility script migrations (`v3_fill_gaps.py`, `recost_trades.py`)

## Execution conventions

- TDD: failing test → observe fail → implement → observe pass → commit.
- Pydantic v2 models already define `DatasetManifest` (P1 `src/nfo/specs/manifest.py`) — reuse, don't redefine.
- `from __future__ import annotations` on every new module.
- Commit style: Conventional Commits.

---

## Bundle A — datasets/features.py

### Task P6-A1 — ingest_features_parquet

**Files:**
- Create: `src/nfo/datasets/features.py`
- Create: `src/nfo/datasets/_hashing.py` (shared helpers: `sha256_file`, `schema_fingerprint`)
- Create: `tests/nfo/datasets/test_features_ingest.py`

**Contract:**
```python
def ingest_features_parquet(
    *,
    parquet_path: Path,
    dataset_id: str,
    datasets_root: Path,
    upstream_datasets: list[str] | None = None,
    code_version: str | None = None,
) -> DatasetManifest:
    """Copy/link an existing features parquet into data/nfo/datasets/features/<dataset_id>/
    and write its manifest.json. Returns the manifest.

    - Reads `parquet_path`.
    - Computes `parquet_sha256` = hashlib.sha256 of file bytes.
    - Computes `schema_fingerprint` = sha256 of sorted [(col, str(dtype))] pairs.
    - Derives `row_count` and `date_window` from `date` column.
    - Writes `data/nfo/datasets/features/<dataset_id>/dataset.parquet` (copy).
    - Writes `data/nfo/datasets/features/<dataset_id>/manifest.json`.
    """


def features_dataset_dir(datasets_root: Path, dataset_id: str) -> Path:
    """Canonical directory for a features dataset."""
```

Supporting helper `_hashing.py`:
```python
def sha256_file(path: Path) -> str:
    """SHA-256 hex of a file's raw bytes."""


def schema_fingerprint(df: pd.DataFrame) -> str:
    """Stable hash of (column_name, dtype_str) pairs, sorted."""
```

**Steps:**
1. Write failing tests:
   - `test_ingest_creates_manifest_and_parquet` — ingest a tmp parquet, assert both files exist.
   - `test_manifest_fields_populated` — `row_count`, `date_window`, `parquet_sha256` (64 hex), `schema_fingerprint` (64 hex), `source_paths` = [original path].
   - `test_parquet_sha256_stable` — ingesting same parquet twice produces identical hash.
   - `test_parquet_sha256_changes_on_content` — modify a value → new hash.
   - `test_schema_fingerprint_stable_under_value_change` — same schema, different values → same fingerprint.
   - `test_schema_fingerprint_changes_on_schema_change` — add a column → new fingerprint.
   - `test_date_window_derived_from_date_column` — smallest and largest dates in df.
2. Implement `_hashing.py` then `features.py`.
3. Commit: `feat(datasets): add features ingestion with manifest`.

---

## Bundle B — datasets/trade_universe.py

### Task P6-B1 — ingest_trade_universe_csv

**Files:**
- Create: `src/nfo/datasets/trade_universe.py`
- Create: `tests/nfo/datasets/test_trade_universe_ingest.py`

**Contract:**
```python
def ingest_trade_universe_csv(
    *,
    csv_paths: list[Path],              # [spread_trades.csv, spread_trades_v3_gaps.csv]
    dataset_id: str,
    datasets_root: Path,
    upstream_datasets: list[str] | None = None,
    code_version: str | None = None,
) -> DatasetManifest:
    """Concatenate the provided CSVs, write as a single parquet + manifest.

    The source CSVs are under results/nfo/ today. This ingest reads them,
    concatenates (preserving row order), writes parquet to
    data/nfo/datasets/trade_universe/<dataset_id>/dataset.parquet.
    - parquet_sha256 = sha256 of the written parquet file.
    - schema_fingerprint = schema_fingerprint(concatenated_df).
    - row_count = len(concatenated_df).
    - date_window = (min(entry_date), max(entry_date)) parsed as date.
    - source_paths = original CSV paths.
    """
```

**Steps:**
1. Write failing tests following the same pattern as Bundle A.
2. Implement `trade_universe.py`.
3. Commit: `feat(datasets): add trade_universe ingestion from CSVs`.

---

## Bundle C — wire wrap_legacy_run + scripts

### Task P6-C1 — extend `wrap_legacy_run` signature

**Files:**
- Modify: `src/nfo/reporting/wrap_legacy_run.py` — add `dataset_refs: list[DatasetRef] | None = None` parameter
- Modify: `tests/nfo/reporting/test_wrap_legacy_run.py` — add test that dataset_hashes populate when refs passed

**Contract change:**

```python
def wrap_legacy_run(
    *,
    study_type: StudyType,
    strategy_path: Path,
    study_path: Path | None,
    legacy_artifacts: list[Path],
    window: tuple[date, date],
    run_logic: Callable[[], dict[str, Any]],
    runs_root: Path,
    code_version: str | None = None,
    dataset_refs: list[DatasetRef] | None = None,   # NEW in P6
) -> WrappedRun:
    """
    When dataset_refs is provided, the resulting RunManifest.dataset_hashes
    is populated by looking up each ref's manifest.json under the provided
    datasets_root convention:
        <dataset_ref.path>/manifest.json → parquet_sha256
    """
```

Add a helper `_resolve_dataset_hashes(refs) -> dict[str, str]` that reads each ref's manifest.json and extracts `parquet_sha256`. If a ref's manifest is missing, log a warning and skip (don't crash the run).

**Steps:**
1. Write failing tests: `test_wrap_legacy_run_populates_dataset_hashes` and `test_wrap_legacy_run_missing_manifest_skips_ref`.
2. Implement the new parameter + helper.
3. Commit: `feat(reporting): wrap_legacy_run accepts DatasetRefs and populates dataset_hashes`.

### Task P6-C2 — seed the two canonical datasets once

**Files:**
- Create: `scripts/nfo/p6_seed_datasets.py` — one-shot script that calls both ingestors to seed `data/nfo/datasets/features/` and `data/nfo/datasets/trade_universe/`
- Run it manually as part of this bundle so the datasets exist on disk before Bundle C3 wires scripts to use them.

**Minimal script:**
```python
"""P6 one-shot: ingest current features + trade_universe into the dataset pipeline."""
from pathlib import Path
from nfo.config import DATA_DIR, RESULTS_DIR
from nfo.datasets.features import ingest_features_parquet
from nfo.datasets.trade_universe import ingest_trade_universe_csv


def main() -> int:
    ds_root = DATA_DIR / "datasets"

    features_manifest = ingest_features_parquet(
        parquet_path=RESULTS_DIR / "historical_signals.parquet",
        dataset_id="historical_features_2024-01_2026-04",
        datasets_root=ds_root,
    )
    print(f"features: {features_manifest.dataset_id} sha256={features_manifest.parquet_sha256[:12]}")

    trades_paths = [RESULTS_DIR / "spread_trades.csv"]
    gaps = RESULTS_DIR / "spread_trades_v3_gaps.csv"
    if gaps.exists():
        trades_paths.append(gaps)
    trades_manifest = ingest_trade_universe_csv(
        csv_paths=trades_paths,
        dataset_id="trade_universe_nifty_2024-01_2026-04",
        datasets_root=ds_root,
    )
    print(f"trade_universe: {trades_manifest.dataset_id} sha256={trades_manifest.parquet_sha256[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run it: `.venv/bin/python scripts/nfo/p6_seed_datasets.py`

Commit: `chore(datasets): P6 one-shot seed script + first dataset manifests`.

### Task P6-C3 — wire all 6 wrapper scripts

**Files:** 6 script modifications, each small.

For each of:
- `scripts/nfo/v3_capital_analysis.py`
- `scripts/nfo/v3_robustness.py`
- `scripts/nfo/v3_falsification.py`
- `scripts/nfo/v3_live_rule_backtest.py`
- `scripts/nfo/redesign_variants.py`
- `scripts/nfo/time_split_validate.py`

Add in the `main()` (the wrap_legacy_run caller — not `_legacy_main`):

```python
from nfo.specs.study import DatasetRef
from nfo.config import DATA_DIR

dataset_refs = [
    DatasetRef(
        dataset_id="historical_features_2024-01_2026-04",
        dataset_type="features",
        path=DATA_DIR / "datasets" / "features" / "historical_features_2024-01_2026-04",
    ),
    DatasetRef(
        dataset_id="trade_universe_nifty_2024-01_2026-04",
        dataset_type="trade_universe",
        path=DATA_DIR / "datasets" / "trade_universe" / "trade_universe_nifty_2024-01_2026-04",
    ),
]

result = wrap_legacy_run(
    ...,
    dataset_refs=dataset_refs,
)
```

Single commit covers all 6: `refactor(scripts): pass DatasetRefs to wrap_legacy_run for drift detection`.

Optional: add a smoke test that runs a single wrapper and verifies the emitted manifest has non-empty `dataset_hashes`.

---

## Bundle D — End-to-end staleness test

### Task P6-D1 — prove drift is detected

**Files:**
- Create: `tests/nfo/datasets/test_staleness_e2e.py`

**Test:**

```python
"""End-to-end drift detection (master design §7.3, §12 item 7).

Seed a run with real dataset hashes. Then simulate drift by overwriting
the dataset's manifest with a different parquet_sha256. The index
generator should mark the run stale.
"""
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


def test_drift_marks_run_stale(tmp_path):
    datasets_root = tmp_path / "datasets"

    # Seed a tiny features parquet + manifest
    df = pd.DataFrame({"date": pd.to_datetime(["2025-01-01", "2025-01-02"]), "x": [1, 2]})
    parquet_path = tmp_path / "seed.parquet"
    df.to_parquet(parquet_path)
    manifest = ingest_features_parquet(
        parquet_path=parquet_path, dataset_id="ds_test",
        datasets_root=datasets_root,
    )
    original_hash = manifest.parquet_sha256

    # Create a run that references this dataset
    runs_root = tmp_path / "runs"
    rd = open_run_directory(root=runs_root, run_id="r-drift-1")
    run_manifest = RunManifest(
        run_id="r-drift-1",
        created_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
        code_version="abc",
        study_spec_hash="x" * 64,
        strategy_spec_hash="s" * 64,
        strategy_id="v3", strategy_version="3.0.0",
        study_type="capital_analysis",
        selection_mode="cycle_matched",
        dataset_hashes={"ds_test": original_hash},
        window_start=date(2024, 1, 1), window_end=date(2025, 1, 1),
        artifacts=[], status="ok", duration_seconds=1.0,
    )
    rd.write_manifest(run_manifest)

    sources = filesystem_hash_sources(
        strategies_root=tmp_path / "strategies",
        datasets_root=datasets_root,
    )
    # Fresh — should NOT be stale
    res = generate_index(runs_root=runs_root, out_root=tmp_path, sources=sources)
    assert res.stale_runs == 0

    # Simulate drift: overwrite the dataset's manifest with a different hash
    ds_manifest_path = datasets_root / "features" / "ds_test" / "manifest.json"
    raw = json.loads(ds_manifest_path.read_text())
    raw["parquet_sha256"] = "DRIFTED" + "0" * (64 - len("DRIFTED"))
    ds_manifest_path.write_text(json.dumps(raw))

    # Now stale
    sources_after = filesystem_hash_sources(
        strategies_root=tmp_path / "strategies",
        datasets_root=datasets_root,
    )
    res_after = generate_index(runs_root=runs_root, out_root=tmp_path, sources=sources_after)
    assert res_after.stale_runs == 1
    idx_md = (tmp_path / "index.md").read_text()
    assert "stale" in idx_md.lower()
    assert "dataset_hash_changed:ds_test" in idx_md
```

Commit: `test(datasets): end-to-end drift detection via index generator`.

---

## Bundle E — P6 Acceptance + tag

1. Full suite green.
2. Regenerate index + master_summary. Verify runs that declared no dataset_refs (pre-P6) stay `ok` (not stale — they have empty dataset_hashes); new runs declare refs and show fresh status.
3. Acceptance:
   - [ ] `data/nfo/datasets/features/historical_features_2024-01_2026-04/manifest.json` exists with valid sha256.
   - [ ] `data/nfo/datasets/trade_universe/trade_universe_nifty_2024-01_2026-04/manifest.json` exists.
   - [ ] All 6 wrapper scripts pass `dataset_refs`; new runs have non-empty `dataset_hashes`.
   - [ ] End-to-end drift test green.
   - [ ] Full test suite green.
   - [ ] Master design §12 item 7 verified: drift flags runs stale automatically.
4. Write `docs/superpowers/plans/2026-04-22-nfo-platform-phase6-completion.md`.
5. Commit + tag `p6-complete`.

## Deferrals documented in completion report

- `datasets/raw.py` + `datasets/normalized.py` + `datasets/study_inputs.py`
- Legacy reshape helper split (§16 item 8 polish)
- Narrative report archival (file moves)
- Utility script migrations (`v3_fill_gaps.py`, `recost_trades.py`)

---

*End of Phase 6 implementation plan.*
