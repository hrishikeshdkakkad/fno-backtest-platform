# NFO Platform Phase 5 — Legacy Body Replacement Implementation Plan

> **For agentic workers:** `superpowers:subagent-driven-development` with fresh subagent per bundle. TDD enforced.

**Goal:** Replace the `_legacy_main` body of every remaining V3-era script with a thin wrapper over `src/nfo/studies/*` that consumes the engine. Post-P5: every script under `scripts/nfo/` is ≤200 lines and delegates its business logic to platform code. Master design §16 acceptance item 8 fully satisfied.

**Architecture:** Strangler-fig closes. For each legacy script:
1. Add or extend a corresponding `src/nfo/studies/<study>.py` module that composes engine primitives end-to-end.
2. Rewrite the script's `_legacy_main` to a call into the study (plus the pre-existing `wrap_legacy_run` shell).
3. Parity tests compare new study output against the legacy artifacts already committed under `results/nfo/runs/*` (from previous phases' smoke runs).

**Master design reference:** §3 studies layer, §6 selection modes, §10.1 P2 parity tolerances (apply to all numeric comparisons here), §16 items 4 + 8.

**Tech stack:** Python 3.14 via `.venv/bin/python`. Pydantic v2. pandas. pytest.

## Scope

**In scope for P5 (sequenced easiest → hardest):**
- Bundle A: `v3_live_rule_backtest.py` body → `studies.live_replay` wrapper (trivial — study exists from P3)
- Bundle B: `studies.capital_analysis` + replace `v3_capital_analysis.py` body
- Bundle C: `studies.time_split` + replace `time_split_validate.py` body
- Bundle D: `studies.robustness` + replace `v3_robustness.py` body
- Bundle E: `studies.falsification` + replace `v3_falsification.py` body
- Bundle F: P5 acceptance + `p5-complete` tag

**Deferred to P6 (separate phase):**
- Full dataset pipeline (`datasets/{raw,normalized,features,trade_universe,study_inputs}.py`)
- Dataset manifests for existing `data/nfo/index/`, `data/nfo/rolling/` caches
- Archival of old narrative reports under `results/nfo/legacy/archive/` (currently deprecation is documented via README only)

---

## Execution conventions

- TDD: failing test → observe fail → implement → observe pass → commit.
- Parity tolerance:
  - Non-numeric (outcome, dates, strings, ids): byte-exact.
  - P&L / equity / stats floats: 1e-6 relative (engine stack already at 1e-6 per P2-P3).
  - Bootstrap percentiles (same RNG seed): 1e-6 relative.
- Each replaced script must preserve its CLI behavior (same args, same exit codes, same legacy CSV/MD outputs under `results/nfo/`). The `wrap_legacy_run` call shape also stays; only the inside of `_legacy_main` changes.
- Commit style: Conventional Commits.

---

## Bundle A — v3_live_rule_backtest body replacement

### Task P5-A1: `scripts/nfo/v3_live_rule_backtest.py` → thin wrapper over `studies.live_replay`

**Files:**
- Modify: `scripts/nfo/v3_live_rule_backtest.py` — replace `_legacy_main` body with a call to `nfo.studies.live_replay.run_live_replay(...)` and write the legacy CSVs/MD from the result.
- Create: `tests/nfo/scripts/test_v3_live_rule_backtest_body_parity.py` — parity vs committed `results/nfo/v3_live_trades_hte.csv`.

**Contract:**
- CLI still produces `results/nfo/v3_live_trades_pt50.csv`, `v3_live_trades_hte.csv`, `v3_live_report.md`.
- New `_legacy_main` loads `v3_live_rule.yaml`, runs `run_live_replay` for pt50 + hte variants, assembles the three legacy outputs from the `LiveReplayResult.selected_trades` DataFrames.
- `pnl_contract` / `outcome` / `entry_date` in the regenerated CSV must match committed CSV within tolerance.

**Steps:**
1. Read `scripts/nfo/v3_live_rule_backtest.py::_legacy_main` to see exactly which columns + filenames it writes.
2. Write parity test (loads committed `v3_live_trades_hte.csv` snapshot; runs `_legacy_main` once; re-reads regenerated CSV; compares per-row on `entry_date`, `expiry_date`, `outcome`, `pnl_contract` within 1e-6).
3. Replace the body. The PT50 variant requires loading `v3_live_rule.yaml` with exit_rule.variant swapped to `pt50` — since StrategySpec forbids hash-drift, the cleanest approach is to:
   - Ship a sibling YAML `configs/nfo/strategies/v3_live_rule_pt50.yaml` with `strategy_version: 3.0.2` and the pt50 exit spec.
   - Or construct a modified spec in-memory (skip the registry drift check by passing `skip_drift_check=True` if the loader supports it; if not, add a loader helper `load_strategy_from_dict(raw_dict)` that skips registry updates).

Recommendation: ship the sibling YAML — cleaner, fits the strategy-per-spec model. Bump strategy_version to `3.0.2`.

4. Run parity test green.
5. Commit: `refactor(v3_live_rule_backtest): delegate body to studies.live_replay`.

**Acceptance:** parity test green; legacy output files regenerate with same values.

---

## Bundle B — studies.capital_analysis + v3_capital_analysis body

### Task P5-B1: `src/nfo/studies/capital_analysis.py`

**Files:**
- Create: `src/nfo/studies/capital_analysis.py`
- Create: `tests/nfo/studies/test_capital_analysis.py`

**Contract:**
```python
@dataclass
class CapitalAnalysisResult:
    selected_trades: pd.DataFrame    # one row per cycle
    equity_result: EquityResult      # from engine.capital
    stats: SummaryStats              # from engine.metrics
    pt_variant: Literal["pt50","hte","pt25","pt75","dte2"]


def run_capital_analysis(
    *,
    spec: StrategySpec,
    features_df: pd.DataFrame,
    atr_series: pd.Series,
    trades_df: pd.DataFrame,
    pt_variant: str,
    capital_inr: float,
    years: float,
    event_resolver: Callable | None = None,
) -> CapitalAnalysisResult:
    """Engine-backed capital analysis (master design §3, §10.1)."""
```

Pipeline:
1. `TriggerEvaluator(spec).fire_dates(features_df, atr_series)` → firing days
2. `group_fires_by_cycle(...)` → cycle index
3. `select_cycle_matched(trades_df, cycles, spec, pt_variant=pt_variant)` → selected
4. `engine.capital.compute_equity_curves(selected, capital_spec=CapitalSpec(...), years=years)` → equity
5. `engine.metrics.summary_stats(selected)` → stats

Tests: parity against `robustness.get_v3_matched_trades` + legacy `compute_equity_curves` on V3 cached data within 1e-6 rel on total_pnl_fixed / sharpe / max_drawdown_pct.

### Task P5-B2: Replace `v3_capital_analysis.py::_legacy_main` body

Rewrite `_legacy_main(argv)` to:
1. Parse `--pt-variant`.
2. Load v3_frozen.yaml, signals parquet, trades CSV + gaps.
3. Call `run_capital_analysis(..., pt_variant=args.pt_variant, capital_inr=10_00_000)`.
4. Write legacy `v3_capital_report_<variant>.md` + `v3_capital_trades_<variant>.csv` from the result.
5. Return `{"metrics": {...}, "body_markdown": ..., "warnings": []}` for `wrap_legacy_run`.

Parity test: regenerate CSVs, compare to committed files on `expiry_date`, `param_pt`, `outcome`, `pnl_contract` (1e-6 rel).

Commit: `refactor(v3_capital_analysis): delegate body to studies.capital_analysis`.

---

## Bundle C — studies.time_split + time_split_validate body

### Task P5-C1: `src/nfo/studies/time_split.py`

**Contract:**
```python
@dataclass
class TimeSplitResult:
    train_stats: SummaryStats
    test_stats: SummaryStats
    verdict: Literal["holds_up", "inconclusive", "broken", "no_fires"]
    n_train: int
    n_test: int


def run_time_split(
    *,
    spec: StrategySpec,
    features_df: pd.DataFrame,
    atr_series: pd.Series,
    trades_df: pd.DataFrame,
    train_window: tuple[date, date],
    test_window: tuple[date, date],
    inconclusive_threshold_trades: int = 10,
    event_resolver: Callable | None = None,
) -> TimeSplitResult:
```

Pipeline:
1. Evaluate triggers on full features.
2. Group cycles and select cycle_matched trades.
3. Split selected trades by `entry_date` into train/test windows.
4. Compute `summary_stats` on each.
5. Verdict: `no_fires` if train empty, `inconclusive` if test < threshold, `holds_up` if test sharpe > 0 AND test win_rate within 10% of train, else `broken`.

### Task P5-C2: Replace `time_split_validate.py::_legacy_main`

Rewrite to call `run_time_split` and write `results/nfo/time_split_report.md` from result. Parity: regenerate the report, match key numbers (train/test trade counts, sharpe ratios) to committed file within 1e-6.

Commit: `refactor(time_split_validate): delegate body to studies.time_split`.

---

## Bundle D — studies.robustness + v3_robustness body

### Task P5-D1: `src/nfo/studies/robustness.py`

This is the largest study — it currently lives half in `src/nfo/robustness.py` (core primitives) + half in `scripts/nfo/v3_robustness.py` (orchestration). Extract orchestration into `src/nfo/studies/robustness.py`.

**Contract:**
```python
@dataclass
class RobustnessResult:
    matched_trades: pd.DataFrame
    slippage_sweep: pd.DataFrame        # rows: (slippage_rupees_per_lot, summary metrics)
    leave_one_out: list[LooRow]
    bootstrap: BootstrapResult
    baseline_stats: SummaryStats
    baseline_equity: EquityResult


def run_robustness(
    *,
    spec: StrategySpec,
    features_df: pd.DataFrame,
    atr_series: pd.Series,
    trades_df: pd.DataFrame,
    capital_inr: float,
    years: float,
    bootstrap_iterations: int = 10_000,
    seed: int = 42,
    slippage_sweep_rupees: list[int] = [0, 250, 500, 750, 1000],
    event_resolver: Callable | None = None,
) -> RobustnessResult:
```

Pipeline:
1. Engine-select V3 matched trades.
2. For each slippage level: apply `robustness.apply_slippage` + recompute summary_stats + equity curves → row.
3. Leave-one-out via `robustness.leave_one_out` on matched trades.
4. Block bootstrap via `robustness.block_bootstrap` (seed=42) with `bootstrap_iterations`.

Reuses all `src/nfo/robustness.py` primitives (these stay; only orchestration moves).

### Task P5-D2: Replace `v3_robustness.py::_legacy_main`

Call `run_robustness`, write the 4 legacy artifacts (`robustness_slippage.csv`, `robustness_loo.csv`, `robustness_bootstrap.csv`, `robustness_report.md`) from the result.

Parity: regenerate and compare to committed CSVs — `slippage_sweep` and `leave_one_out` byte-exact on non-numeric + 1e-6 rel on floats; `bootstrap` same seed → identical percentiles.

Commit: `refactor(v3_robustness): delegate orchestration to studies.robustness`.

---

## Bundle E — studies.falsification + v3_falsification body

### Task P5-E1: `src/nfo/studies/falsification.py`

**Contract:**
```python
@dataclass
class FalsificationResult:
    tail_loss: pd.DataFrame             # n_injections × iterations × outcome
    allocation_sweep: pd.DataFrame      # allocation_fraction × equity metrics
    walkforward: pd.DataFrame           # fold × metrics


def run_falsification(
    *,
    spec: StrategySpec,
    features_df: pd.DataFrame,
    atr_series: pd.Series,
    trades_df: pd.DataFrame,
    capital_inr: float,
    years: float,
    tail_loss_injections: list[int] = [1, 2, 3],
    tail_loss_iterations: int = 1000,
    allocation_fractions: list[float] = [0.25, 0.5, 1.0],
    walkforward_folds: int = 4,
    seed: int = 42,
    event_resolver: Callable | None = None,
) -> FalsificationResult:
```

Reuses `robustness.inject_tail_losses`, `robustness.compute_equity_curves` (now engine-backed) — just the orchestration is new.

### Task P5-E2: Replace `v3_falsification.py::_legacy_main`

Call `run_falsification`, write 4 legacy artifacts. Parity: same seed → identical results.

Commit: `refactor(v3_falsification): delegate orchestration to studies.falsification`.

---

## Bundle F — P5 Acceptance + tag

1. Full suite green.
2. Regenerate index + master_summary + every study's run directory (the 5 wrapped scripts).
3. Every `scripts/nfo/*.py` file is ≤200 LOC (modulo regime_watch.py which has 1800 LOC of TUI and stays out of this count).
4. Master design §16 acceptance items re-verified:
   - Item 4: no business-critical selection logic lives only in scripts — ✅ all 5 V3-era scripts now delegate to `src/nfo/studies/`.
   - Item 8: every `scripts/nfo/<name>.py` under 200 lines (except regime_watch per §11.1 "script names preserved, business logic moved").
5. Write `docs/superpowers/plans/2026-04-22-nfo-platform-phase5-completion.md`.
6. Commit + tag `p5-complete`.

## Deferrals (P6+)

- Full dataset pipeline (`datasets/{raw,normalized,features,trade_universe,study_inputs}.py`) with per-stage manifests and staleness detection.
- Archival of old narrative reports under `results/nfo/legacy/archive/` (currently only `results/nfo/legacy/README.md` deprecates them).
- Potential `scripts/nfo/v3_fill_gaps.py` + `scripts/nfo/recost_trades.py` migration (these are utility scripts, lower priority).

---

*End of Phase 5 implementation plan.*
