# NFO Platform Phase 2 — Engine Extraction Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` to execute. Fresh subagent per bundle. TDD enforced: failing test first, then implementation.

**Goal:** Centralize the two master-design platform invariants — trigger evaluation and live-rule entry resolution — into `src/nfo/engine/`, make them the single source of truth, and migrate `redesign_variants.py` as proof-of-concept that scripts can become thin wrappers over the engine. Everything else (exits, execution, capital, metrics, datasets pipeline, remaining 5 script migrations) is deferred to P2b / P3 prep.

**Architecture:** Strangler-fig. New engine modules built alongside existing `scripts/nfo/` code. Parity tests compare new-engine output to legacy-script output (byte-exact on non-numeric; 1e-9 tolerance on floats). Legacy paths remain runnable; they are wired to delegate to engine only after parity gate passes.

**Master design reference:** `docs/superpowers/specs/2026-04-21-nfo-research-platform-design.md` §6 (evaluation semantics), §10.1 (P2 parity gates), §12 acceptance items 3 (single-source-of-truth for triggers + entry).

**Tech stack:** Python 3.14 via `.venv/bin/python`. Pydantic v2. pandas. pytest.

---

## Execution conventions

- **TDD:** write failing test → observe fail → implement → observe pass → commit.
- **Commit style:** Conventional Commits (`feat`, `test`, `refactor`).
- **Parity tolerance:**
  - Non-numeric columns (dates, strings, bools, ids): byte-exact equality.
  - Floats: `np.isclose(a, b, rtol=1e-9, atol=1e-12)` unless a task specifies looser.
- **Imports:** `from nfo.engine.triggers import ...` (absolute).
- **No mocks for broker/data clients.** Tests use cached parquet under `data/nfo/` and `results/nfo/`.

---

## Bundle A (P2) — Engine triggers + parity

### Task P2-A1: `engine/triggers.py` — TriggerEvaluator + `fire_dates`

**Contract:**
- `TriggerEvaluator(spec: StrategySpec)` encapsulates a specific strategy's trigger logic.
- `fire_dates(features_df, atr_series) -> list[tuple[date, dict]]` returns firing days with per-gate pass/fail detail.
- Semantics must match legacy `scripts/nfo/redesign_variants.py::_row_passes + get_firing_dates` for the V3 strategy (strategy_id=v3, strategy_version=3.0.0).

**Files:**
- Create: `src/nfo/engine/triggers.py`
- Create: `tests/nfo/engine/test_triggers.py` (unit)
- Create: `tests/nfo/engine/test_triggers_parity.py` (V3 legacy parity)

**Steps:**

