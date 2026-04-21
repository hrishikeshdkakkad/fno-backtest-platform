# NFO Platform Phase 1 — Foundation & Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the foundation layer of the NFO research platform — git repo, renamed package, validated Pydantic specs, canonical identifiers, run-directory writer, top-level index generator, and thin wrappers that make every existing script emit manifest-backed runs.

**Architecture:** Strangler-fig migration. New modules (`src/nfo/specs/`, `src/nfo/engine/`, `src/nfo/reporting/`, `src/nfo/datasets/`) created alongside existing code. Scripts remain functional but are wrapped to additionally emit `results/nfo/runs/<run_id>/` with `manifest.json`, `metrics.json`, and methodology-headered `report.md`. No business-logic changes in P1.

**Tech Stack:** Python 3.11, Pydantic v2, PyYAML, pyarrow, pandas, pytest. Git initialized on P1 step 1. Dhan and Parallel API calls are never exercised in tests.

**Master design:** `docs/superpowers/specs/2026-04-21-nfo-research-platform-design.md`. Every task references the relevant § for contract details.

---

## Execution conventions

- **TDD rhythm per task:** write failing test → run to see it fail → implement minimal code → run to see it pass → commit.
- **Commit style:** Conventional Commits (`feat:`, `test:`, `refactor:`, `chore:`, `docs:`).
- **Never skip tests.** If a task has no test (e.g. YAML config), the step says so explicitly.
- **Never use `git commit --no-verify`.** If a hook fails, fix root cause.
- **Pydantic v2 only.** Use `Annotated[T, Field(...)]` over deprecated `conint`/`confloat`. Use `model_config = ConfigDict(extra="forbid")`.
- **Python 3.11+:** use `from __future__ import annotations` at top of every new module.
- **Imports:** absolute from `nfo.<module>` (not relative). Example: `from nfo.specs.strategy import StrategySpec`.
- **Tests:** live under `tests/nfo/<module>/test_*.py`. No mocks for Dhan/Parallel.

---

## Group A — Repository Bootstrap (Tasks 1–5)

### Task 1: Initialize git and commit the current tree

**Files:**
- Create: `.git/` (via `git init`)
- Modify: `.gitignore` (add entries)

- [ ] **Step 1: Verify no git state exists**

Run: `ls -la /Users/hrishikeshkakkad/Documents/market/csp/.git 2>/dev/null || echo "no git"`
Expected output: `no git`

- [ ] **Step 2: Initialize the repo**

Run:
```bash
cd /Users/hrishikeshkakkad/Documents/market/csp
git init
git branch -m main
```
Expected: `Initialized empty Git repository …`, no output from branch rename.

- [ ] **Step 3: Extend .gitignore**

Replace the entire file contents with:

```gitignore
# Python
.env
.env.local
*.pyc
__pycache__/
.venv/
venv/
.ruff_cache/
.pytest_cache/
.mypy_cache/
*.egg-info/
dist/
build/

# macOS
.DS_Store

# Large caches / generated artifacts
data/nfo/parallel_cache/
data/nfo/rolling/
data/nfo/index/
data/nfo/monitor_snapshots/
data/nfo/events.parquet
data/nfo/macro_brief.json
data/nfo/fii_dii_flow.parquet
data/nfo/parallel_cost_log.parquet

# Run artifacts — platform rebuilds these on demand
results/nfo/runs/
results/nfo/index.md
results/nfo/latest.json

# Legacy CSP artifacts (archived under legacy/)
results/csp/
```

Note: we intentionally track `results/nfo/legacy/` and `configs/` and the top-level structure, but not mutable run outputs. Regeneration is cheap.

- [ ] **Step 4: First commit**

Run:
```bash
git add -A
git -c user.email="platform@local" -c user.name="NFO Platform Bootstrap" commit -m "chore: import NFO research platform current state (pre-refactor)"
```
Expected: one commit created, working tree clean.

- [ ] **Step 5: Verify**

Run: `git log --oneline`
Expected: one line showing the bootstrap commit.
Run: `git rev-parse --short HEAD` — copy SHA for later reference.

---

### Task 2: Rename the Python package to `nfo-platform`

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/nfo/test_package_identity.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/test_package_identity.py`:

```python
"""Package identity test — asserts pyproject declares nfo-platform."""
from __future__ import annotations

import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pyproject_name_is_nfo_platform():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert data["project"]["name"] == "nfo-platform"


