# NFO Platform Phase 3 — Canonical Live-Valid Execution Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` to execute. Fresh subagent per bundle. TDD enforced.

**Goal:** Complete the engine layer (`exits`, `execution`, `capital`, `metrics`) and ship `select_live_rule` as a first-class selection mode backed by `engine.execution`. Migrate `v3_live_rule_backtest.py` to run through the platform's live-replay study. Master design §13.3 deliverable.

**Architecture:** Strangler-fig. Extract logic from `src/nfo/backtest.py`, `src/nfo/robustness.py`, and `src/nfo/calibrate.py` into `src/nfo/engine/`. Legacy modules keep their public APIs (they currently have callers in `scripts/nfo/`) but delegate internally to the engine. Parity tests lock the extraction.

**Master design reference:** `docs/superpowers/specs/2026-04-21-nfo-research-platform-design.md` §6 (evaluation semantics), §10.1 P3 parity rule (no parity expected for look-ahead cycle reports; dual-mode comparison between cycle_matched and live_rule is the new deliverable), §12 acceptance items 3 + 4 (live ↔ research parity).

**Tech stack:** Python 3.14 via `.venv/bin/python`. Pydantic v2. pandas. pytest.

---

## Execution conventions

- TDD: failing test → observe fail → implement → observe pass → commit.
- Parity:
  - Exits/execution: byte-exact on non-numeric (outcome, exit_date, dte_exit); 1e-6 relative on floats.
  - Capital: 1e-6 relative on total_pnl / equity / dd.
  - Metrics: 1e-9 relative (pure math).
- `from __future__ import annotations` on every new module.
- Commit style: Conventional Commits.

---

## Bundle A (P3) — engine/exits.py

### Task P3-A1 — extract `_manage_exit` + payoff helpers

**Files:**
- Create: `src/nfo/engine/exits.py`
- Create: `tests/nfo/engine/test_exits.py`

**Contract:**
```python
@dataclass
class ExitDecision:
    exit_row: pd.Series | None   # None => settled at expiry
    outcome: Literal["profit_take", "managed", "expired_worthless",
                     "partial_loss", "max_loss"]
    net_close_at_exit: float
    exit_date: date
    dte_exit: int
    pnl_per_share: float

def decide_exit(
    merged_legs: pd.DataFrame,
    *,
    exit_spec: ExitSpec,
    net_credit: float,
    short_strike: float,
    long_strike: float,
    spot_at_expiry: float,
    expiry_date: date,
) -> ExitDecision:
    """Single source of truth for when/how a credit-spread cycle exits.

    Replaces `backtest._manage_exit` + the expiry-settlement branch at the
    tail of `backtest._run_cycle`. Does not touch costs — that stays in
    execution.py. Returns an ExitDecision the caller can assemble into a
    SpreadTrade-equivalent row.
    """
```

Semantics (must match legacy):
1. If `exit_spec.profit_take_fraction` < 1.0 and any merged row's `net_close <= (1 - pt) * net_credit`, exit at the first such row with `outcome="profit_take"`.
2. Else if `exit_spec.manage_at_dte` is not None and any merged row's `dte <= manage_at_dte`, exit at first such with `outcome="managed"`.
3. Else settle at expiry using `spread_payoff_per_share(short_strike, long_strike, net_credit, spot_at_expiry)` from `src/nfo/spread.py`.
4. For HTE variant (`profit_take_fraction=1.0`, `manage_at_dte=None`): skip branches 1+2, always settle at expiry.

**Steps:**

1. Write unit tests (4+ cases): pt50 hits, pt50 misses → managed at 21 DTE, HTE settles at expiry, empty merged frame → expire on spot.
2. Write a parity test at `tests/nfo/engine/test_exits_parity.py` that constructs a representative `merged` DataFrame + SpreadConfig/ExitSpec pair, calls both `backtest._manage_exit` (for intermediate outcomes) and engine `decide_exit`, compares outcomes + exit_dates + dte_exit + pnl_per_share byte-exact where non-float and 1e-6 on floats.
3. Implement `engine/exits.py`.
4. Commit: `feat(engine): add decide_exit (single source of truth for exit timing)`.