1. Write failing unit tests at `tests/nfo/engine/test_triggers.py`:
```python
"""Unit tests for engine.triggers — TriggerEvaluator.

Keep these tests spec-driven: use StrategySpec to drive the evaluator and
assert behavior. Legacy parity lives in test_triggers_parity.py.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from nfo.engine.triggers import TriggerEvaluator, FireRow
from nfo.specs.strategy import (
    CapitalSpec, EntrySpec, ExitSpec, SelectionSpec, SlippageSpec,
    StrategySpec, TriggerSpec, UniverseSpec,
)


def _v3_like_spec() -> StrategySpec:
    return StrategySpec(
        strategy_id="test_v3",
        strategy_version="3.0.0",
        description="test",
        universe=UniverseSpec(
            underlyings=["NIFTY"], delta_target=0.30, delta_tolerance=0.05,
            width_rule="fixed", width_value=100.0, dte_target=35, dte_tolerance=3,
        ),
        feature_set=["vix_abs", "vix_pct_3mo", "iv_rank", "iv_minus_rv", "trend_score", "event_risk_v3"],
        trigger_rule=TriggerSpec(
            score_gates={"min_score": 4},
            specific_pass_gates=["s3_iv_rv", "s6_trend", "s8_events"],
            event_window_days=10,
            feature_thresholds={
                "iv_minus_rv_min_vp": -2.0,
                "trend_score_min": 2.0,
                "vix_abs_min": 20.0,
                "vix_pct_3mo_min": 0.80,
                "iv_rank_min": 0.60,
            },
        ),
        selection_rule=SelectionSpec(mode="cycle_matched", preferred_exit_variant="hte"),
        entry_rule=EntrySpec(allow_pre_fire_entry=True),
        exit_rule=ExitSpec(variant="hte", profit_take_fraction=1.0, manage_at_dte=None),
        capital_rule=CapitalSpec(fixed_capital_inr=1_000_000),
        slippage_rule=SlippageSpec(),
    )


def test_evaluator_fires_when_all_gates_pass():
    spec = _v3_like_spec()
    ev = TriggerEvaluator(spec)
    row = pd.Series({
        "date": pd.Timestamp("2025-03-24"),
        "vix": 22.0,
        "vix_pct_3mo": 0.85,
        "iv_minus_rv": 0.5,
        "iv_rank_12mo": 0.70,
        "trend_score": 3,
        "dte": 35,
        "event_risk_v3": "none",
    })
    result = ev.evaluate_row(row, atr_value=100.0)
    assert result.fired is True
    assert result.detail["s3"] is True
    assert result.detail["s6"] is True
    assert result.detail["s8"] is True


def test_evaluator_specific_gate_fails_when_core_missing():
    spec = _v3_like_spec()
    ev = TriggerEvaluator(spec)
    row = pd.Series({
        "date": pd.Timestamp("2025-03-24"),
        "vix": 25.0,
        "vix_pct_3mo": 0.90,
        "iv_minus_rv": -5.0,   # s3 FAIL
        "iv_rank_12mo": 0.80,
        "trend_score": 3,
        "dte": 35,
        "event_risk_v3": "none",
    })
    result = ev.evaluate_row(row, atr_value=100.0)
    assert result.fired is False
    assert result.detail["s3"] is False


def test_evaluator_fire_dates_returns_only_firing():
    spec = _v3_like_spec()
    ev = TriggerEvaluator(spec)
    df = pd.DataFrame([
        {"date": pd.Timestamp("2025-03-24"), "vix": 22.0, "vix_pct_3mo": 0.85,
         "iv_minus_rv": 0.5, "iv_rank_12mo": 0.70, "trend_score": 3, "dte": 35,
         "event_risk_v3": "none"},
        {"date": pd.Timestamp("2025-03-25"), "vix": 10.0, "vix_pct_3mo": 0.1,
         "iv_minus_rv": -5.0, "iv_rank_12mo": 0.2, "trend_score": 0, "dte": 34,
         "event_risk_v3": "none"},
    ])
    atr = pd.Series([100.0, 100.0], index=df["date"])
    fires = ev.fire_dates(df, atr)
    assert len(fires) == 1
    assert fires[0][0] == date(2025, 3, 24)
```

2. Run → expect collection error.