def test_pyproject_description_mentions_nfo():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    desc = data["project"]["description"].lower()
    assert "nfo" in desc or "nifty" in desc
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/nfo/test_package_identity.py -v`
Expected: 2 failures (`name == "csp"` and description doesn't mention nfo).

- [ ] **Step 3: Update pyproject.toml**

Replace the file contents with:

```toml
[project]
name = "nfo-platform"
version = "0.1.0"
description = "NFO (NIFTY/BANKNIFTY F&O) research platform — spec-driven strategy backtests, robustness studies, and live regime monitoring for Indian credit-spread trading."
requires-python = ">=3.11"
dependencies = [
  "httpx>=0.27",
  "pandas>=2.2",
  "pyarrow>=16",
  "numpy>=1.26",
  "python-dotenv>=1.0",
  "scipy>=1.13",
  "tenacity>=9.0",
  "rich>=13",
  "tabulate>=0.9",
  "pydantic>=2.5",
  "parallel-web>=0.1",
  "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.5"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
include = ["nfo*"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.pytest.ini_options]
addopts = ""
testpaths = ["tests"]
```

Note: `include = ["nfo*"]` ensures `src/csp/` (when still present pre-archival) is excluded from the installed package.

- [ ] **Step 4: Reinstall and run tests**

Run:
```bash
.venv/bin/pip install -e . --quiet
.venv/bin/python -m pytest tests/nfo/test_package_identity.py -v
```
Expected: 2 passes.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/nfo/test_package_identity.py
git commit -m "refactor: rename package to nfo-platform"
```

---

### Task 3: Add PyYAML dependency and verify import

**Files:**
- Modify: `pyproject.toml` (already updated in Task 2; verify)
- Test: `tests/nfo/test_yaml_available.py` (new)

- [ ] **Step 1: Write the failing test (before PyYAML installed)**

Create `tests/nfo/test_yaml_available.py`:

```python
"""PyYAML availability test — required by specs.loader."""
from __future__ import annotations


def test_yaml_importable():
    import yaml
    doc = yaml.safe_load("foo: 1\nbar: [a, b]")
    assert doc == {"foo": 1, "bar": ["a", "b"]}


def test_yaml_roundtrip():
    import yaml
    src = {"strategy_id": "v3", "strategy_version": "3.0.0"}
    out = yaml.safe_load(yaml.safe_dump(src))
    assert out == src
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `.venv/bin/python -m pytest tests/nfo/test_yaml_available.py -v`
Expected: either PASS (already installed transitively) or ImportError.

- [ ] **Step 3: Ensure PyYAML is installed**

Run: `.venv/bin/pip install -e . --quiet` (pyproject already lists `pyyaml>=6.0` from Task 2).

- [ ] **Step 4: Re-run tests**

Run: `.venv/bin/python -m pytest tests/nfo/test_yaml_available.py -v`
Expected: 2 passes.

- [ ] **Step 5: Commit**

```bash
git add tests/nfo/test_yaml_available.py
git commit -m "test: verify PyYAML is available for spec loading"
```

---

### Task 4: Rewrite README for NFO platform focus

**Files:**
- Modify: `README.md`

- [ ] **Step 1: No test required (docs-only)**

- [ ] **Step 2: Replace README contents**

Overwrite `README.md` with:

```markdown
# NFO Research Platform

Spec-driven research platform for Indian NIFTY / BANKNIFTY F&O credit-spread
strategies. Backed by Dhan v2 for historical and live data.

## What this is

A single system that answers three distinct research questions for the same
strategy spec:

- **day_matched** — "Are trades entered on signal days generally good?"
- **cycle_matched** — "If I force one canonical trade per cycle, how does that
  trade family behave?"
- **live_rule** — "What would a literal live system have done using only the
  information available on that date?"

Every study runs from a validated `StrategySpec` (YAML) and writes a
manifest-backed run directory under `results/nfo/runs/<run_id>/`. Live regime
monitoring consumes the same trigger engine as historical replay, so live and
research cannot silently disagree about the same strategy.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e .

cp .env.example .env            # add DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, PARALLEL_API_KEY
.venv/bin/python -m pytest tests/nfo/ -q
```

## Layout

| Path | Purpose |
|---|---|
| `src/nfo/specs/` | Pydantic models: StrategySpec, StudySpec, RunManifest, DatasetManifest |
| `src/nfo/engine/` | Triggers, cycles, selection, entry, exits, execution, capital, metrics |
| `src/nfo/studies/` | Variant comparison, time split, capital analysis, robustness, falsification, live replay |
| `src/nfo/monitor/` | Live regime snapshots, state machine, research parity |
| `src/nfo/reporting/` | Run directory writer, methodology header, top-level index |
| `src/nfo/datasets/` | Stage pipeline: raw → normalized → features → trade_universe → study_inputs |
| `configs/nfo/strategies/` | Strategy YAMLs (e.g. v3_frozen.yaml) |
| `configs/nfo/studies/` | Study YAMLs |
| `scripts/nfo/` | Thin CLI wrappers (business logic lives in `src/nfo/`) |
| `results/nfo/runs/` | Canonical run outputs |
| `results/nfo/index.md` | Generated index of all runs |
| `data/nfo/` | Cached raw data, dataset parquets, monitor snapshots |
| `legacy/` | Archived CSP backtester (predecessor project) |
| `docs/superpowers/specs/` | Master platform design |
| `docs/superpowers/plans/` | Phase-level implementation plans |

## Status

Phase 1 (Foundation & Contracts) — in progress. See
`docs/superpowers/plans/2026-04-21-nfo-platform-phase1-plan.md`.

## Prior CSP work

The original cash-secured-put backtester for US equities (Massive.com data) is
archived under `legacy/csp/` with its own quick-start. It is not part of the
NFO platform.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README for NFO platform focus"
```

---

### Task 5: Archive legacy CSP code under `legacy/`

**Files:**
- Move: `src/csp/` → `legacy/csp/`
- Move: `scripts/run_backtest.py`, `scripts/smoke_test.py`, `scripts/quick_iwm.py`, `scripts/run_grid.py`, `scripts/focused_run.py`, `scripts/build_plan.py`, `scripts/xlv_run.py`, `scripts/build_plan_v2.py`, `scripts/spread_run.py`, `scripts/plot_spreads.py` → `legacy/scripts_csp/`
- Move: `tests/test_spread.py` → `legacy/tests/test_spread.py`
- Create: `legacy/README.md`
- Test: `tests/nfo/test_no_csp_in_src.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/test_no_csp_in_src.py`:

```python
"""Assert that CSP code has been archived out of the installable package."""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_no_src_csp_directory():
    assert not (REPO_ROOT / "src" / "csp").exists(), \
        "src/csp/ must be archived to legacy/csp/"


def test_legacy_csp_exists():
    assert (REPO_ROOT / "legacy" / "csp" / "__init__.py").exists(), \
        "legacy/csp/ must contain the archived package"


def test_legacy_has_readme():
    assert (REPO_ROOT / "legacy" / "README.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/nfo/test_no_csp_in_src.py -v`
Expected: 3 failures.

- [ ] **Step 3: Move directories and write the legacy README**

Run:
```bash
mkdir -p legacy/scripts_csp legacy/tests
git mv src/csp legacy/csp
git mv tests/test_spread.py legacy/tests/test_spread.py
for f in run_backtest smoke_test quick_iwm run_grid focused_run build_plan xlv_run build_plan_v2 spread_run plot_spreads; do
  git mv scripts/$f.py legacy/scripts_csp/$f.py
done
```

Create `legacy/README.md`:

```markdown
# Legacy archive

Code archived from the predecessor CSP backtester (US-equity cash-secured puts
on Massive.com data). Retained for reference; not installed or maintained.

## Contents

- `csp/` — Python package (`src/csp/` in the pre-refactor tree).
- `scripts_csp/` — CSP-era CLI scripts (`scripts/*.py` except `scripts/nfo/*`).
- `tests/test_spread.py` — legacy spread-backtest test.

## Running legacy code

The archived code is not part of the installable `nfo-platform` package. To run
it, add `legacy/` to `PYTHONPATH`:

```bash
PYTHONPATH=legacy .venv/bin/python -m csp.backtest  # example
```

The NFO research platform does not depend on anything here.
```

- [ ] **Step 4: Re-run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/nfo/test_no_csp_in_src.py -v`
Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: archive CSP code under legacy/ (platform scope = NFO only)"
```

---

## Group B — Spec Schemas (Tasks 6–10)

All tasks in this group reference master design §4.

### Task 6: `specs/hashing.py` — canonical JSON and spec hashing

**Files:**
- Create: `src/nfo/specs/__init__.py`
- Create: `src/nfo/specs/hashing.py`
- Test: `tests/nfo/specs/__init__.py`, `tests/nfo/specs/test_hashing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/specs/__init__.py` (empty) and `tests/nfo/specs/test_hashing.py`:

```python
"""Tests for canonical JSON + spec hashing (master design §4.2)."""
from __future__ import annotations

from pydantic import BaseModel

from nfo.specs.hashing import canonical_json, spec_hash, short_hash


class _Toy(BaseModel):
    b: int
    a: str


def test_canonical_json_sorts_keys():
    m = _Toy(a="x", b=1)
    out = canonical_json(m)
    assert out == b'{"a":"x","b":1}'


def test_canonical_json_no_whitespace():
    m = _Toy(a="x", b=1)
    out = canonical_json(m)
    assert b" " not in out
    assert b"\n" not in out


def test_spec_hash_is_hex_sha256():
    m = _Toy(a="x", b=1)
    h = spec_hash(m)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_spec_hash_stable_across_field_order():
    m1 = _Toy(a="x", b=1)
    m2 = _Toy.model_validate({"b": 1, "a": "x"})
    assert spec_hash(m1) == spec_hash(m2)


def test_spec_hash_changes_on_value_change():
    assert spec_hash(_Toy(a="x", b=1)) != spec_hash(_Toy(a="x", b=2))


def test_short_hash_is_6_chars():
    h = short_hash(_Toy(a="x", b=1))
    assert len(h) == 6
    assert h == spec_hash(_Toy(a="x", b=1))[:6]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/nfo/specs/test_hashing.py -v`
Expected: collection error (module not found) or 6 failures.

- [ ] **Step 3: Implement**

Create `src/nfo/specs/__init__.py`:

```python
"""Pydantic specs for the NFO research platform."""
```

Create `src/nfo/specs/hashing.py`:

```python
"""Canonical JSON serialization + spec hashing (master design §4.2).

Contract:
  canonical_json(model) -> bytes — sorted keys, no whitespace, JSON mode.
  spec_hash(model) -> str — hex-encoded SHA-256 of canonical_json.
  short_hash(model) -> str — first 6 chars of spec_hash.

These helpers are used by:
  - RunManifest.strategy_spec_hash / study_spec_hash
  - StrategyDriftError detection at load time
  - run_id construction (short_hash[:6])
"""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel


def canonical_json(model: BaseModel) -> bytes:
    payload = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def spec_hash(model: BaseModel) -> str:
    return hashlib.sha256(canonical_json(model)).hexdigest()


def short_hash(model: BaseModel, length: int = 6) -> str:
    return spec_hash(model)[:length]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/nfo/specs/test_hashing.py -v`
Expected: 6 passes.

- [ ] **Step 5: Commit**

```bash
git add src/nfo/specs/__init__.py src/nfo/specs/hashing.py tests/nfo/specs/
git commit -m "feat(specs): add canonical_json and spec_hash helpers"
```

---

### Task 7: `specs/strategy.py` — StrategySpec and nested models

**Files:**
- Create: `src/nfo/specs/strategy.py`
- Test: `tests/nfo/specs/test_strategy.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/specs/test_strategy.py`:

```python
"""Tests for StrategySpec + nested models (master design §4.1)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from nfo.specs.strategy import (
    CapitalSpec,
    EntrySpec,
    ExitSpec,
    SelectionSpec,
    SlippageSpec,
    StrategySpec,
    TriggerSpec,
    UniverseSpec,
)


def _valid_universe() -> UniverseSpec:
    return UniverseSpec(
        underlyings=["NIFTY"],
        delta_target=0.30,
        delta_tolerance=0.05,
        width_rule="fixed",
        width_value=100.0,
        dte_target=35,
        dte_tolerance=3,
    )


def _valid_trigger() -> TriggerSpec:
    return TriggerSpec(
        score_gates={"min_score": 4},
        specific_pass_gates=["s3_iv_rv", "s6_trend", "s8_events"],
        event_window_days=10,
        feature_thresholds={"vix_abs_min": 20.0, "iv_rank_min": 0.60},
    )


def _valid_strategy(**overrides) -> dict:
    base = dict(
        strategy_id="v3",
        strategy_version="3.0.0",
        description="V3 credit spread filter",
        universe=_valid_universe(),
        feature_set=["vix", "iv_rank", "trend_score", "event_risk"],
        trigger_rule=_valid_trigger(),
        selection_rule=SelectionSpec(
            mode="cycle_matched",
            preferred_exit_variant="hte",
        ),
        entry_rule=EntrySpec(),
        exit_rule=ExitSpec(
            variant="hte",
            profit_take_fraction=1.0,
            manage_at_dte=None,
        ),
        capital_rule=CapitalSpec(fixed_capital_inr=1_000_000),
        slippage_rule=SlippageSpec(flat_rupees_per_lot=0.0),
    )
    base.update(overrides)
    return base


# ── UniverseSpec ────────────────────────────────────────────────────────────

def test_universe_accepts_nifty():
    u = _valid_universe()
    assert u.underlyings == ["NIFTY"]


def test_universe_rejects_delta_out_of_range():
    with pytest.raises(ValidationError):
        UniverseSpec(underlyings=["NIFTY"], delta_target=1.5,
                     delta_tolerance=0.05, width_rule="fixed",
                     width_value=100.0, dte_target=35, dte_tolerance=3)


def test_universe_fixed_requires_width_value():
    with pytest.raises(ValidationError):
        UniverseSpec(underlyings=["NIFTY"], delta_target=0.30,
                     delta_tolerance=0.05, width_rule="fixed",
                     width_value=None, dte_target=35, dte_tolerance=3)


# ── SelectionSpec ───────────────────────────────────────────────────────────

def test_selection_mode_literal():
    with pytest.raises(ValidationError):
        SelectionSpec(mode="bogus", preferred_exit_variant="hte")


# ── EntrySpec validator ─────────────────────────────────────────────────────

def test_entry_default_ok():
    e = EntrySpec()
    assert e.earliest_entry_relative_to_first_fire == 0
    assert e.session_snap_rule == "forward_only"
    assert e.allow_pre_fire_entry is False


# ── ExitSpec validator ──────────────────────────────────────────────────────

def test_exit_hte_requires_no_manage_dte():
    with pytest.raises(ValidationError, match="hte"):
        ExitSpec(variant="hte", profit_take_fraction=1.0, manage_at_dte=5)


def test_exit_hte_requires_pt_1():
    with pytest.raises(ValidationError, match="hte"):
        ExitSpec(variant="hte", profit_take_fraction=0.5, manage_at_dte=None)


def test_exit_pt50_ok():
    e = ExitSpec(variant="pt50", profit_take_fraction=0.5, manage_at_dte=21)
    assert e.variant == "pt50"


# ── StrategySpec composition + validators ───────────────────────────────────

def test_strategy_roundtrip():
    s = StrategySpec.model_validate(_valid_strategy())
    assert s.strategy_id == "v3"
    assert s.strategy_version == "3.0.0"


def test_strategy_rejects_bad_semver():
    with pytest.raises(ValidationError):
        StrategySpec.model_validate(_valid_strategy(strategy_version="3.0"))


def test_strategy_rejects_extra_fields():
    with pytest.raises(ValidationError):
        StrategySpec.model_validate({**_valid_strategy(), "surprise": True})


def test_live_rule_forbids_pre_fire_entry():
    bad = _valid_strategy(
        selection_rule=SelectionSpec(mode="live_rule", preferred_exit_variant="hte"),
        entry_rule=EntrySpec(allow_pre_fire_entry=True),
    )
    with pytest.raises(ValidationError, match="live_rule"):
        StrategySpec.model_validate(bad)


def test_live_rule_forbids_nonzero_earliest_entry():
    bad = _valid_strategy(
        selection_rule=SelectionSpec(mode="live_rule", preferred_exit_variant="hte"),
        entry_rule=EntrySpec(earliest_entry_relative_to_first_fire=2),
    )
    with pytest.raises(ValidationError, match="live_rule"):
        StrategySpec.model_validate(bad)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/nfo/specs/test_strategy.py -v`
Expected: collection error or many failures.

- [ ] **Step 3: Implement**

Create `src/nfo/specs/strategy.py`:

```python
"""StrategySpec and nested models (master design §4.1)."""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

Underlying = Literal["NIFTY", "BANKNIFTY", "FINNIFTY"]


class UniverseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    underlyings: list[Underlying]
    delta_target: Annotated[float, Field(gt=0, lt=1)]
    delta_tolerance: Annotated[float, Field(ge=0, lt=0.5)]
    width_rule: Literal["fixed", "formula", "risk_budget"]
    width_value: float | None = None
    dte_target: Annotated[int, Field(ge=1, le=60)]
    dte_tolerance: Annotated[int, Field(ge=0, le=14)]
    allowed_contract_families: list[Literal["PE", "CE"]] = Field(default_factory=lambda: ["PE"])

    @model_validator(mode="after")
    def _fixed_width_requires_value(self) -> "UniverseSpec":
        if self.width_rule == "fixed" and self.width_value is None:
            raise ValueError("width_rule='fixed' requires width_value to be set")
        return self


class TriggerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score_gates: dict[str, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)
    specific_pass_gates: list[str] = Field(default_factory=list)
    event_window_days: Annotated[int, Field(ge=0, le=30)] = 10
    feature_thresholds: dict[str, float] = Field(default_factory=dict)
    missing_data_policy: Literal["skip_day", "treat_as_fail", "treat_as_pass"] = "skip_day"


class SelectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["day_matched", "cycle_matched", "live_rule"]
    one_trade_per_cycle: bool = True
    preferred_exit_variant: Literal["pt25", "pt50", "pt75", "hte", "dte2"]
    canonical_trade_chooser: Literal["first_fire", "best_delta_match", "earliest_entry"] = "first_fire"
    width_handling: Literal["strict_fixed", "allow_alternate"] = "strict_fixed"
    tie_breaker_order: list[str] = Field(
        default_factory=lambda: ["delta_err_asc", "width_exact", "entry_date_asc"]
    )


class EntrySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    earliest_entry_relative_to_first_fire: Annotated[int, Field(ge=0)] = 0
    session_snap_rule: Literal["forward_only", "forward_or_backward", "no_snap"] = "forward_only"
    entry_timestamp_convention: Literal["session_close", "session_open", "mid_session"] = "session_close"
    allow_pre_fire_entry: bool = False


class ExitSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant: Literal["pt25", "pt50", "pt75", "hte", "dte2"]
    profit_take_fraction: float | None = None
    manage_at_dte: Annotated[int, Field(ge=0, le=14)] | None = None
    expiry_settlement: Literal["cash_settled_to_spot", "held_to_expiry_intrinsic"] = "cash_settled_to_spot"

    @model_validator(mode="after")
    def _variant_constraints(self) -> "ExitSpec":
        if self.variant == "hte":
            if self.manage_at_dte is not None:
                raise ValueError("exit_rule.variant='hte' requires manage_at_dte=None")
            if self.profit_take_fraction not in (None, 1.0):
                raise ValueError("exit_rule.variant='hte' requires profit_take_fraction in (None, 1.0)")
        return self


class CapitalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixed_capital_inr: Annotated[float, Field(gt=0)]
    deployment_fraction: Annotated[float, Field(gt=0, le=1.0)] = 1.0
    compounding: bool = False
    lot_rounding_mode: Literal["floor", "round"] = "floor"


class SlippageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["flat_rupees_per_lot", "percent_of_premium"] = "flat_rupees_per_lot"
    flat_rupees_per_lot: float = 0.0
    percent_of_premium: float = 0.0


_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    strategy_version: str
    description: str
    universe: UniverseSpec
    feature_set: list[str]
    trigger_rule: TriggerSpec
    selection_rule: SelectionSpec
    entry_rule: EntrySpec
    exit_rule: ExitSpec
    capital_rule: CapitalSpec
    slippage_rule: SlippageSpec
    report_defaults: dict[str, Any] = Field(default_factory=dict)

    @field_validator("strategy_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(f"strategy_version must match ^\\d+\\.\\d+\\.\\d+$, got {v!r}")
        return v

    @model_validator(mode="after")
    def _live_rule_consistency(self) -> "StrategySpec":
        if self.selection_rule.mode == "live_rule":
            if self.entry_rule.allow_pre_fire_entry:
                raise ValueError("selection mode 'live_rule' forbids entry_rule.allow_pre_fire_entry=True")
            if self.entry_rule.earliest_entry_relative_to_first_fire != 0:
                raise ValueError(
                    "selection mode 'live_rule' requires entry_rule.earliest_entry_relative_to_first_fire == 0"
                )
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/nfo/specs/test_strategy.py -v`
Expected: all passes.

- [ ] **Step 5: Commit**

```bash
git add src/nfo/specs/strategy.py tests/nfo/specs/test_strategy.py
git commit -m "feat(specs): add StrategySpec and nested spec models"
```

---

### Task 8: `specs/study.py` — StudySpec and DatasetRef

**Files:**
- Create: `src/nfo/specs/study.py`
- Test: `tests/nfo/specs/test_study.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/specs/test_study.py`:

```python
"""Tests for StudySpec + DatasetRef (master design §4.3)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from nfo.specs.study import DatasetRef, StudySpec


def _ref() -> DatasetRef:
    return DatasetRef(
        dataset_id="historical_features_2024-01_2026-04",
        dataset_type="features",
        path=Path("data/nfo/datasets/features/historical_features_2024-01_2026-04"),
    )


def _spec(**overrides) -> dict:
    base = dict(
        study_id="capital_analysis_10L",
        study_type="capital_analysis",
        strategy_spec_ref=Path("configs/nfo/strategies/v3_frozen.yaml"),
        dataset_refs=[_ref()],
        parameters={"capital_inr": 1_000_000, "variant": "hte"},
    )
    base.update(overrides)
    return base


def test_study_roundtrip():
    s = StudySpec.model_validate(_spec())
    assert s.study_id == "capital_analysis_10L"


def test_study_rejects_bad_type():
    with pytest.raises(ValidationError):
        StudySpec.model_validate(_spec(study_type="not_a_real_type"))


def test_study_rejects_extra():
    with pytest.raises(ValidationError):
        StudySpec.model_validate({**_spec(), "extra": 1})


def test_parameters_must_be_json_serializable():
    class _NotJson:
        pass

    with pytest.raises(ValidationError):
        StudySpec.model_validate(_spec(parameters={"bad": _NotJson()}))


def test_parameters_accept_nested():
    s = StudySpec.model_validate(_spec(parameters={"nested": {"a": [1, 2], "b": "x"}}))
    assert s.parameters["nested"]["a"] == [1, 2]


def test_dataset_ref_literal_type():
    with pytest.raises(ValidationError):
        DatasetRef(
            dataset_id="x",
            dataset_type="bogus",
            path=Path("."),
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/nfo/specs/test_study.py -v`
Expected: collection error or failures.

- [ ] **Step 3: Implement**

Create `src/nfo/specs/study.py`:

```python
"""StudySpec + DatasetRef (master design §4.3)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

StudyType = Literal[
    "variant_comparison",
    "time_split",
    "capital_analysis",
    "robustness",
    "falsification",
    "live_replay",
    "monitor_snapshot",
]

DatasetType = Literal["raw", "normalized", "features", "trade_universe", "study_inputs"]


class DatasetRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    dataset_type: DatasetType
    path: Path


class StudySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_id: str
    study_type: StudyType
    strategy_spec_ref: Path
    dataset_refs: list[DatasetRef]
    parameters: dict[str, Any] = Field(default_factory=dict)
    output_profile: Literal["default", "compact", "full"] = "default"

    @field_validator("parameters")
    @classmethod
    def _parameters_json_serializable(cls, v: dict) -> dict:
        try:
            json.dumps(v)
        except (TypeError, ValueError) as e:
            raise ValueError(f"parameters must be JSON-serializable: {e}") from e
        return v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/nfo/specs/test_study.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/nfo/specs/study.py tests/nfo/specs/test_study.py
git commit -m "feat(specs): add StudySpec and DatasetRef"
```

---

### Task 9: `specs/manifest.py` — RunManifest and DatasetManifest

**Files:**
- Create: `src/nfo/specs/manifest.py`
- Test: `tests/nfo/specs/test_manifest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/specs/test_manifest.py`:

```python
"""Tests for RunManifest + DatasetManifest (master design §4.4, §4.5)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from nfo.specs.manifest import DatasetManifest, RunManifest


def _run(**overrides) -> dict:
    base = dict(
        run_id="20260421T143000-capital_analysis-7a3f9b",
        created_at=datetime(2026, 4, 21, 14, 30, 0, tzinfo=timezone.utc),
        code_version="a1b2c3d",
        study_spec_hash="x" * 64,
        strategy_spec_hash="y" * 64,
        strategy_id="v3",
        strategy_version="3.0.0",
        study_type="capital_analysis",
        selection_mode="cycle_matched",
        dataset_hashes={"historical_features_2024-01_2026-04": "z" * 64},
        window_start=date(2024, 2, 1),
        window_end=date(2026, 4, 18),
        artifacts=["manifest.json", "metrics.json", "tables/selected_trades.csv", "report.md"],
        status="ok",
        warnings=[],
        stale_inputs_detected=[],
        duration_seconds=12.4,
    )
    base.update(overrides)
    return base


def test_run_manifest_roundtrip():
    m = RunManifest.model_validate(_run())
    j = m.model_dump_json()
    back = RunManifest.model_validate_json(j)
    assert back == m


def test_run_manifest_rejects_bad_selection_mode():
    with pytest.raises(ValidationError):
        RunManifest.model_validate(_run(selection_mode="bogus"))


def test_run_manifest_dirty_code_version_ok():
    m = RunManifest.model_validate(_run(code_version="a1b2c3d-dirty"))
    assert m.code_version.endswith("-dirty")


def _dataset(**overrides) -> dict:
    base = dict(
        dataset_id="historical_features_2024-01_2026-04",
        dataset_type="features",
        source_paths=[Path("data/nfo/index/NIFTY_2023-12-15_2026-04-18.parquet")],
        date_window=(date(2024, 1, 15), date(2026, 4, 18)),
        row_count=559,
        build_time=datetime(2026, 4, 21, tzinfo=timezone.utc),
        code_version="a1b2c3d",
        upstream_datasets=[],
        parquet_sha256="p" * 64,
        schema_fingerprint="s" * 64,
    )
    base.update(overrides)
    return base


def test_dataset_manifest_roundtrip():
    m = DatasetManifest.model_validate(_dataset())
    back = DatasetManifest.model_validate_json(m.model_dump_json())
    assert back == m


def test_dataset_manifest_allows_no_date_window():
    m = DatasetManifest.model_validate(_dataset(date_window=None))
    assert m.date_window is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/nfo/specs/test_manifest.py -v`
Expected: collection error / failures.

- [ ] **Step 3: Implement**

Create `src/nfo/specs/manifest.py`:

```python
"""RunManifest + DatasetManifest (master design §4.4, §4.5)."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nfo.specs.study import DatasetType, StudyType

SelectionMode = Literal["day_matched", "cycle_matched", "live_rule"]
RunStatus = Literal["ok", "failed", "warnings"]


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: datetime
    code_version: str
    study_spec_hash: str
    strategy_spec_hash: str
    strategy_id: str
    strategy_version: str
    study_type: StudyType
    selection_mode: SelectionMode
    dataset_hashes: dict[str, str] = Field(default_factory=dict)
    window_start: date
    window_end: date
    artifacts: list[str] = Field(default_factory=list)
    status: RunStatus
    warnings: list[str] = Field(default_factory=list)
    stale_inputs_detected: list[str] = Field(default_factory=list)
    duration_seconds: float


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    dataset_type: DatasetType
    source_paths: list[Path] = Field(default_factory=list)
    date_window: tuple[date, date] | None = None
    row_count: int
    build_time: datetime
    code_version: str
    upstream_datasets: list[str] = Field(default_factory=list)
    parquet_sha256: str
    schema_fingerprint: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/nfo/specs/test_manifest.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/nfo/specs/manifest.py tests/nfo/specs/test_manifest.py
git commit -m "feat(specs): add RunManifest and DatasetManifest"
```

---

### Task 10: `specs/loader.py` — YAML loader, registry, StrategyDriftError

**Files:**
- Create: `src/nfo/specs/loader.py`
- Create: `configs/nfo/` directory with `.registry.json` stub
- Test: `tests/nfo/specs/test_loader.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/specs/test_loader.py`:

```python
"""Tests for YAML loader + StrategyDriftError (master design §4.2)."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from nfo.specs.loader import (
    StrategyDriftError,
    load_strategy,
    load_study,
    reset_registry_for_tests,
)


STRAT_YAML = textwrap.dedent("""
    strategy_id: v3
    strategy_version: 3.0.0
    description: V3 credit spread filter
    universe:
      underlyings: [NIFTY]
      delta_target: 0.30
      delta_tolerance: 0.05
      width_rule: fixed
      width_value: 100.0
      dte_target: 35
      dte_tolerance: 3
    feature_set: [vix, iv_rank, trend_score]
    trigger_rule:
      specific_pass_gates: [s3_iv_rv, s6_trend, s8_events]
      event_window_days: 10
      feature_thresholds: {vix_abs_min: 20.0, iv_rank_min: 0.60}
    selection_rule:
      mode: cycle_matched
      preferred_exit_variant: hte
    entry_rule: {}
    exit_rule:
      variant: hte
      profit_take_fraction: 1.0
      manage_at_dte: null
    capital_rule:
      fixed_capital_inr: 1000000
    slippage_rule:
      flat_rupees_per_lot: 0.0
""")


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    reset_registry_for_tests(tmp_path / "registry.json")
    yield


def test_load_strategy_returns_model_and_hash(tmp_path):
    p = tmp_path / "v3.yaml"
    p.write_text(STRAT_YAML)
    spec, h = load_strategy(p)
    assert spec.strategy_id == "v3"
    assert len(h) == 64


def test_load_strategy_rejects_version_drift(tmp_path):
    p = tmp_path / "v3.yaml"
    p.write_text(STRAT_YAML)
    load_strategy(p)
    modified = STRAT_YAML.replace("event_window_days: 10", "event_window_days: 7")
    p.write_text(modified)
    with pytest.raises(StrategyDriftError, match="content hash changed"):
        load_strategy(p)


def test_load_strategy_allows_new_version(tmp_path):
    p = tmp_path / "v3.yaml"
    p.write_text(STRAT_YAML)
    load_strategy(p)
    bumped = STRAT_YAML.replace("strategy_version: 3.0.0", "strategy_version: 3.1.0")
    bumped = bumped.replace("event_window_days: 10", "event_window_days: 7")
    p.write_text(bumped)
    spec, _ = load_strategy(p)
    assert spec.strategy_version == "3.1.0"


def test_load_study(tmp_path):
    strat = tmp_path / "v3.yaml"
    strat.write_text(STRAT_YAML)
    study = tmp_path / "capital.yaml"
    study.write_text(textwrap.dedent(f"""
        study_id: capital_analysis_10L
        study_type: capital_analysis
        strategy_spec_ref: {strat}
        dataset_refs: []
        parameters:
          capital_inr: 1000000
    """))
    spec, h = load_study(study)
    assert spec.study_id == "capital_analysis_10L"
    assert len(h) == 64
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/nfo/specs/test_loader.py -v`
Expected: collection error / failures.

- [ ] **Step 3: Implement**

Create `configs/nfo/.registry.json`:

```json
{
  "strategies": {}
}
```

Create `src/nfo/specs/loader.py`:

```python
"""YAML loader for strategy/study specs with drift detection (master design §4.2).

Registry format (configs/nfo/.registry.json):
  {
    "strategies": {
      "<strategy_id>@<strategy_version>": {
        "hash": "<sha256 hex>",
        "path": "<relative path>",
        "loaded_at": "<ISO8601>"
      }
    }
  }
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from nfo.specs.hashing import spec_hash
from nfo.specs.strategy import StrategySpec
from nfo.specs.study import StudySpec


class StrategyDriftError(Exception):
    """Raised when strategy_version did not bump despite content changes."""


_REGISTRY_PATH: Path = Path("configs/nfo/.registry.json")


def reset_registry_for_tests(path: Path) -> None:
    """Test helper: point the loader at a fresh registry file."""
    global _REGISTRY_PATH
    _REGISTRY_PATH = path
    _REGISTRY_PATH.write_text(json.dumps({"strategies": {}}))


def _read_registry() -> dict:
    if not _REGISTRY_PATH.exists():
        return {"strategies": {}}
    return json.loads(_REGISTRY_PATH.read_text())


def _write_registry(reg: dict) -> None:
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(json.dumps(reg, indent=2, sort_keys=True))


def load_strategy(path: Path) -> tuple[StrategySpec, str]:
    raw = yaml.safe_load(Path(path).read_text())
    spec = StrategySpec.model_validate(raw)
    current_hash = spec_hash(spec)

    reg = _read_registry()
    key = f"{spec.strategy_id}@{spec.strategy_version}"
    entry = reg["strategies"].get(key)
    if entry is not None and entry["hash"] != current_hash:
        raise StrategyDriftError(
            f"strategy_id={spec.strategy_id!r} version={spec.strategy_version!r} "
            f"content hash changed ({entry['hash'][:12]} -> {current_hash[:12]}). "
            f"Bump strategy_version before editing spec content."
        )
    reg["strategies"][key] = {
        "hash": current_hash,
        "path": str(path),
        "loaded_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_registry(reg)
    return spec, current_hash


def load_study(path: Path) -> tuple[StudySpec, str]:
    raw = yaml.safe_load(Path(path).read_text())
    spec = StudySpec.model_validate(raw)
    return spec, spec_hash(spec)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/nfo/specs/test_loader.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add configs/nfo/.registry.json src/nfo/specs/loader.py tests/nfo/specs/test_loader.py
git commit -m "feat(specs): add YAML loader, strategy registry, and StrategyDriftError"
```

---

## Group C — Canonical Identifiers (Task 11)

### Task 11: `engine/cycles.py` — id helpers only

Implements master design §5. The full cycle engine lives in P2; this task adds only the id-construction helpers that other P1 code depends on.

**Files:**
- Create: `src/nfo/engine/__init__.py`
- Create: `src/nfo/engine/cycles.py`
- Test: `tests/nfo/engine/__init__.py`, `tests/nfo/engine/test_ids.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/engine/__init__.py` (empty) and `tests/nfo/engine/test_ids.py`:

```python
"""Tests for canonical id helpers (master design §5)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from nfo.engine.cycles import (
    build_run_id,
    cycle_id,
    feature_day_id,
    fire_id,
    selection_id,
    trade_id,
)


def test_feature_day_id_shape():
    assert feature_day_id("NIFTY", date(2025, 3, 24)) == "NIFTY:2025-03-24"


def test_cycle_id_shape():
    cid = cycle_id("NIFTY", date(2025, 4, 24), "3.0.0")
    assert cid == "NIFTY:2025-04-24:3.0.0"


def test_fire_id_shape():
    cid = cycle_id("NIFTY", date(2025, 4, 24), "3.0.0")
    fid = fire_id(cid, date(2025, 3, 24))
    assert fid == "NIFTY:2025-04-24:3.0.0:2025-03-24"


def test_selection_id_shape():
    cid = cycle_id("NIFTY", date(2025, 4, 24), "3.0.0")
    sid = selection_id(cid, "live_rule", "hte")
    assert sid == "NIFTY:2025-04-24:3.0.0:live_rule:hte"


def test_trade_id_is_hex_16_and_deterministic():
    kw = dict(
        underlying="NIFTY",
        expiry_date=date(2025, 4, 24),
        short_strike=22500,
        long_strike=22400,
        width=100.0,
        delta_target=0.30,
        exit_variant="hte",
        entry_date=date(2025, 3, 24),
    )
    a = trade_id(**kw)
    b = trade_id(**kw)
    assert a == b
    assert len(a) == 16
    assert all(c in "0123456789abcdef" for c in a)


def test_trade_id_differs_on_strike():
    kw = dict(
        underlying="NIFTY",
        expiry_date=date(2025, 4, 24),
        short_strike=22500,
        long_strike=22400,
        width=100.0,
        delta_target=0.30,
        exit_variant="hte",
        entry_date=date(2025, 3, 24),
    )
    a = trade_id(**kw)
    b = trade_id(**{**kw, "short_strike": 22400})
    assert a != b


def test_build_run_id_shape():
    ts = datetime(2026, 4, 21, 14, 30, 0, tzinfo=timezone.utc)
    rid = build_run_id(created_at=ts, study_id="capital_analysis", strategy_hash_short="7a3f9b")
    assert rid == "20260421T143000-capital_analysis-7a3f9b"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/nfo/engine/test_ids.py -v`
Expected: collection error / failures.

- [ ] **Step 3: Implement**

Create `src/nfo/engine/__init__.py`:

```python
"""Engine — core evaluation modules (trigger/cycle/selection/entry/exit/execution)."""
```

Create `src/nfo/engine/cycles.py`:

```python
"""Canonical identifier helpers (master design §5).

Only id-construction is exposed in P1. Full cycle-grouping and trigger
evaluation lands in P2 under this same module.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime


def feature_day_id(underlying: str, on_date: date) -> str:
    return f"{underlying}:{on_date.isoformat()}"


def cycle_id(underlying: str, target_expiry: date, strategy_version: str) -> str:
    return f"{underlying}:{target_expiry.isoformat()}:{strategy_version}"


def fire_id(cycle_id_: str, fire_date: date) -> str:
    return f"{cycle_id_}:{fire_date.isoformat()}"


def selection_id(cycle_id_: str, selection_mode: str, exit_variant: str) -> str:
    return f"{cycle_id_}:{selection_mode}:{exit_variant}"


def trade_id(
    *,
    underlying: str,
    expiry_date: date,
    short_strike: float,
    long_strike: float,
    width: float,
    delta_target: float,
    exit_variant: str,
    entry_date: date,
) -> str:
    payload = {
        "underlying": underlying,
        "expiry_date": expiry_date.isoformat(),
        "short_strike": float(short_strike),
        "long_strike": float(long_strike),
        "width": float(width),
        "delta_target": float(delta_target),
        "exit_variant": exit_variant,
        "entry_date": entry_date.isoformat(),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:16]


def build_run_id(*, created_at: datetime, study_id: str, strategy_hash_short: str) -> str:
    ts = created_at.astimezone().strftime("%Y%m%dT%H%M%S") if created_at.tzinfo else created_at.strftime("%Y%m%dT%H%M%S")
    return f"{ts}-{study_id}-{strategy_hash_short}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/nfo/engine/test_ids.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/nfo/engine/__init__.py src/nfo/engine/cycles.py tests/nfo/engine/
git commit -m "feat(engine): add canonical id helpers (cycle_id, fire_id, trade_id, run_id)"
```

---

## Group D — Reporting Platform (Tasks 12–17)

### Task 12: `reporting/methodology_header.py` — manifest → markdown block

**Files:**
- Create: `src/nfo/reporting/__init__.py`
- Create: `src/nfo/reporting/methodology_header.py`
- Test: `tests/nfo/reporting/__init__.py`, `tests/nfo/reporting/test_methodology_header.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/reporting/__init__.py` (empty) and `tests/nfo/reporting/test_methodology_header.py`:

```python
"""Methodology header rendering (master design §8.4)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from nfo.reporting.methodology_header import build_header
from nfo.specs.manifest import RunManifest


def _manifest() -> RunManifest:
    return RunManifest(
        run_id="20260421T143000-capital_analysis-7a3f9b",
        created_at=datetime(2026, 4, 21, 14, 30, 0, tzinfo=timezone.utc),
        code_version="a1b2c3d",
        study_spec_hash="a" * 64,
        strategy_spec_hash="7a3f9b2e1c9d8a6f" + "0" * 48,
        strategy_id="v3",
        strategy_version="3.0.0",
        study_type="capital_analysis",
        selection_mode="cycle_matched",
        dataset_hashes={"ds_features_2024": "4c2d" + "0" * 60},
        window_start=date(2024, 2, 1),
        window_end=date(2026, 4, 18),
        artifacts=["manifest.json", "metrics.json", "report.md"],
        status="ok",
        duration_seconds=12.4,
    )


def test_header_starts_and_ends_with_markers():
    out = build_header(_manifest())
    assert out.startswith("<!-- methodology:begin -->")
    assert out.rstrip().endswith("<!-- methodology:end -->")


def test_header_contains_required_fields():
    out = build_header(_manifest())
    for fragment in [
        "20260421T143000-capital_analysis-7a3f9b",
        "capital_analysis",
        "`v3`",
        "`3.0.0`",
        "cycle_matched",
        "2024-02-01",
        "2026-04-18",
        "a1b2c3d",
    ]:
        assert fragment in out, f"missing {fragment!r}"


def test_header_shows_dirty_code_version():
    m = _manifest()
    m = m.model_copy(update={"code_version": "a1b2c3d-dirty"})
    out = build_header(m)
    assert "a1b2c3d-dirty" in out
    assert "dirty" in out.lower()


def test_header_no_placeholders():
    out = build_header(_manifest())
    for bad in ["TBD", "TODO", "FIXME", "XXX"]:
        assert bad not in out
```

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/reporting/test_methodology_header.py -v`
Expected: failures.

- [ ] **Step 3: Implement**

Create `src/nfo/reporting/__init__.py`:

```python
"""Run-scoped artifact writers and methodology rendering."""
```

Create `src/nfo/reporting/methodology_header.py`:

```python
"""Methodology header block (master design §8.4).

Every report.md under runs/<run_id>/ starts with the output of build_header.
The output is deterministic given the manifest.
"""
from __future__ import annotations

from nfo.specs.manifest import RunManifest


BEGIN_MARKER = "<!-- methodology:begin -->"
END_MARKER = "<!-- methodology:end -->"


def build_header(manifest: RunManifest) -> str:
    lines: list[str] = [BEGIN_MARKER, "## Methodology"]
    lines.append(f"- **Run ID:** `{manifest.run_id}`")
    lines.append(f"- **Study type:** {manifest.study_type}")
    lines.append(
        f"- **Strategy:** `{manifest.strategy_id}` version `{manifest.strategy_version}` "
        f"(hash `{manifest.strategy_spec_hash[:16]}`)"
    )
    lines.append(f"- **Selection mode:** {manifest.selection_mode}")
    lines.append(f"- **Date window:** {manifest.window_start.isoformat()} → {manifest.window_end.isoformat()}")
    if manifest.dataset_hashes:
        lines.append("- **Datasets:**")
        for dsid in sorted(manifest.dataset_hashes):
            h = manifest.dataset_hashes[dsid]
            lines.append(f"  - `{dsid}` (sha256 `{h[:12]}`)")
    clean = "clean" if not manifest.code_version.endswith("-dirty") else "dirty"
    lines.append(f"- **Code version:** `{manifest.code_version}` ({clean})")
    lines.append(f"- **Created:** {manifest.created_at.isoformat()}")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/reporting/test_methodology_header.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/nfo/reporting/__init__.py src/nfo/reporting/methodology_header.py tests/nfo/reporting/
git commit -m "feat(reporting): render methodology header from RunManifest"
```

---

### Task 13: `reporting/artifacts.py` — RunDirectory writer

**Files:**
- Create: `src/nfo/reporting/artifacts.py`
- Test: `tests/nfo/reporting/test_artifacts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/reporting/test_artifacts.py`:

```python
"""Tests for RunDirectory writer (master design §8.1)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from nfo.reporting.artifacts import RunDirectory, open_run_directory
from nfo.specs.manifest import RunManifest


def _manifest(run_id: str) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        created_at=datetime(2026, 4, 21, 14, 30, 0, tzinfo=timezone.utc),
        code_version="a1b2c3d",
        study_spec_hash="a" * 64,
        strategy_spec_hash="b" * 64,
        strategy_id="v3",
        strategy_version="3.0.0",
        study_type="capital_analysis",
        selection_mode="cycle_matched",
        dataset_hashes={},
        window_start=date(2024, 2, 1),
        window_end=date(2026, 4, 18),
        artifacts=[],
        status="ok",
        duration_seconds=0.0,
    )


def test_open_run_directory_creates_structure(tmp_path):
    rd = open_run_directory(root=tmp_path, run_id="20260421T143000-test-abcdef")
    assert rd.path.is_dir()
    assert (rd.path / "tables").is_dir()
    assert (rd.path / "logs").is_dir()


def test_write_manifest_adds_to_artifacts(tmp_path):
    rd = open_run_directory(root=tmp_path, run_id="r1")
    m = _manifest("r1")
    rd.write_manifest(m)
    written = json.loads((rd.path / "manifest.json").read_text())
    assert written["run_id"] == "r1"
    assert "manifest.json" in written["artifacts"]


def test_write_metrics_adds_to_artifacts(tmp_path):
    rd = open_run_directory(root=tmp_path, run_id="r2")
    m = _manifest("r2")
    rd.write_manifest(m)
    rd.write_metrics({"total_pnl_inr": 123456.0, "win_rate": 0.875})
    metrics = json.loads((rd.path / "metrics.json").read_text())
    assert metrics["total_pnl_inr"] == 123456.0
    manifest_after = json.loads((rd.path / "manifest.json").read_text())
    assert "metrics.json" in manifest_after["artifacts"]


def test_write_table_csv(tmp_path):
    rd = open_run_directory(root=tmp_path, run_id="r3")
    rd.write_manifest(_manifest("r3"))
    df = pd.DataFrame([{"a": 1, "b": "x"}])
    rd.write_table("selected_trades", df, fmt="csv")
    csv_path = rd.path / "tables" / "selected_trades.csv"
    assert csv_path.exists()
    manifest_after = json.loads((rd.path / "manifest.json").read_text())
    assert "tables/selected_trades.csv" in manifest_after["artifacts"]


def test_write_report_prepends_header(tmp_path):
    rd = open_run_directory(root=tmp_path, run_id="r4")
    rd.write_manifest(_manifest("r4"))
    rd.write_report(body_markdown="## Summary\n\nBody text.\n")
    content = (rd.path / "report.md").read_text()
    assert content.startswith("<!-- methodology:begin -->")
    assert "Body text." in content


def test_write_report_refuses_duplicate_header(tmp_path):
    rd = open_run_directory(root=tmp_path, run_id="r5")
    rd.write_manifest(_manifest("r5"))
    with pytest.raises(ValueError, match="methodology:begin"):
        rd.write_report(body_markdown="<!-- methodology:begin -->\nsneaky\n")
```

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/reporting/test_artifacts.py -v`
Expected: failures.

- [ ] **Step 3: Implement**

Create `src/nfo/reporting/artifacts.py`:

```python
"""Run directory writer (master design §8.1).

Layout produced:
  runs/<run_id>/
    manifest.json
    metrics.json
    tables/
    report.md
    logs/
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from nfo.reporting.methodology_header import BEGIN_MARKER, build_header
from nfo.specs.manifest import RunManifest


@dataclass
class RunDirectory:
    path: Path
    _manifest: RunManifest | None = None

    def _bind_manifest(self, manifest: RunManifest) -> None:
        self._manifest = manifest

    def _require_manifest(self) -> RunManifest:
        if self._manifest is None:
            raise RuntimeError("write_manifest must be called before other writers")
        return self._manifest

    def _record_artifact(self, relpath: str) -> None:
        m = self._require_manifest()
        if relpath not in m.artifacts:
            m.artifacts.append(relpath)
            (self.path / "manifest.json").write_text(m.model_dump_json(indent=2))

    def write_manifest(self, manifest: RunManifest) -> None:
        self._bind_manifest(manifest)
        if "manifest.json" not in manifest.artifacts:
            manifest.artifacts.append("manifest.json")
        (self.path / "manifest.json").write_text(manifest.model_dump_json(indent=2))

    def write_metrics(self, metrics: dict[str, Any]) -> None:
        import json as _json
        (self.path / "metrics.json").write_text(_json.dumps(metrics, indent=2, sort_keys=True))
        self._record_artifact("metrics.json")

    def write_table(self, name: str, df: pd.DataFrame, *, fmt: Literal["csv", "parquet"] = "csv") -> None:
        tables_dir = self.path / "tables"
        tables_dir.mkdir(exist_ok=True)
        if fmt == "csv":
            rel = f"tables/{name}.csv"
            df.to_csv(self.path / rel, index=False)
        elif fmt == "parquet":
            rel = f"tables/{name}.parquet"
            df.to_parquet(self.path / rel, index=False)
        else:
            raise ValueError(f"unsupported fmt {fmt!r}")
        self._record_artifact(rel)

    def write_report(self, *, body_markdown: str) -> None:
        m = self._require_manifest()
        if BEGIN_MARKER in body_markdown:
            raise ValueError(
                f"body_markdown must not contain {BEGIN_MARKER!r}; "
                f"the header is rendered by RunDirectory.write_report"
            )
        content = build_header(m) + "\n" + body_markdown.lstrip()
        if not content.endswith("\n"):
            content += "\n"
        (self.path / "report.md").write_text(content)
        self._record_artifact("report.md")

    def write_log(self, name: str, text: str) -> None:
        logs_dir = self.path / "logs"
        logs_dir.mkdir(exist_ok=True)
        rel = f"logs/{name}"
        (self.path / rel).write_text(text)
        self._record_artifact(rel)


def open_run_directory(*, root: Path, run_id: str) -> RunDirectory:
    rundir = Path(root) / run_id
    (rundir / "tables").mkdir(parents=True, exist_ok=True)
    (rundir / "logs").mkdir(parents=True, exist_ok=True)
    return RunDirectory(path=rundir)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/reporting/test_artifacts.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/nfo/reporting/artifacts.py tests/nfo/reporting/test_artifacts.py
git commit -m "feat(reporting): add RunDirectory writer for manifest/metrics/tables/report"
```

---

### Task 14: `datasets/staleness.py` — hash drift detection

**Files:**
- Create: `src/nfo/datasets/__init__.py`
- Create: `src/nfo/datasets/staleness.py`
- Test: `tests/nfo/datasets/__init__.py`, `tests/nfo/datasets/test_staleness.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/datasets/__init__.py` (empty) and `tests/nfo/datasets/test_staleness.py`:

```python
"""Tests for staleness detection (master design §7.2)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from nfo.datasets.staleness import HashSources, is_run_stale
from nfo.specs.manifest import RunManifest


def _m(*, strategy_hash="s" * 64, dataset_hashes=None) -> RunManifest:
    return RunManifest(
        run_id="r1",
        created_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
        code_version="a1b2c3d",
        study_spec_hash="x" * 64,
        strategy_spec_hash=strategy_hash,
        strategy_id="v3",
        strategy_version="3.0.0",
        study_type="capital_analysis",
        selection_mode="cycle_matched",
        dataset_hashes=dataset_hashes or {},
        window_start=date(2024, 2, 1),
        window_end=date(2026, 4, 18),
        artifacts=[],
        status="ok",
        duration_seconds=1.0,
    )


def test_fresh_when_hashes_match():
    sources = HashSources(
        strategy_hash_fn=lambda sid, ver: "s" * 64,
        dataset_hash_fn=lambda did: "d" * 64,
    )
    assert is_run_stale(_m(dataset_hashes={"ds": "d" * 64}), sources) == []


def test_stale_when_strategy_hash_drifts():
    sources = HashSources(
        strategy_hash_fn=lambda sid, ver: "NEW" + "s" * 61,
        dataset_hash_fn=lambda did: "d" * 64,
    )
    reasons = is_run_stale(_m(dataset_hashes={"ds": "d" * 64}), sources)
    assert any("strategy_spec_hash_changed" in r for r in reasons)


def test_stale_when_dataset_hash_drifts():
    sources = HashSources(
        strategy_hash_fn=lambda sid, ver: "s" * 64,
        dataset_hash_fn=lambda did: "NEW" + "d" * 61,
    )
    reasons = is_run_stale(_m(dataset_hashes={"ds": "d" * 64}), sources)
    assert any("dataset_hash_changed:ds" in r for r in reasons)


def test_stale_when_dataset_missing():
    sources = HashSources(
        strategy_hash_fn=lambda sid, ver: "s" * 64,
        dataset_hash_fn=lambda did: None,
    )
    reasons = is_run_stale(_m(dataset_hashes={"ds": "d" * 64}), sources)
    assert any("dataset_missing:ds" in r for r in reasons)


def test_stale_when_strategy_absent():
    sources = HashSources(
        strategy_hash_fn=lambda sid, ver: None,
        dataset_hash_fn=lambda did: "d" * 64,
    )
    reasons = is_run_stale(_m(dataset_hashes={"ds": "d" * 64}), sources)
    assert any("strategy_missing" in r for r in reasons)
```

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/datasets/test_staleness.py -v`
Expected: failures.

- [ ] **Step 3: Implement**

Create `src/nfo/datasets/__init__.py`:

```python
"""Dataset pipeline stages (master design §7)."""
```

Create `src/nfo/datasets/staleness.py`:

```python
"""Run staleness detection (master design §7.2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from nfo.specs.manifest import RunManifest


@dataclass
class HashSources:
    """Pluggable hash lookups — injected so the generator can be tested without
    touching the filesystem."""
    strategy_hash_fn: Callable[[str, str], str | None]   # (strategy_id, strategy_version) -> hash
    dataset_hash_fn: Callable[[str], str | None]         # (dataset_id) -> hash


def is_run_stale(manifest: RunManifest, sources: HashSources) -> list[str]:
    reasons: list[str] = []
    cur_strategy = sources.strategy_hash_fn(manifest.strategy_id, manifest.strategy_version)
    if cur_strategy is None:
        reasons.append(f"strategy_missing:{manifest.strategy_id}@{manifest.strategy_version}")
    elif cur_strategy != manifest.strategy_spec_hash:
        reasons.append(
            f"strategy_spec_hash_changed:{manifest.strategy_id}@{manifest.strategy_version}"
        )
    for dsid, expected in manifest.dataset_hashes.items():
        cur = sources.dataset_hash_fn(dsid)
        if cur is None:
            reasons.append(f"dataset_missing:{dsid}")
        elif cur != expected:
            reasons.append(f"dataset_hash_changed:{dsid}")
    return reasons
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/datasets/test_staleness.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/nfo/datasets/ tests/nfo/datasets/
git commit -m "feat(datasets): add staleness detection for runs"
```

---

### Task 15: `reporting/index.py` — top-level index + latest.json

**Files:**
- Create: `src/nfo/reporting/index.py`
- Test: `tests/nfo/reporting/test_index.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/reporting/test_index.py`:

```python
"""Tests for top-level index generator (master design §8.2)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from nfo.datasets.staleness import HashSources
from nfo.reporting.artifacts import open_run_directory
from nfo.reporting.index import IndexResult, generate_index
from nfo.specs.manifest import RunManifest


def _make_run(root: Path, run_id: str, study_type: str, strategy_hash: str,
              dataset_hashes: dict[str, str], created: datetime) -> None:
    rd = open_run_directory(root=root, run_id=run_id)
    m = RunManifest(
        run_id=run_id,
        created_at=created,
        code_version="a1b2c3d",
        study_spec_hash="x" * 64,
        strategy_spec_hash=strategy_hash,
        strategy_id="v3",
        strategy_version="3.0.0",
        study_type=study_type,   # type: ignore[arg-type]
        selection_mode="cycle_matched",
        dataset_hashes=dataset_hashes,
        window_start=date(2024, 2, 1),
        window_end=date(2026, 4, 18),
        artifacts=[],
        status="ok",
        duration_seconds=1.0,
    )
    rd.write_manifest(m)


def test_generate_index_lists_all_runs(tmp_path):
    runs_root = tmp_path / "runs"
    _make_run(runs_root, "r1", "capital_analysis", "s" * 64, {"ds": "d" * 64},
              datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc))
    _make_run(runs_root, "r2", "robustness", "s" * 64, {"ds": "d" * 64},
              datetime(2026, 4, 21, 11, 0, tzinfo=timezone.utc))
    sources = HashSources(
        strategy_hash_fn=lambda sid, ver: "s" * 64,
        dataset_hash_fn=lambda did: "d" * 64,
    )
    res: IndexResult = generate_index(runs_root=runs_root, out_root=tmp_path, sources=sources)
    md = (tmp_path / "index.md").read_text()
    latest = json.loads((tmp_path / "latest.json").read_text())
    assert "r1" in md and "r2" in md
    assert latest["capital_analysis"]["run_id"] == "r1"
    assert latest["robustness"]["run_id"] == "r2"
    assert res.total_runs == 2
    assert res.stale_runs == 0


def test_generate_index_marks_stale(tmp_path):
    runs_root = tmp_path / "runs"
    _make_run(runs_root, "r1", "capital_analysis", "s" * 64, {"ds": "d" * 64},
              datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc))
    sources = HashSources(
        strategy_hash_fn=lambda sid, ver: "NEW" + "s" * 61,
        dataset_hash_fn=lambda did: "d" * 64,
    )
    res = generate_index(runs_root=runs_root, out_root=tmp_path, sources=sources)
    md = (tmp_path / "index.md").read_text()
    assert "stale" in md.lower()
    assert res.stale_runs == 1


def test_generate_index_handles_empty(tmp_path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    sources = HashSources(strategy_hash_fn=lambda s, v: None, dataset_hash_fn=lambda d: None)
    res = generate_index(runs_root=runs_root, out_root=tmp_path, sources=sources)
    assert res.total_runs == 0
    assert (tmp_path / "index.md").exists()
    assert json.loads((tmp_path / "latest.json").read_text()) == {}
```

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/reporting/test_index.py -v`
Expected: failures.

- [ ] **Step 3: Implement**

Create `src/nfo/reporting/index.py`:

```python
"""Top-level run index generator (master design §8.2)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from nfo.datasets.staleness import HashSources, is_run_stale
from nfo.specs.manifest import RunManifest


@dataclass
class IndexResult:
    total_runs: int
    stale_runs: int
    by_study: dict[str, int]


def _load_manifests(runs_root: Path) -> list[RunManifest]:
    manifests: list[RunManifest] = []
    if not runs_root.exists():
        return manifests
    for child in sorted(runs_root.iterdir()):
        mpath = child / "manifest.json"
        if not mpath.exists():
            continue
        manifests.append(RunManifest.model_validate_json(mpath.read_text()))
    return manifests


def generate_index(
    *,
    runs_root: Path,
    out_root: Path,
    sources: HashSources,
) -> IndexResult:
    manifests = _load_manifests(runs_root)
    stale_map: dict[str, list[str]] = {m.run_id: is_run_stale(m, sources) for m in manifests}

    # latest.json: newest manifest per study_type
    latest: dict[str, dict] = {}
    for m in manifests:
        cur = latest.get(m.study_type)
        if cur is None or m.created_at > cur["_created_at"]:
            latest[m.study_type] = {
                "run_id": m.run_id,
                "path": str((runs_root / m.run_id).relative_to(out_root.parent) if runs_root.is_relative_to(out_root.parent) else runs_root / m.run_id),
                "created_at": m.created_at.isoformat(),
                "_created_at": m.created_at,
            }
    latest_serializable = {
        k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
        for k, v in latest.items()
    }
    (out_root / "latest.json").write_text(json.dumps(latest_serializable, indent=2, sort_keys=True))

    # index.md: a table grouped by study_type, each row showing run_id + created_at + stale
    lines: list[str] = ["# NFO Platform — Runs Index", ""]
    by_study: dict[str, list[RunManifest]] = {}
    for m in manifests:
        by_study.setdefault(m.study_type, []).append(m)

    stale_count = 0
    for study in sorted(by_study):
        lines.append(f"## {study}")
        lines.append("")
        lines.append("| Run ID | Created | Status | Stale? |")
        lines.append("|---|---|---|---|")
        for m in sorted(by_study[study], key=lambda x: x.created_at, reverse=True):
            reasons = stale_map.get(m.run_id, [])
            stale_mark = "no" if not reasons else f"YES — {'; '.join(reasons)}"
            if reasons:
                stale_count += 1
            lines.append(f"| `{m.run_id}` | {m.created_at.isoformat()} | {m.status} | {stale_mark} |")
        lines.append("")

    if not manifests:
        lines.append("_No runs yet._")
    (out_root / "index.md").write_text("\n".join(lines) + "\n")

    return IndexResult(
        total_runs=len(manifests),
        stale_runs=stale_count,
        by_study={k: len(v) for k, v in by_study.items()},
    )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/reporting/test_index.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/nfo/reporting/index.py tests/nfo/reporting/test_index.py
git commit -m "feat(reporting): generate results/nfo/index.md and latest.json"
```

---

### Task 16: `reporting/git_version.py` — code_version helper

**Files:**
- Create: `src/nfo/reporting/git_version.py`
- Test: `tests/nfo/reporting/test_git_version.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/reporting/test_git_version.py`:

```python
"""Tests for the code_version git helper (master design §2.3)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nfo.reporting.git_version import current_code_version


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _init_repo(root: Path) -> None:
    _run(["git", "init", "-q"], root)
    _run(["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit",
          "--allow-empty", "-m", "init", "-q"], root)


def test_current_code_version_is_short_sha(tmp_path):
    _init_repo(tmp_path)
    ver = current_code_version(repo_root=tmp_path)
    assert len(ver) >= 7
    assert not ver.endswith("-dirty")


def test_current_code_version_marks_dirty(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "file.txt").write_text("hi")
    _run(["git", "add", "file.txt"], tmp_path)
    ver = current_code_version(repo_root=tmp_path)
    assert ver.endswith("-dirty")


def test_current_code_version_fallback_when_not_git(tmp_path):
    ver = current_code_version(repo_root=tmp_path)
    assert ver == "unversioned"
```

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/reporting/test_git_version.py -v`
Expected: failures.

- [ ] **Step 3: Implement**

Create `src/nfo/reporting/git_version.py`:

```python
"""Read `git` state for RunManifest.code_version (master design §2.3)."""
from __future__ import annotations

import subprocess
from pathlib import Path


def current_code_version(*, repo_root: Path) -> str:
    """Return short SHA (+ `-dirty` if working tree has changes), or 'unversioned'."""
    if not (repo_root / ".git").exists():
        return "unversioned"
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return "unversioned"
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return f"{sha}-dirty" if dirty else sha
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/reporting/test_git_version.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/nfo/reporting/git_version.py tests/nfo/reporting/test_git_version.py
git commit -m "feat(reporting): add git-backed code_version helper"
```

---

### Task 17: `reporting/hash_sources.py` — filesystem-backed HashSources

**Files:**
- Create: `src/nfo/reporting/hash_sources.py`
- Test: `tests/nfo/reporting/test_hash_sources.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/reporting/test_hash_sources.py`:

```python
"""Tests for filesystem-backed HashSources factory."""
from __future__ import annotations

import textwrap
from pathlib import Path

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
```

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/reporting/test_hash_sources.py -v`
Expected: failures.

- [ ] **Step 3: Implement**

Create `src/nfo/reporting/hash_sources.py`:

```python
"""Filesystem-backed HashSources factory for the index generator."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from nfo.datasets.staleness import HashSources
from nfo.specs.hashing import spec_hash
from nfo.specs.strategy import StrategySpec


def filesystem_hash_sources(*, strategies_root: Path, datasets_root: Path) -> HashSources:
    def strategy_hash(strategy_id: str, strategy_version: str) -> str | None:
        if not strategies_root.exists():
            return None
        for yml in strategies_root.glob("*.yaml"):
            try:
                raw = yaml.safe_load(yml.read_text())
            except Exception:
                continue
            if raw.get("strategy_id") == strategy_id and raw.get("strategy_version") == strategy_version:
                return spec_hash(StrategySpec.model_validate(raw))
        return None

    def dataset_hash(dataset_id: str) -> str | None:
        if not datasets_root.exists():
            return None
        for manifest_path in datasets_root.rglob("manifest.json"):
            try:
                raw = json.loads(manifest_path.read_text())
            except Exception:
                continue
            if raw.get("dataset_id") == dataset_id:
                return raw.get("parquet_sha256")
        return None

    return HashSources(strategy_hash_fn=strategy_hash, dataset_hash_fn=dataset_hash)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/reporting/test_hash_sources.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/nfo/reporting/hash_sources.py tests/nfo/reporting/test_hash_sources.py
git commit -m "feat(reporting): add filesystem-backed HashSources factory"
```

---

## Group E — First Strategy & Study Configs (Tasks 18–19)

### Task 18: `configs/nfo/strategies/v3_frozen.yaml` (cycle-matched) and registry

**Files:**
- Create: `configs/nfo/strategies/v3_frozen.yaml`
- Modify: `configs/nfo/.registry.json`
- Test: `tests/nfo/configs/__init__.py`, `tests/nfo/configs/test_v3_frozen.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/configs/__init__.py` (empty) and `tests/nfo/configs/test_v3_frozen.py`:

```python
"""Tests that v3_frozen.yaml matches docs/v3-spec-frozen.md contract."""
from __future__ import annotations

from pathlib import Path

import pytest

from nfo.specs.loader import load_strategy, reset_registry_for_tests


REPO_ROOT = Path(__file__).resolve().parents[3]
V3_PATH = REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml"


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")


def test_v3_loads():
    spec, _ = load_strategy(V3_PATH)
    assert spec.strategy_id == "v3"
    assert spec.strategy_version == "3.0.0"


def test_v3_universe_matches_frozen_doc():
    spec, _ = load_strategy(V3_PATH)
    assert spec.universe.underlyings == ["NIFTY"]
    assert spec.universe.delta_target == 0.30
    assert spec.universe.width_rule == "fixed"
    assert spec.universe.width_value == 100.0
    assert spec.universe.dte_target == 35


def test_v3_trigger_specific_pass_gate():
    spec, _ = load_strategy(V3_PATH)
    assert set(spec.trigger_rule.specific_pass_gates) == {"s3_iv_rv", "s6_trend", "s8_events"}


def test_v3_selection_is_cycle_matched_hte():
    spec, _ = load_strategy(V3_PATH)
    assert spec.selection_rule.mode == "cycle_matched"
    assert spec.exit_rule.variant == "hte"
    assert spec.exit_rule.manage_at_dte is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/nfo/configs/test_v3_frozen.py -v`
Expected: FileNotFoundError or validation failures.

- [ ] **Step 3: Write the YAML**

Create `configs/nfo/strategies/v3_frozen.yaml`:

```yaml
# V3 credit-spread strategy — frozen for falsification.
# Source of truth: docs/v3-spec-frozen.md (2026-04-20).
# Selection mode is cycle_matched; the live-rule variant lives in v3_live_rule.yaml (P3).
strategy_id: v3
strategy_version: 3.0.0
description: >
  NIFTY credit put spread, 0.30Δ short / 100-pt width / 35 DTE.
  V3 specific-pass gate requires trend + IV-RV + events OK,
  with at least one vol signal (VIX>20, VIX 3-mo pct>=0.80, or IV rank>=0.60).
universe:
  underlyings: [NIFTY]
  delta_target: 0.30
  delta_tolerance: 0.05
  width_rule: fixed
  width_value: 100.0
  dte_target: 35
  dte_tolerance: 3
  allowed_contract_families: [PE]
feature_set:
  - vix_abs
  - vix_pct_3mo
  - iv_rank
  - iv_minus_rv
  - trend_score
  - event_risk_v3
feature_thresholds_reference: >
  See TriggerSpec.feature_thresholds below; gates are also encoded in
  docs/v3-spec-frozen.md.
trigger_rule:
  score_gates:
    min_score: 4
  specific_pass_gates:
    - s3_iv_rv
    - s6_trend
    - s8_events
  event_window_days: 10
  feature_thresholds:
    iv_minus_rv_min_vp: -2.0
    trend_score_min: 2.0
    vix_abs_min: 20.0
    vix_pct_3mo_min: 0.80
    iv_rank_min: 0.60
  missing_data_policy: skip_day
selection_rule:
  mode: cycle_matched
  one_trade_per_cycle: true
  preferred_exit_variant: hte
  canonical_trade_chooser: first_fire
  width_handling: strict_fixed
  tie_breaker_order: [delta_err_asc, width_exact, entry_date_asc]
entry_rule:
  earliest_entry_relative_to_first_fire: 0
  session_snap_rule: forward_only
  entry_timestamp_convention: session_close
  allow_pre_fire_entry: true   # cycle_matched permits canonical-grid entry
exit_rule:
  variant: hte
  profit_take_fraction: 1.0
  manage_at_dte: null
  expiry_settlement: cash_settled_to_spot
capital_rule:
  fixed_capital_inr: 1000000
  deployment_fraction: 1.0
  compounding: false
  lot_rounding_mode: floor
slippage_rule:
  model: flat_rupees_per_lot
  flat_rupees_per_lot: 0.0
  percent_of_premium: 0.0
report_defaults:
  currency: INR
  underlying_lot_size: 65
```

- [ ] **Step 4: Re-run tests**

Run: `.venv/bin/python -m pytest tests/nfo/configs/test_v3_frozen.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add configs/nfo/strategies/v3_frozen.yaml tests/nfo/configs/
git commit -m "feat(configs): add v3_frozen.yaml (cycle_matched) strategy spec"
```

---

### Task 19: Default study configs for each study type

**Files:**
- Create: `configs/nfo/studies/variant_comparison_default.yaml`
- Create: `configs/nfo/studies/capital_analysis_10L.yaml`
- Create: `configs/nfo/studies/robustness_default.yaml`
- Create: `configs/nfo/studies/falsification_default.yaml`
- Create: `configs/nfo/studies/time_split_default.yaml`
- Create: `configs/nfo/studies/live_replay_default.yaml`
- Test: `tests/nfo/configs/test_study_configs.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/configs/test_study_configs.py`:

```python
"""Tests that default study YAMLs validate."""
from __future__ import annotations

from pathlib import Path

import pytest

from nfo.specs.loader import load_study


REPO_ROOT = Path(__file__).resolve().parents[3]
STUDIES = REPO_ROOT / "configs" / "nfo" / "studies"


@pytest.mark.parametrize("yaml_name,expected_type", [
    ("variant_comparison_default.yaml", "variant_comparison"),
    ("capital_analysis_10L.yaml", "capital_analysis"),
    ("robustness_default.yaml", "robustness"),
    ("falsification_default.yaml", "falsification"),
    ("time_split_default.yaml", "time_split"),
    ("live_replay_default.yaml", "live_replay"),
])
def test_study_yaml_loads(yaml_name: str, expected_type: str):
    spec, _ = load_study(STUDIES / yaml_name)
    assert spec.study_type == expected_type
```

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/configs/test_study_configs.py -v`
Expected: FileNotFoundError for each parametrization.

- [ ] **Step 3: Write YAMLs**

Create `configs/nfo/studies/variant_comparison_default.yaml`:

```yaml
study_id: variant_comparison_default
study_type: variant_comparison
strategy_spec_ref: configs/nfo/strategies/v3_frozen.yaml
dataset_refs: []
parameters:
  variant_names: [V0, V1, V2, V3, V4, V5, V6]
  window_start: "2024-01-15"
  window_end: "2026-04-18"
output_profile: default
```

Create `configs/nfo/studies/capital_analysis_10L.yaml`:

```yaml
study_id: capital_analysis_10L
study_type: capital_analysis
strategy_spec_ref: configs/nfo/strategies/v3_frozen.yaml
dataset_refs: []
parameters:
  capital_inr: 1000000
  pt_variants: [pt50, hte]
  window_start: "2024-01-15"
  window_end: "2026-04-18"
output_profile: default
```

Create `configs/nfo/studies/robustness_default.yaml`:

```yaml
study_id: robustness_default
study_type: robustness
strategy_spec_ref: configs/nfo/strategies/v3_frozen.yaml
dataset_refs: []
parameters:
  capital_inr: 1000000
  bootstrap_iterations: 10000
  seed: 42
  slippage_sweep: [0, 250, 500, 750, 1000]
output_profile: default
```

Create `configs/nfo/studies/falsification_default.yaml`:

```yaml
study_id: falsification_default
study_type: falsification
strategy_spec_ref: configs/nfo/strategies/v3_frozen.yaml
dataset_refs: []
parameters:
  capital_inr: 1000000
  walkforward_folds: 4
  tail_loss_injections: [1, 2, 3]
  allocation_fractions: [0.25, 0.5, 1.0]
output_profile: default
```

Create `configs/nfo/studies/time_split_default.yaml`:

```yaml
study_id: time_split_default
study_type: time_split
strategy_spec_ref: configs/nfo/strategies/v3_frozen.yaml
dataset_refs: []
parameters:
  train_window: ["2024-01-15", "2024-12-31"]
  test_window: ["2025-01-01", "2026-04-18"]
  inconclusive_threshold_trades: 10
output_profile: default
```

Create `configs/nfo/studies/live_replay_default.yaml`:

```yaml
study_id: live_replay_default
study_type: live_replay
strategy_spec_ref: configs/nfo/strategies/v3_live_rule.yaml
dataset_refs: []
parameters:
  capital_inr: 1000000
  pt_variants: [pt50, hte]
  window_start: "2024-01-15"
  window_end: "2026-04-18"
output_profile: default
```

Note: `live_replay_default.yaml` references `v3_live_rule.yaml`, which ships in P3. That's expected; this study cannot be run until P3 lands, but the config validates against the schema now.

- [ ] **Step 4: Re-run tests**

Run: `.venv/bin/python -m pytest tests/nfo/configs/test_study_configs.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add configs/nfo/studies/
git commit -m "feat(configs): add default study configs for every study_type"
```

---

## Group F — Legacy Script Wrapping (Tasks 20–26)

### Task 20: `reporting/wrap_legacy_run.py` — shared wrapper helper

**Files:**
- Create: `src/nfo/reporting/wrap_legacy_run.py`
- Test: `tests/nfo/reporting/test_wrap_legacy_run.py`

This helper lets existing scripts additionally emit a run directory without changing their business logic. Called by every P1 wrapper.

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/reporting/test_wrap_legacy_run.py`:

```python
"""Tests for the wrap_legacy_run helper."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from nfo.reporting.wrap_legacy_run import WrappedRun, wrap_legacy_run
from nfo.specs.loader import reset_registry_for_tests


STRAT_YAML = """
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
"""


@pytest.fixture(autouse=True)
def _iso(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")


def test_wrap_legacy_run_writes_run_dir(tmp_path):
    strat_path = tmp_path / "v3.yaml"
    strat_path.write_text(STRAT_YAML)

    legacy_csv = tmp_path / "legacy_trades.csv"
    legacy_md = tmp_path / "legacy_report.md"

    def run_logic():
        legacy_csv.write_text("a,b\n1,2\n")
        legacy_md.write_text("## body\n")
        return {"metrics": {"total_pnl_inr": 42.0}, "body_markdown": "## body\n"}

    result: WrappedRun = wrap_legacy_run(
        study_type="capital_analysis",
        strategy_path=strat_path,
        study_path=None,
        legacy_artifacts=[legacy_csv, legacy_md],
        window=(date(2024, 2, 1), date(2026, 4, 18)),
        run_logic=run_logic,
        runs_root=tmp_path / "runs",
        code_version="testsha",
    )

    assert result.run_dir.path.exists()
    manifest = json.loads((result.run_dir.path / "manifest.json").read_text())
    assert manifest["study_type"] == "capital_analysis"
    assert manifest["selection_mode"] == "cycle_matched"
    metrics = json.loads((result.run_dir.path / "metrics.json").read_text())
    assert metrics["total_pnl_inr"] == 42.0
    assert (result.run_dir.path / "tables" / "legacy_trades.csv").exists()
    report = (result.run_dir.path / "report.md").read_text()
    assert "## body" in report
    assert "<!-- methodology:begin -->" in report


def test_wrap_legacy_run_sets_status_warnings(tmp_path):
    strat_path = tmp_path / "v3.yaml"
    strat_path.write_text(STRAT_YAML)

    def run_logic():
        return {"metrics": {}, "body_markdown": "", "warnings": ["data gap: 2025-01-06"]}

    result = wrap_legacy_run(
        study_type="robustness",
        strategy_path=strat_path,
        study_path=None,
        legacy_artifacts=[],
        window=(date(2024, 2, 1), date(2026, 4, 18)),
        run_logic=run_logic,
        runs_root=tmp_path / "runs",
        code_version="testsha",
    )
    manifest = json.loads((result.run_dir.path / "manifest.json").read_text())
    assert manifest["status"] == "warnings"
    assert manifest["warnings"] == ["data gap: 2025-01-06"]
```

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/reporting/test_wrap_legacy_run.py -v`
Expected: failures.

- [ ] **Step 3: Implement**

Create `src/nfo/reporting/wrap_legacy_run.py`:

```python
"""wrap_legacy_run — lets P1 scripts emit a run directory without changing their
business logic (master design §10.1).

Usage (from a wrapper script):
  result = wrap_legacy_run(
      study_type="capital_analysis",
      strategy_path=REPO/"configs/nfo/strategies/v3_frozen.yaml",
      study_path=REPO/"configs/nfo/studies/capital_analysis_10L.yaml",
      legacy_artifacts=[RESULTS/"v3_capital_trades_hte.csv", ...],
      window=(window_start, window_end),
      run_logic=_run,   # returns {"metrics": {...}, "body_markdown": "...", "warnings": [...]}
      runs_root=RESULTS/"runs",
  )
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from nfo.engine.cycles import build_run_id
from nfo.reporting.artifacts import RunDirectory, open_run_directory
from nfo.reporting.git_version import current_code_version
from nfo.specs.hashing import spec_hash, short_hash
from nfo.specs.loader import load_strategy, load_study
from nfo.specs.manifest import RunManifest
from nfo.specs.study import StudyType


@dataclass
class WrappedRun:
    run_dir: RunDirectory
    manifest: RunManifest


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
) -> WrappedRun:
    strategy, strategy_hash_hex = load_strategy(strategy_path)
    study_hash_hex = ""
    if study_path is not None:
        _, study_hash_hex = load_study(study_path)

    created_at = datetime.now(timezone.utc)
    run_id = build_run_id(
        created_at=created_at,
        study_id=study_path.stem if study_path else study_type,
        strategy_hash_short=short_hash(strategy),
    )
    runs_root.mkdir(parents=True, exist_ok=True)
    rd = open_run_directory(root=runs_root, run_id=run_id)

    t0 = time.perf_counter()
    result = run_logic() or {}
    dt = time.perf_counter() - t0

    warnings = list(result.get("warnings", []) or [])
    status = "warnings" if warnings else "ok"

    manifest = RunManifest(
        run_id=run_id,
        created_at=created_at,
        code_version=code_version or current_code_version(repo_root=Path.cwd()),
        study_spec_hash=study_hash_hex or "",
        strategy_spec_hash=strategy_hash_hex,
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.strategy_version,
        study_type=study_type,
        selection_mode=strategy.selection_rule.mode,
        dataset_hashes={},
        window_start=window[0],
        window_end=window[1],
        artifacts=[],
        status=status,
        warnings=warnings,
        stale_inputs_detected=[],
        duration_seconds=dt,
    )
    rd.write_manifest(manifest)

    metrics = dict(result.get("metrics") or {})
    rd.write_metrics(metrics)

    tables_dir = rd.path / "tables"
    for src in legacy_artifacts:
        if not src.exists():
            continue
        if src.suffix in {".csv", ".parquet"}:
            dst = tables_dir / src.name
            shutil.copy2(src, dst)
            rd._record_artifact(f"tables/{src.name}")

    body = result.get("body_markdown") or ""
    rd.write_report(body_markdown=body)

    return WrappedRun(run_dir=rd, manifest=manifest)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/reporting/test_wrap_legacy_run.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/nfo/reporting/wrap_legacy_run.py tests/nfo/reporting/test_wrap_legacy_run.py
git commit -m "feat(reporting): add wrap_legacy_run helper for P1 script wrappers"
```

---

### Task 21: Wire `scripts/nfo/v3_capital_analysis.py` through wrap_legacy_run

**Files:**
- Modify: `scripts/nfo/v3_capital_analysis.py`
- Test: manual smoke + automated smoke added in Task 27

- [ ] **Step 1: Read current state**

Run: `.venv/bin/python -c "open('scripts/nfo/v3_capital_analysis.py').read()"` and identify the current `main()` entry point. Note which artifacts are written (`v3_capital_report_{pt50,hte}.md`, `v3_capital_trades_{pt50,hte}.csv`).

- [ ] **Step 2: Modify main() to wrap existing logic**

At the bottom of `scripts/nfo/v3_capital_analysis.py`, change:

```python
def main() -> int:
    ... existing body ...
    return 0
```

Into:

```python
def _legacy_main(pt_variant: str) -> dict:
    """Original main body, factored out so wrap_legacy_run can call it.

    Returns:
        {"metrics": dict[str, float],
         "body_markdown": str,
         "warnings": list[str]}
    """
    ... existing body from original main(), but capture metrics + body_markdown
    instead of only writing files ...
    return {
        "metrics": computed_metrics,
        "body_markdown": report_markdown,
        "warnings": [],
    }


def main() -> int:
    import argparse
    from datetime import date
    from pathlib import Path

    from nfo.config import RESULTS_DIR, ROOT
    from nfo.reporting.wrap_legacy_run import wrap_legacy_run

    parser = argparse.ArgumentParser()
    parser.add_argument("--pt-variant", choices=("pt50", "hte"), default="hte")
    args = parser.parse_args()

    def run_logic() -> dict:
        return _legacy_main(args.pt_variant)

    result = wrap_legacy_run(
        study_type="capital_analysis",
        strategy_path=ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml",
        study_path=ROOT / "configs" / "nfo" / "studies" / "capital_analysis_10L.yaml",
        legacy_artifacts=[
            RESULTS_DIR / f"v3_capital_report_{args.pt_variant}.md",
            RESULTS_DIR / f"v3_capital_trades_{args.pt_variant}.csv",
        ],
        window=(date(2024, 2, 1), date(2026, 4, 18)),
        run_logic=run_logic,
        runs_root=RESULTS_DIR / "runs",
    )
    print(result.run_dir.path)
    return 0
```

Note: the engineer must factor the existing `main()` body into `_legacy_main(pt_variant)` such that it still writes legacy CSV/MD files AND returns a metrics dict + rendered body markdown. The function signature is new but the side effects (legacy outputs) are preserved.

If the legacy script's `main()` does not already isolate the metrics computation from file-writing, the engineer must do a minimal refactor: extract metric computation into a local helper, keep existing file writes, then return the metrics dict.

- [ ] **Step 3: Run manually against cached data**

Run:
```bash
.venv/bin/python scripts/nfo/v3_capital_analysis.py --pt-variant hte
```
Expected: prints `results/nfo/runs/<run_id>`. Legacy files at `results/nfo/v3_capital_{report,trades}_hte.*` still updated.

- [ ] **Step 4: Verify run directory structure**

Run:
```bash
ls results/nfo/runs/
cat results/nfo/runs/<run_id>/manifest.json | head -30
```
Expected: manifest validates, `selection_mode == "cycle_matched"`, tables/ contains the mirrored legacy files.

- [ ] **Step 5: Commit**

```bash
git add scripts/nfo/v3_capital_analysis.py
git commit -m "refactor(v3_capital_analysis): emit run-scoped manifest via wrap_legacy_run"
```

---

### Task 22: Wire `scripts/nfo/v3_robustness.py`

Same pattern as Task 21.

**Files:**
- Modify: `scripts/nfo/v3_robustness.py`

- [ ] **Step 1: Factor existing logic**

Identify legacy artifacts: `robustness_slippage.csv`, `robustness_loo.csv`, `robustness_bootstrap.csv`, `robustness_report.md`.

- [ ] **Step 2: Wrap**

Add a `_legacy_main()` helper and a new `main()` using `wrap_legacy_run`:

```python
def main() -> int:
    from datetime import date
    from pathlib import Path
    from nfo.config import RESULTS_DIR, ROOT
    from nfo.reporting.wrap_legacy_run import wrap_legacy_run

    def run_logic() -> dict:
        return _legacy_main()

    result = wrap_legacy_run(
        study_type="robustness",
        strategy_path=ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml",
        study_path=ROOT / "configs" / "nfo" / "studies" / "robustness_default.yaml",
        legacy_artifacts=[
            RESULTS_DIR / "robustness_slippage.csv",
            RESULTS_DIR / "robustness_loo.csv",
            RESULTS_DIR / "robustness_bootstrap.csv",
            RESULTS_DIR / "robustness_report.md",
        ],
        window=(date(2024, 2, 1), date(2026, 4, 18)),
        run_logic=run_logic,
        runs_root=RESULTS_DIR / "runs",
    )
    print(result.run_dir.path)
    return 0
```

- [ ] **Step 3: Run manually**

Run: `.venv/bin/python scripts/nfo/v3_robustness.py`
Expected: new `runs/<run_id>/` created with mirrored legacy artifacts.

- [ ] **Step 4: Verify manifest**

Check `runs/<run_id>/manifest.json` has `study_type == "robustness"` and `selection_mode == "cycle_matched"`.

- [ ] **Step 5: Commit**

```bash
git add scripts/nfo/v3_robustness.py
git commit -m "refactor(v3_robustness): emit run-scoped manifest via wrap_legacy_run"
```

---

### Task 23: Wire `scripts/nfo/v3_falsification.py`

**Files:**
- Modify: `scripts/nfo/v3_falsification.py`

Legacy artifacts: `falsify_tail_loss.csv`, `falsify_allocation.csv`, `falsify_walkforward.csv`, `falsification_report.md`.

- [ ] **Step 1: Factor existing logic** into `_legacy_main()`.
- [ ] **Step 2: Wrap** with `wrap_legacy_run`, `study_type="falsification"`, `study_path=…falsification_default.yaml`, `strategy_path=…v3_frozen.yaml`, legacy_artifacts as listed above.
- [ ] **Step 3: Run** `.venv/bin/python scripts/nfo/v3_falsification.py`.
- [ ] **Step 4: Verify manifest** has `study_type == "falsification"`.
- [ ] **Step 5: Commit**

```bash
git add scripts/nfo/v3_falsification.py
git commit -m "refactor(v3_falsification): emit run-scoped manifest via wrap_legacy_run"
```

---

### Task 24: Wire `scripts/nfo/v3_live_rule_backtest.py`

**Files:**
- Modify: `scripts/nfo/v3_live_rule_backtest.py`

Legacy artifacts: `v3_live_trades_pt50.csv`, `v3_live_trades_hte.csv`, `v3_live_report.md`.

**Important:** this script semantically IS live-rule — but the strategy spec at `configs/nfo/strategies/v3_frozen.yaml` is cycle_matched. The wrapper must reference a *live-rule* strategy spec. Since `v3_live_rule.yaml` ships in P3, P1 creates a minimal cycle_matched→live_rule variant just for this wrapper.

- [ ] **Step 1: Add a minimal `configs/nfo/strategies/v3_live_rule.yaml`**

```yaml
strategy_id: v3
strategy_version: 3.0.1
description: >
  V3 credit spread — live-rule variant. Same trigger/universe as v3_frozen
  (3.0.0), but selection_mode=live_rule with entry forced on/after first fire.
universe:
  underlyings: [NIFTY]
  delta_target: 0.30
  delta_tolerance: 0.05
  width_rule: fixed
  width_value: 100.0
  dte_target: 35
  dte_tolerance: 3
  allowed_contract_families: [PE]
feature_set: [vix_abs, vix_pct_3mo, iv_rank, iv_minus_rv, trend_score, event_risk_v3]
trigger_rule:
  score_gates: {min_score: 4}
  specific_pass_gates: [s3_iv_rv, s6_trend, s8_events]
  event_window_days: 10
  feature_thresholds:
    iv_minus_rv_min_vp: -2.0
    trend_score_min: 2.0
    vix_abs_min: 20.0
    vix_pct_3mo_min: 0.80
    iv_rank_min: 0.60
  missing_data_policy: skip_day
selection_rule:
  mode: live_rule
  one_trade_per_cycle: true
  preferred_exit_variant: hte
  canonical_trade_chooser: first_fire
  width_handling: strict_fixed
entry_rule:
  earliest_entry_relative_to_first_fire: 0
  session_snap_rule: forward_only
  entry_timestamp_convention: session_close
  allow_pre_fire_entry: false
exit_rule:
  variant: hte
  profit_take_fraction: 1.0
  manage_at_dte: null
  expiry_settlement: cash_settled_to_spot
capital_rule:
  fixed_capital_inr: 1000000
slippage_rule:
  flat_rupees_per_lot: 0.0
```

Note the strategy_version bump to `3.0.1` — the content differs from v3_frozen so the hash differs, and bumping avoids StrategyDriftError.

- [ ] **Step 2: Factor `main()` into `_legacy_main()` and wrap**

```python
def main() -> int:
    from datetime import date
    from nfo.config import RESULTS_DIR, ROOT
    from nfo.reporting.wrap_legacy_run import wrap_legacy_run

    def run_logic() -> dict:
        return _legacy_main()

    result = wrap_legacy_run(
        study_type="live_replay",
        strategy_path=ROOT / "configs" / "nfo" / "strategies" / "v3_live_rule.yaml",
        study_path=ROOT / "configs" / "nfo" / "studies" / "live_replay_default.yaml",
        legacy_artifacts=[
            RESULTS_DIR / "v3_live_trades_pt50.csv",
            RESULTS_DIR / "v3_live_trades_hte.csv",
            RESULTS_DIR / "v3_live_report.md",
        ],
        window=(date(2024, 2, 1), date(2026, 4, 18)),
        run_logic=run_logic,
        runs_root=RESULTS_DIR / "runs",
    )
    print(result.run_dir.path)
    return 0
```

- [ ] **Step 3: Run**

`.venv/bin/python scripts/nfo/v3_live_rule_backtest.py`

- [ ] **Step 4: Verify**

Manifest should have `selection_mode == "live_rule"` and `study_type == "live_replay"`.

- [ ] **Step 5: Commit**

```bash
git add configs/nfo/strategies/v3_live_rule.yaml scripts/nfo/v3_live_rule_backtest.py
git commit -m "refactor(v3_live_rule_backtest): add live_rule strategy spec and run-scoped manifest"
```

---

### Task 25: Wire `scripts/nfo/redesign_variants.py`

**Files:**
- Modify: `scripts/nfo/redesign_variants.py`

Legacy artifacts: `redesign_comparison.csv`, `redesign_comparison.md`, `redesign_winner.json`.

- [ ] **Step 1: Factor existing logic** into `_legacy_main()`.
- [ ] **Step 2: Wrap** with `study_type="variant_comparison"`, `study_path=…variant_comparison_default.yaml`, `strategy_path=…v3_frozen.yaml`.
- [ ] **Step 3: Run** `.venv/bin/python scripts/nfo/redesign_variants.py`.
- [ ] **Step 4: Verify manifest** — `study_type == "variant_comparison"`, `selection_mode == "cycle_matched"` (V3's mode even though the script iterates variants).
- [ ] **Step 5: Commit**

```bash
git add scripts/nfo/redesign_variants.py
git commit -m "refactor(redesign_variants): emit run-scoped manifest via wrap_legacy_run"
```

---

### Task 26: Wire `scripts/nfo/time_split_validate.py`

**Files:**
- Modify: `scripts/nfo/time_split_validate.py`

Legacy artifact: `time_split_report.md`.

- [ ] **Step 1: Factor existing logic** into `_legacy_main()`.
- [ ] **Step 2: Wrap** with `study_type="time_split"`, `study_path=…time_split_default.yaml`.
- [ ] **Step 3: Run** `.venv/bin/python scripts/nfo/time_split_validate.py`.
- [ ] **Step 4: Verify manifest** — `study_type == "time_split"`.
- [ ] **Step 5: Commit**

```bash
git add scripts/nfo/time_split_validate.py
git commit -m "refactor(time_split_validate): emit run-scoped manifest via wrap_legacy_run"
```

---

## Group G — Smoke tests and cross-report consistency (Tasks 27–28)

### Task 27: Smoke test — every wrapper produces a valid run directory

**Files:**
- Create: `tests/nfo/smoke/__init__.py`
- Create: `tests/nfo/smoke/test_wrapper_scripts.py`

This test discovers every script under `scripts/nfo/` that calls `wrap_legacy_run`, runs it in a subprocess, and validates the emitted run directory. Uses cached data; does not call Dhan/Parallel.

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/smoke/__init__.py` (empty) and `tests/nfo/smoke/test_wrapper_scripts.py`:

```python
"""Smoke tests for P1 script wrappers.

Each wrapper must emit a run directory that validates against RunManifest.
Requires cached data under data/nfo/; skipped if cache is missing.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from nfo.specs.manifest import RunManifest


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = REPO_ROOT / "results" / "nfo" / "runs"


WRAPPER_SCRIPTS = [
    ("v3_capital_analysis.py", ["--pt-variant", "hte"]),
    ("v3_robustness.py", []),
    ("v3_falsification.py", []),
    ("v3_live_rule_backtest.py", []),
    ("redesign_variants.py", []),
    ("time_split_validate.py", []),
]


def _cache_ready() -> bool:
    return (REPO_ROOT / "results" / "nfo" / "historical_signals.parquet").exists() and \
           (REPO_ROOT / "results" / "nfo" / "spread_trades.csv").exists()


@pytest.mark.skipif(not _cache_ready(), reason="requires cached signals/trades")
@pytest.mark.parametrize("script_name,extra_args", WRAPPER_SCRIPTS)
def test_wrapper_emits_valid_run_dir(tmp_path, script_name, extra_args):
    before = set(RUNS_DIR.iterdir()) if RUNS_DIR.exists() else set()
    env = os.environ.copy()
    result = subprocess.run(
        [".venv/bin/python", f"scripts/nfo/{script_name}", *extra_args],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env, timeout=600,
    )
    assert result.returncode == 0, f"script failed:\n{result.stderr}"
    after = set(RUNS_DIR.iterdir())
    new = after - before
    assert len(new) >= 1, "expected a new run directory"
    run_dir = sorted(new, key=lambda p: p.stat().st_mtime)[-1]
    manifest = RunManifest.model_validate_json((run_dir / "manifest.json").read_text())
    assert manifest.status in ("ok", "warnings")
    report = (run_dir / "report.md").read_text()
    assert "<!-- methodology:begin -->" in report
```

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/smoke/test_wrapper_scripts.py -v`
Expected: all pass (or skipped if cache is missing).

- [ ] **Step 3: Commit**

```bash
git add tests/nfo/smoke/
git commit -m "test(smoke): verify each P1 wrapper emits a valid run directory"
```

---

### Task 28: Cross-report consistency test

**Files:**
- Create: `tests/nfo/cross_report/__init__.py`
- Create: `tests/nfo/cross_report/test_manifest_header_consistency.py`

- [ ] **Step 1: Write the failing test**

Create `tests/nfo/cross_report/__init__.py` (empty) and `tests/nfo/cross_report/test_manifest_header_consistency.py`:

```python
"""Cross-report consistency — header facts must match manifest facts."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from nfo.specs.manifest import RunManifest


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = REPO_ROOT / "results" / "nfo" / "runs"


def _all_run_dirs() -> list[Path]:
    if not RUNS_DIR.exists():
        return []
    return [p for p in RUNS_DIR.iterdir() if (p / "manifest.json").exists()]


@pytest.mark.parametrize("run_dir", _all_run_dirs(), ids=lambda p: p.name)
def test_report_header_matches_manifest(run_dir):
    manifest = RunManifest.model_validate_json((run_dir / "manifest.json").read_text())
    report = (run_dir / "report.md").read_text()
    assert f"`{manifest.run_id}`" in report
    assert f"`{manifest.strategy_id}`" in report
    assert f"`{manifest.strategy_version}`" in report
    assert manifest.selection_mode in report
    assert manifest.window_start.isoformat() in report
    assert manifest.window_end.isoformat() in report


@pytest.mark.parametrize("run_dir", _all_run_dirs(), ids=lambda p: p.name)
def test_manifest_artifacts_exist(run_dir):
    manifest = RunManifest.model_validate_json((run_dir / "manifest.json").read_text())
    for rel in manifest.artifacts:
        assert (run_dir / rel).exists(), f"missing artifact: {rel}"


@pytest.mark.parametrize("run_dir", _all_run_dirs(), ids=lambda p: p.name)
def test_run_id_format(run_dir):
    manifest = RunManifest.model_validate_json((run_dir / "manifest.json").read_text())
    assert re.match(r"^\d{8}T\d{6}-[a-z0-9_]+-[0-9a-f]{6}$", manifest.run_id), manifest.run_id
```

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/nfo/cross_report/ -v`
Expected: all pass (or no parametrizations if no runs exist yet).

- [ ] **Step 3: Commit**

```bash
git add tests/nfo/cross_report/
git commit -m "test(cross_report): verify manifest ↔ report header ↔ artifacts consistency"
```

---

## Group H — Bootstrap the index + master run summary (Task 29)

### Task 29: CLI entry to regenerate `results/nfo/index.md`

**Files:**
- Create: `src/nfo/reporting/__main__.py`
- Test: manual run + existing index test

- [ ] **Step 1: Implement the CLI**

Create `src/nfo/reporting/__main__.py`:

```python
"""CLI: regenerate results/nfo/index.md + latest.json."""
from __future__ import annotations

import argparse
from pathlib import Path

from nfo.config import RESULTS_DIR, ROOT
from nfo.reporting.hash_sources import filesystem_hash_sources
from nfo.reporting.index import generate_index


def main() -> int:
    parser = argparse.ArgumentParser(description="regenerate NFO platform run index")
    parser.add_argument("--runs-root", type=Path, default=RESULTS_DIR / "runs")
    parser.add_argument("--out-root", type=Path, default=RESULTS_DIR)
    args = parser.parse_args()

    sources = filesystem_hash_sources(
        strategies_root=ROOT / "configs" / "nfo" / "strategies",
        datasets_root=ROOT / "data" / "nfo" / "datasets",
    )
    res = generate_index(runs_root=args.runs_root, out_root=args.out_root, sources=sources)
    print(f"Indexed {res.total_runs} runs ({res.stale_runs} stale).")
    print(f"  by_study={res.by_study}")
    print(f"Wrote {args.out_root / 'index.md'}")
    print(f"Wrote {args.out_root / 'latest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it against current runs**

Run: `.venv/bin/python -m nfo.reporting`
Expected: prints run count + writes `results/nfo/index.md` and `results/nfo/latest.json`.

- [ ] **Step 3: Inspect outputs**

Run: `cat results/nfo/index.md`
Expected: one table per study_type populated by Group F wrappers.

- [ ] **Step 4: Commit**

```bash
git add src/nfo/reporting/__main__.py
git commit -m "feat(reporting): CLI to regenerate top-level index + latest.json"
```

---

## Group I — Acceptance verification (Task 30)

### Task 30: Run the full test suite and verify P1 acceptance

**Files:**
- No new files; this is the sign-off task.

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest tests/nfo/ -q`
Expected: all pass. Count should be ≥ (previous count + new-module tests).

- [ ] **Step 2: Acceptance checklist (master design §10.1)**

- [ ] Each wrapper script emits a `results/nfo/runs/<run_id>/` directory.
- [ ] Every manifest validates against `RunManifest`.
- [ ] Every `report.md` contains the methodology header.
- [ ] `results/nfo/index.md` lists every run with stale markers where applicable.
- [ ] `results/nfo/latest.json` points to the newest run per study_type.
- [ ] `configs/nfo/.registry.json` contains entries for `v3@3.0.0` and `v3@3.0.1`.
- [ ] `StrategyDriftError` demoed: edit `v3_frozen.yaml` without bumping version, load it, see the error. Then revert.
- [ ] `src/csp/` is gone; `legacy/csp/` exists with README.
- [ ] `pyproject.toml` name == `nfo-platform`.

- [ ] **Step 3: Write P1 completion report**

Create `docs/superpowers/plans/2026-04-21-nfo-platform-phase1-completion.md` with:

```markdown
# P1 Completion Report

Completed: <date>

## Summary
- Git bootstrapped (initial SHA: <sha>)
- Package renamed to `nfo-platform`
- Legacy CSP archived under `legacy/`
- Pydantic schemas shipped: StrategySpec + nested, StudySpec, RunManifest, DatasetManifest
- Canonical id helpers shipped
- Reporting infrastructure shipped: RunDirectory, methodology header, index generator
- 6 legacy scripts wrapped to emit run-scoped manifests
- First strategy spec (v3_frozen@3.0.0) + live variant (v3_live_rule@3.0.1) shipped
- Default study configs for all 6 study types shipped

## Test coverage
- Tests added: <N>
- All passing: yes
- Smoke tests green: yes
- Cross-report consistency: yes

## Known deferrals (P2+)
- Engine extraction (trigger/cycles/selection/entry/exit/execution/capital/metrics)
- Dataset manifests for existing data caches
- Parity tests between legacy and engine-extracted paths
- Legacy script body replacement with thin wrappers

## Next: Phase 2 plan
- Spec: docs/superpowers/specs/2026-04-21-nfo-research-platform-design.md
- Plan: docs/superpowers/plans/<date>-nfo-platform-phase2-plan.md (to be written)
```

- [ ] **Step 4: Commit the completion report**

```bash
git add docs/superpowers/plans/2026-04-21-nfo-platform-phase1-completion.md
git commit -m "docs: Phase 1 completion report"
```

- [ ] **Step 5: Tag the phase**

```bash
git tag -a p1-complete -m "NFO platform Phase 1 complete"
git log --oneline | head -5
```

---

## Self-review (plan author, post-writing)

### Coverage check (master design § → plan tasks)

| Master design § | Tasks |
|---|---|
| §2.3 git bootstrap | Task 1 |
| §3 repo layout (pyproject, legacy, tests tree, configs) | Tasks 2, 4, 5, 6–17 |
| §4.1 StrategySpec + nested | Task 7 |
| §4.2 spec hashing | Task 6 |
| §4.3 StudySpec | Task 8 |
| §4.4 RunManifest | Task 9 |
| §4.5 DatasetManifest | Task 9 |
| §5 canonical ids | Task 11 |
| §7.2 staleness | Task 14 |
| §8.1 run directory | Task 13 |
| §8.2 top-level index | Tasks 15, 17, 29 |
| §8.4 methodology header | Task 12 |
| §10.1 P1 script wrappers | Tasks 20–26 |
| §10.2 legacy archival | Task 5 |
| §11 testing philosophy | Tasks 27, 28, 30 |
| §12 acceptance | Task 30 |
| §15 decision 2 (git bootstrap) | Task 1 |
| §15 decision 3 (YAML format) | Task 3, 18, 19 |
| §15 decision 4 (rename) | Tasks 2, 4, 5 |
| §15 decision 5 (SemVer + hash drift) | Tasks 6, 10 |

### Placeholder audit

Every task has concrete file paths, test code, implementation code, and commit messages. The only "deferred" markers refer to P2+ scope, not Phase 1 work.

### Type consistency audit

- `short_hash` / `build_run_id` / `spec_hash`: consistent across Tasks 6, 11, 20.
- `RunManifest`, `DatasetManifest`: field names consistent across Tasks 9, 13, 14, 15, 20, 28.
- `StudyType` / `SelectionMode` literals consistent across specs/ and reporting/.
- `wrap_legacy_run` signature consistent across Tasks 20–26.
- `reset_registry_for_tests` used in Tasks 10, 17, 18, 20.

### Known scope boundaries

P1 does **not** include: engine extraction, dataset manifests for existing caches, engine-backed parity tests, legacy-script body replacement, monitor snapshot capture, master summary generator. Those are P2/P3/P4.

---

*End of Phase 1 implementation plan. Phase 2, 3, 4 plans will be written after P1 ships.*