### Acceptance
- `.venv/bin/python -m pytest tests/nfo/engine/test_exits.py tests/nfo/engine/test_exits_parity.py -v` all green.
- Full suite green.

---

## Bundle B (P3) — engine/execution.py

### Task P3-B1 — extract `simulate_cycle` from `backtest._run_cycle`

**Files:**
- Create: `src/nfo/engine/execution.py`
- Create: `tests/nfo/engine/test_execution.py`
- Create: `tests/nfo/engine/test_execution_parity.py`

**Contract:**
```python
@dataclass
class SimulatedTrade:
    # Same fields as backtest.SpreadTrade but constructed by engine path.
    # Enriched with cycle_id + trade_id + selection_id canonical identifiers.
    ...

def simulate_cycle(
    *,
    client: DhanClient,
    under: Underlying,
    strategy_spec: StrategySpec,
    entry_date: date,
    expiry_date: date,
    spot_daily: pd.DataFrame,
    selection_mode: SelectionMode,
) -> SimulatedTrade | None:
    """Single-cycle simulation, engine version of backtest._run_cycle.

    Differences from legacy:
    - Takes StrategySpec instead of SpreadConfig (derives profit_take,
      manage_at_dte, target_delta, dte_target, width from the spec).
    - Delegates exit logic to engine.exits.decide_exit.
    - Emits canonical identifiers (cycle_id, trade_id, selection_id).
    - Does not know about selection mode except to record it in the output.
    """
```

**Steps:**

1. Unit tests: feed a synthetic merged frame (no Dhan), verify entry/exit/cost math matches a hand-computed expected SimulatedTrade.
2. Parity test: pick 3 cycles from `results/nfo/spread_trades.csv` (pre-recosted), re-simulate them via `simulate_cycle` using cached Dhan data, assert outcome/entry_date/exit_date/pnl_contract within tolerance.
3. Implement.
4. Commit: `feat(engine): add simulate_cycle (engine version of _run_cycle)`.

### Acceptance
- Engine sim matches legacy row-for-row on 3 sampled cycles within 1e-6 relative on pnl_contract.

---

## Bundle C (P3) — engine/capital.py

### Task P3-C1 — extract `compute_equity_curves` from `robustness`

**Files:**
- Create: `src/nfo/engine/capital.py`
- Create: `tests/nfo/engine/test_capital.py`

**Contract:**
```python
@dataclass
class EquityResult:
    pnl_fixed: pd.Series
    pnl_compound: pd.Series
    equity_compound: pd.Series
    lots_fixed: pd.Series
    lots_compound: pd.Series
    total_pnl_fixed: float
    total_pnl_compound: float
    final_equity_compound: float
    max_drawdown_pct: float
    annualised_pct_fixed: float
    annualised_pct_compound: float
    sharpe: float
    years: float

def compute_equity_curves(
    trades: pd.DataFrame,
    *,
    capital_spec: CapitalSpec,
    years: float,
) -> EquityResult:
    """Move the function out of src/nfo/robustness.py into engine.capital.
    Signature swaps out the (capital, deployment_frac) pair for a CapitalSpec.
    """
```

**Steps:**

1. Unit tests: empty frame returns zero curve; single-trade frame; 10-trade frame exercising non-compounding + compounding + lot-rounding + drawdown.
2. Parity test: run on V3 matched trades (via `robustness.get_v3_matched_trades`), assert identical EquityResult vs legacy `robustness.compute_equity_curves(trades, capital=1_000_000, years=…)`.
3. Implement. In `src/nfo/robustness.py::compute_equity_curves`, replace body with a thin shim that constructs a CapitalSpec and delegates to engine.
4. Parity test must still pass after the shim swap (proves shim == engine).
5. Commit: `feat(engine): add compute_equity_curves; robustness delegates to engine.capital`.

---

## Bundle D (P3) — engine/metrics.py