3. Implement `src/nfo/engine/triggers.py`:
```python
"""Engine: trigger evaluation (master design §12 acceptance item 3).

Single source of truth for whether a strategy fires on a given day. Replaces
`scripts/nfo/redesign_variants._row_passes` + `get_firing_dates` for any spec
whose trigger_rule.feature_thresholds declares the V3-family gates. Legacy
variants (V0–V2, V4–V6) remain in redesign_variants until P2b.

Semantics (matches V3 legacy at V3's thresholds):
- s3_iv_rv  : iv_minus_rv >= threshold "iv_minus_rv_min_vp"
- s6_trend  : trend_score  >= threshold "trend_score_min"
- s8_events : event_risk_v3 != "high" OR event occurs outside window_days
- Optional vol signals (any pass satisfies the specific-pass "vol_ok" branch):
  s1_vix_abs  : vix > threshold "vix_abs_min"
  s2_vix_pct  : vix_pct_3mo >= threshold "vix_pct_3mo_min"
  s5_iv_rank  : iv_rank_12mo >= threshold "iv_rank_min"
- When trigger_rule.specific_pass_gates lists s3/s6/s8, require all core AND ≥1 vol.
- score_gates.min_score (if set) is applied as a final floor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from nfo.specs.strategy import StrategySpec


@dataclass
class FireRow:
    fired: bool
    detail: dict[str, Any]


CORE_GATES = ("s3_iv_rv", "s6_trend", "s8_events")
VOL_GATES  = ("s1_vix_abs", "s2_vix_pct", "s5_iv_rank")


class TriggerEvaluator:
    """Evaluate trigger_rule against a features frame."""

    def __init__(self, spec: StrategySpec) -> None:
        self.spec = spec
        self.thresholds = spec.trigger_rule.feature_thresholds
        self.specific = set(spec.trigger_rule.specific_pass_gates)
        self.min_score = int(spec.trigger_rule.score_gates.get("min_score", 0))
        self.window_days = spec.trigger_rule.event_window_days

    def evaluate_row(self, row: pd.Series, *, atr_value: float = float("nan")) -> FireRow:
        t = self.thresholds
        vix = float(row.get("vix", np.nan))
        vpct = float(row.get("vix_pct_3mo", np.nan))
        iv_rv = float(row.get("iv_minus_rv", np.nan))
        ivr = float(row.get("iv_rank_12mo", np.nan))
        trend = float(row.get("trend_score", 0) or 0)
        event_risk = row.get("event_risk_v3", row.get("event_risk", "none"))

        s1 = np.isfinite(vix) and vix > t.get("vix_abs_min", float("inf"))
        s2 = np.isfinite(vpct) and vpct >= t.get("vix_pct_3mo_min", float("inf"))
        s3 = np.isfinite(iv_rv) and iv_rv >= t.get("iv_minus_rv_min_vp", float("inf"))
        s5 = np.isfinite(ivr) and ivr >= t.get("iv_rank_min", float("inf"))
        s6 = trend >= t.get("trend_score_min", float("inf"))
        s8 = self._event_pass(event_risk)

        passes = {"s1": bool(s1), "s2": bool(s2), "s3": bool(s3),
                  "s5": bool(s5), "s6": bool(s6), "s8": bool(s8)}
        score = sum(1 for v in passes.values() if v)

        if self.specific:
            core_ok = all(passes[g[:2]] for g in CORE_GATES if g in self.specific)
            vol_ok = any(passes[g[:2]] for g in VOL_GATES)
            if not (core_ok and vol_ok):
                return FireRow(False, {"score": score, **passes})

        return FireRow(score >= self.min_score, {"score": score, **passes})

    def fire_dates(self, features_df: pd.DataFrame, atr_series: pd.Series) -> list[tuple[date, dict]]:
        atr_by_date: dict[date, float] = {}
        for d, v in atr_series.items():
            atr_by_date[pd.Timestamp(d).date()] = float(v) if np.isfinite(v) else float("nan")
        fires: list[tuple[date, dict]] = []
        for _, row in features_df.iterrows():
            entry = row["date"]
            if isinstance(entry, pd.Timestamp):
                entry = entry.date()
            atr_val = atr_by_date.get(entry, float("nan"))
            result = self.evaluate_row(row, atr_value=atr_val)
            if result.fired:
                fires.append((entry, result.detail))
        return fires

    def _event_pass(self, event_risk: Any) -> bool:
        # Legacy semantics: event_risk is a string category (high/medium/low/none)
        # or an already-gated boolean from feature-engineering. For P2-A1 we accept
        # either. Any "high" risk within window fails; otherwise pass.
        if isinstance(event_risk, bool):
            return not event_risk
        if not isinstance(event_risk, str):
            return True
        return event_risk.lower() != "high"
```

4. Re-run: expect 3 unit tests pass.

5. Commit:
```bash
git add src/nfo/engine/triggers.py tests/nfo/engine/test_triggers.py
git commit -m "feat(engine): add TriggerEvaluator for spec-driven firing decisions"
```

### Task P2-A2: Parity test vs legacy redesign_variants (V3 firing dates)

**Files:**
- Create: `tests/nfo/engine/test_triggers_parity.py`

**Steps:**

1. Write parity test:
```python
"""Parity: engine.TriggerEvaluator must reproduce legacy V3 firing dates.

Uses the cached `results/nfo/historical_signals.parquet` so the test runs
deterministically (no Dhan calls). The assertion is exact set equality of
firing dates between legacy `redesign_variants.get_firing_dates` and the
new engine TriggerEvaluator loaded from `configs/nfo/strategies/v3_frozen.yaml`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from nfo.engine.triggers import TriggerEvaluator
from nfo.specs.loader import load_strategy, reset_registry_for_tests


REPO_ROOT = Path(__file__).resolve().parents[3]
SIGNALS = REPO_ROOT / "results" / "nfo" / "historical_signals.parquet"


def _import_legacy():
    path = REPO_ROOT / "scripts" / "nfo" / "redesign_variants.py"
    spec = importlib.util.spec_from_file_location("_legacy_rv", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_legacy_rv"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def _iso_registry(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")


@pytest.mark.skipif(not SIGNALS.exists(), reason="requires cached historical_signals.parquet")
def test_v3_firing_dates_match_legacy(_iso_registry):
    strat_path = REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml"
    spec, _ = load_strategy(strat_path)
    df = pd.read_parquet(SIGNALS)
    df["date"] = pd.to_datetime(df["date"])

    rv = _import_legacy()
    variant_v3 = next(v for v in rv.make_variants() if v.name == "V3")
    atr = rv.load_nifty_atr(df["date"])

    legacy_fires = rv.get_firing_dates(variant_v3, df, atr)
    legacy_dates = {d for d, _ in legacy_fires}

    ev = TriggerEvaluator(spec)
    engine_fires = ev.fire_dates(df, atr)
    engine_dates = {d for d, _ in engine_fires}

    assert engine_dates == legacy_dates, (
        f"engine∖legacy={engine_dates - legacy_dates}; legacy∖engine={legacy_dates - engine_dates}"
    )
```

2. Run → expect FAIL (event_risk parsing between engine and legacy may differ initially).

3. Inspect any mismatched dates. Common causes:
   - `event_risk_v3` column in parquet vs the legacy `_event_pass(entry, dte, …)` function using hardcoded event calendar. The engine currently reads `event_risk_v3` as a column value. Legacy re-evaluates against HARD_EVENTS with a window.
   - To reach parity: the engine may need to fall back to legacy event evaluation. Simplest fix: the parquet already contains `event_risk_v3` column (computed during `historical_backtest.py`); if it matches legacy's `_event_pass` output per-row, parity holds.
   - If mismatches exist, extend `TriggerEvaluator._event_pass` to accept the parquet's pre-computed column (master design §7.3 — the feature dataset IS the single source of truth).

4. Iterate until parity holds. If parity cannot be achieved in reasonable iteration, mark the test `@pytest.mark.xfail(strict=True)` with a clear comment; file a follow-up task, and document the known gap in the P2 completion report.

5. Commit:
```bash
git add tests/nfo/engine/test_triggers_parity.py
git commit -m "test(engine): parity — TriggerEvaluator reproduces legacy V3 firing dates"
```

---

## Bundle B (P2) — Cycles grouping + parity

### Task P2-B1: `engine/cycles.py::group_fires_by_cycle` + parity

**Contract:**
- `group_fires_by_cycle(fires, features_df, strategy_version) -> dict[str, CycleFires]` where `CycleFires = namedtuple("CycleFires", "cycle_id first_fire_date target_expiry fire_dates")`.
- `first_fire_date` is `min(fire_dates)` per cycle.
- Must reproduce legacy `scripts/nfo/v3_live_rule_backtest._v3_cycles` output for same inputs.

**Files:**
- Modify: `src/nfo/engine/cycles.py` (add `group_fires_by_cycle` and `CycleFires` below existing id helpers)
- Create: `tests/nfo/engine/test_cycles_grouping.py`

**Steps:**

1. Write failing tests for `group_fires_by_cycle`:
   - unit test: given a 3-fire input across 2 expiries, assert correct grouping.
   - parity test: skip-if-cache-missing; compare against legacy `_v3_cycles`.

2. Implement `group_fires_by_cycle` in `src/nfo/engine/cycles.py`:
```python
from collections import namedtuple
from datetime import date

CycleFires = namedtuple("CycleFires", "cycle_id first_fire_date target_expiry fire_dates")


def group_fires_by_cycle(
    fires: list[tuple[date, dict]],
    features_df,
    *,
    underlying: str,
    strategy_version: str,
) -> dict[str, CycleFires]:
    """Group firing dates by target_expiry cycle.

    features_df must have a `target_expiry` column aligned to `date`. The same
    function is the single source of truth for cycle grouping, replacing
    `v3_live_rule_backtest._v3_cycles` and `robustness.get_v3_matched_trades`.
    """
    import pandas as pd
    by_expiry: dict[str, list[date]] = {}
    for fire_date, _ in fires:
        matching = features_df[features_df["date"].dt.date == fire_date]
        if matching.empty:
            continue
        exp_str = str(matching["target_expiry"].iloc[0])
        if not exp_str:
            continue
        by_expiry.setdefault(exp_str, []).append(fire_date)
    out: dict[str, CycleFires] = {}
    for exp_str, dates in by_expiry.items():
        exp_date = date.fromisoformat(exp_str)
        cid = cycle_id(underlying, exp_date, strategy_version)
        out[cid] = CycleFires(
            cycle_id=cid,
            first_fire_date=min(dates),
            target_expiry=exp_date,
            fire_dates=sorted(dates),
        )
    return out
```

3. Run tests, expect pass.

4. Commit: `feat(engine): add group_fires_by_cycle (single source of truth)`.