### Task P3-D1 — extract `summary_stats` from `calibrate`

**Files:**
- Create: `src/nfo/engine/metrics.py`
- Create: `tests/nfo/engine/test_metrics.py`

**Contract:**
```python
# Identical to calibrate.SummaryStats + calibrate.summary_stats, just moved.
# calibrate.py becomes a thin re-export for backward compatibility.
```

**Steps:**

1. Unit tests for `summary_stats`: empty, single-trade, mixed win/loss, all-max-loss.
2. Implement `engine/metrics.py` with identical logic.
3. Replace `calibrate.summary_stats` body with `return engine.metrics.summary_stats(...)`. Keep `SummaryStats` re-exported.
4. Run full suite — many existing tests import from `nfo.calibrate`; they must still pass.
5. Commit: `feat(engine): extract summary_stats into engine.metrics; calibrate delegates`.

---

## Bundle E (P3) — select_live_rule + studies/live_replay

### Task P3-E1 — full `select_live_rule` + `studies/live_replay.py`

**Files:**
- Modify: `src/nfo/engine/selection.py` (replace NotImplementedError stub with full impl)
- Create: `src/nfo/studies/live_replay.py`
- Create: `tests/nfo/engine/test_selection_live_rule.py`
- Create: `tests/nfo/studies/test_live_replay.py`

**Contract:**
```python
def select_live_rule(
    cycles: dict[str, CycleFires],
    strategy_spec: StrategySpec,
    sessions: Iterable[date],
    *,
    client: DhanClient,
    under: Underlying,
    spot_daily: pd.DataFrame,
) -> pd.DataFrame:
    """Full live-rule selection. For each cycle:
      1. resolve_entry_date(spec, first_fire_date, sessions) -> entry_date
      2. engine.simulate_cycle(..., entry_date, expiry_date, ...) -> SimulatedTrade
      3. Collect trades, enrich with cycle_id/selection_id/first_fire_date.
    """
```

**Steps:**

1. Test that for a spec in `live_rule` mode, each cycle's entry_date equals `resolve_entry_date(spec, first_fire_date, sessions)`.
2. Parity test: run `select_live_rule` against the 6 V3 live cycles and compare to legacy `v3_live_rule_backtest.py`'s output `results/nfo/v3_live_trades_hte.csv`. Allow 1e-6 relative tolerance on pnl_contract.
3. Implement `select_live_rule` using `engine.entry.resolve_entry_date` + `engine.execution.simulate_cycle`.
4. Implement `studies/live_replay.py` that composes fires → cycles → select_live_rule → metrics.
5. Commit: `feat(engine): implement select_live_rule backed by engine.execution` and `feat(studies): add live_replay`.

---

## Bundle F (P3) — Acceptance + tag

1. Run full suite including smoke; confirm green.
2. Regenerate index.
3. Acceptance checklist per master design §12:
   - [ ] `engine/{exits,execution,capital,metrics}.py` exist with passing parity tests.
   - [ ] `select_live_rule` ships with parity against `v3_live_rule_backtest`.
   - [ ] `src/nfo/robustness.py::compute_equity_curves` delegates to engine.
   - [ ] `src/nfo/calibrate.py::summary_stats` delegates to engine.
   - [ ] Master §12 item 3 reaffirmed: `engine/entry.py` is the only entry-resolver for live_rule, now actually exercised end-to-end.
4. Write completion report `docs/superpowers/plans/2026-04-22-nfo-platform-phase3-completion.md`.
5. Commit + tag `p3-complete`.

## Deferrals (P4+)

- Full dataset pipeline (`datasets/{raw,normalized,features,trade_universe,study_inputs}.py`)
- Legacy body replacement for `v3_capital_analysis.py`, `v3_robustness.py`, `v3_falsification.py`, `time_split_validate.py` (they still emit manifests but carry full legacy logic)
- Monitor migration (`src/nfo/monitor/`)
- Master summary generator
- Archival of old narrative reports
- Full-suite live ↔ research parity test

---

*End of Phase 3 implementation plan.*