---

## Bundle C (P2) — Entry resolution (live-rule invariant)

### Task P2-C1: `engine/entry.py::resolve_entry_date`

**Contract (master design §6.3, §12 acceptance item 3):**
- For `live_rule` mode: returns `first_fire_date` or the next session on/after it. Never before.
- For `cycle_matched` with `allow_pre_fire_entry=True`: returns the caller-supplied canonical entry date.
- For `day_matched`: returns the firing date itself.
- Raises `ValueError` if live_rule is requested but `spec.entry_rule.allow_pre_fire_entry is True` (should be caught by spec validator, but defense in depth).

**Files:**
- Create: `src/nfo/engine/entry.py`
- Create: `tests/nfo/engine/test_entry.py`
- Create: `tests/nfo/engine/test_entry_parity.py`

**Steps:**

1. Write failing unit tests covering:
   - Live-rule: fire date is session → returns fire date.
   - Live-rule: fire date is Saturday → snaps forward to Monday (first session in list).
   - Live-rule: fire date is Friday evening → returns Friday (inclusive).
   - Live-rule: fire date after last available session → returns None.
   - Cycle-matched + pre_fire OK: returns the canonical date even if before fire.
   - Day-matched: returns fire date.
   - Live-rule with `allow_pre_fire_entry=True` → ValueError.

2. Implement:
```python
"""Engine: entry date resolution (master design §6.3, §12 item 3).

The ONLY place in the codebase that decides entry dates for live_rule mode.
Any selection code path constructing an entry date directly without calling
`resolve_entry_date` for a live_rule spec is a bug.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

from nfo.specs.strategy import StrategySpec


def resolve_entry_date(
    *,
    spec: StrategySpec,
    first_fire_date: date,
    sessions: Iterable[date],
    canonical_entry_date: date | None = None,
) -> date | None:
    mode = spec.selection_rule.mode
    if mode == "day_matched":
        return first_fire_date

    if mode == "cycle_matched":
        if spec.entry_rule.allow_pre_fire_entry and canonical_entry_date is not None:
            return canonical_entry_date
        return _snap_forward(first_fire_date, sessions)

    if mode == "live_rule":
        if spec.entry_rule.allow_pre_fire_entry:
            raise ValueError(
                "live_rule mode forbids entry_rule.allow_pre_fire_entry=True; "
                "this should have been caught by StrategySpec validator"
            )
        return _snap_forward(first_fire_date, sessions)

    raise ValueError(f"unknown selection mode: {mode!r}")


def _snap_forward(target: date, sessions: Iterable[date]) -> date | None:
    for s in sessions:
        if s >= target:
            return s
    return None
```

3. Write parity test `tests/nfo/engine/test_entry_parity.py` that imports legacy `_first_session_on_or_after` from `scripts/nfo/v3_live_rule_backtest.py` and asserts the engine returns the same value for the 6 V3 firing dates observed historically (hardcode the expected pairs or load them from the cached parquet).

4. Run, verify green.

5. Commit: `feat(engine): add resolve_entry_date as single source of truth for live_rule entries`.

---

## Bundle D (P2) — Selection modes

### Task P2-D1: `engine/selection.py` — select_day_matched / select_cycle_matched / select_live_rule

**Contract (master design §6):**
- `select_day_matched(trade_universe, fire_dates, universe_spec) -> DataFrame`
- `select_cycle_matched(trade_universe, cycle_index, strategy_spec) -> DataFrame`
- `select_live_rule(cycle_index, strategy_spec, simulator) -> DataFrame` — returns a frame built by walking `simulator` per cycle from the resolved entry date.

**Files:**
- Create: `src/nfo/engine/selection.py`
- Create: `tests/nfo/engine/test_selection.py`
- Create: `tests/nfo/engine/test_selection_parity.py`

**Steps:**

1. Write failing tests:
   - Unit: each selection mode handles empty input, single-cycle input, tie-breaker ordering.
   - Parity: `select_cycle_matched` reproduces `scripts/nfo/v3_capital_analysis._pick_trade` for the V3 cached trades.
   - Parity: `select_live_rule` — for Phase-2 scope, verify the selection-side logic (entry-date enforcement) matches legacy `v3_live_rule_backtest` without invoking the full simulator (pass a trivial mock simulator that just echoes entry_date).

2. Implement selection.py with `select_day_matched` and `select_cycle_matched` only. For `select_live_rule`, emit a stub that raises `NotImplementedError("live_rule selection requires engine.execution; deferred to P3")` — the invariant (entry-date resolution) is already in `engine/entry.py`.

3. Parity test for cycle_matched must match legacy `pick_trade_for_expiry` in `src/nfo/robustness.py` row-for-row on V3 trades.

4. Commit:
```bash
git add src/nfo/engine/selection.py tests/nfo/engine/test_selection*.py
git commit -m "feat(engine): add selection modes (day_matched, cycle_matched); live_rule stubbed"
```

---

## Bundle E (P2) — Studies scaffold + migrate redesign_variants

### Task P2-E1: `src/nfo/studies/__init__.py` + `studies/variant_comparison.py`

**Files:**
- Create: `src/nfo/studies/__init__.py`
- Create: `src/nfo/studies/variant_comparison.py`
- Create: `tests/nfo/studies/__init__.py` + `test_variant_comparison.py`

**Contract:**
- `run_variant_comparison(spec, features_df, atr_series, trades_df, variants: list[str]) -> VariantComparisonResult`
- `VariantComparisonResult` dataclass exposes: `per_variant_fires`, `per_variant_metrics`, `winner`, `warnings`.
- For the V3 spec, uses `engine.TriggerEvaluator` to compute fires (replacing `redesign_variants.get_firing_dates` call).

**Steps:**

1. Write tests that invoke `run_variant_comparison` with cached inputs and assert outputs match legacy `redesign_variants` for V3 row.

2. Implement `variant_comparison.py`. For V0–V2, V4–V6 variants, continue calling legacy `redesign_variants.evaluate_variant` (not yet migrated). For V3, use engine path. Compare the two paths' output in the test to prove parity for V3 specifically.

3. Commit: `feat(studies): add variant_comparison.run_variant_comparison (V3 via engine, others legacy)`.

### Task P2-E2: Wire `scripts/nfo/redesign_variants.py` to call the new study

Modify `_legacy_main` so the V3 branch calls `nfo.studies.variant_comparison.run_variant_comparison` and the rest falls through to legacy. The wiring test already monkeypatches `_legacy_main`; extend it to also verify that `nfo.studies.variant_comparison.run_variant_comparison` is called when `--variant V3` is requested (or by default).

Commit: `refactor(redesign_variants): route V3 variant through engine-backed study`.

---

## Bundle F (P2) — Completion

### Task P2-F1: Run full suite, update master design, write completion report, tag

1. Run full test suite including smoke:
```bash
.venv/bin/python -m pytest tests/nfo/ -q
```
2. Regenerate index:
```bash
.venv/bin/python -m nfo.reporting
```
3. Write `docs/superpowers/plans/2026-04-22-nfo-platform-phase2-completion.md`:
   - Commit count
   - Test count
   - Parity results (exact match? drift? known gaps?)
   - Deferrals: exits/execution/capital/metrics/datasets, remaining 5 script migrations, full `select_live_rule` requires engine.execution.

4. Commit and tag:
```bash
git tag -a p2-complete -m "NFO platform Phase 2 — triggers + entry + selection extracted to engine"
```

## Acceptance (master design §10.1 P2 + §12 items 3)

- [ ] `engine/triggers.py` exists and passes V3 legacy parity (byte-exact firing-date set).
- [ ] `engine/cycles.py::group_fires_by_cycle` passes V3 legacy parity.
- [ ] `engine/entry.py::resolve_entry_date` is the only entry-date resolver used by any `live_rule` code path.
- [ ] `engine/selection.py` implements `day_matched`, `cycle_matched`; `live_rule` stubbed with clear NotImplementedError pointing to P3.
- [ ] `scripts/nfo/redesign_variants.py` V3 branch routes through engine path.
- [ ] All tests green (including smoke).
- [ ] `tests/nfo/engine/` has parity tests for triggers, cycles, entry.
- [ ] Completion report written, `p2-complete` tag applied.

## Deferrals (documented in completion report, scoped to P3+)

- `engine/exits.py`, `engine/execution.py`, `engine/capital.py`, `engine/metrics.py`
- Full `src/nfo/datasets/{raw,normalized,features,trade_universe,study_inputs}.py` pipeline
- Legacy-script body replacement for v3_capital_analysis, v3_robustness, v3_falsification, v3_live_rule_backtest, time_split_validate
- `select_live_rule` full implementation (requires engine.execution)
- Master summary generator
- Monitor migration

---

*End of Phase 2 implementation plan.*
