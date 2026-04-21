# Phase 3 Completion Report

**Completed:** 2026-04-22
**Bundles:** A (exits) → B (execution) → C (capital) → D (metrics) → E (live_rule + live_replay) → F (acceptance)
**Commits since p2-complete:** 6 (run `git rev-list p2-complete..HEAD --count`)
**Test count:** 813 passed

## Summary

- `src/nfo/engine/exits.py` — `decide_exit` is the single source of truth for exit timing. Parity with legacy `backtest._manage_exit` across 7 scenarios.
- `src/nfo/engine/execution.py` — split into `simulate_cycle_pure` (pure, testable with synthetic data) and `run_cycle_from_dhan` (Dhan-fetching wrapper). Parity against `backtest._run_cycle` byte-exact on outcome + 1e-6 on pnl_contract across 3 sampled V3 cycles (profit_take, expired_worthless, max_loss — full exit-path coverage).
- `src/nfo/engine/capital.py` — `compute_equity_curves` extracted; `robustness.compute_equity_curves` now a 10-line delegation shim.
- `src/nfo/engine/metrics.py` — `SummaryStats` + `summary_stats` moved; `calibrate` re-exports (same class object preserved).
- `src/nfo/engine/selection.py::select_live_rule` — full impl composing `resolve_entry_date` + `run_cycle_from_dhan` + canonical id enrichment.
- `src/nfo/studies/live_replay.py::run_live_replay` — end-to-end study: triggers → cycles → select_live_rule → metrics.
- Parity live_replay vs legacy `v3_live_trades_hte.csv`: byte-exact on outcome/entry_date/expiry_date, 1e-6 rel on pnl_contract across all 8 V3 live cycles.

## Parity proof points

| Parity | Result |
|---|---|
| decide_exit vs backtest._manage_exit + spread_payoff_per_share | PASS — 7 scenarios byte-exact |
| simulate_cycle_pure vs _run_cycle | PASS — 3 cycles (profit_take, expired_worthless, max_loss), 1e-6 rel on pnl_contract |
| engine.capital vs robustness.compute_equity_curves (shim) | PASS — 1e-6 rel on all fields, full V3 matched trades |
| engine.metrics.summary_stats vs calibrate.summary_stats | PASS — identity (same class object + same function) |
| engine.selection.select_live_rule vs v3_live_rule_backtest | PASS — 8/8 cycles, byte-exact on categorical, 1e-6 rel on pnl_contract |

## Platform invariants re-verified (master design §12)

- Item 3 (entry): `engine/entry.py::resolve_entry_date` is the only code path resolving entry dates in live_rule mode. Now exercised end-to-end by `studies.live_replay`.
- Item 3 (triggers): `engine/triggers.py` is the only code path deciding whether a day fires. V3 parity: 23 firing dates (P2 Bundle A).
- Item 4 (live ↔ research parity): The same StrategySpec + features dataset produces identical fire/entry/exit decisions through engine vs legacy.

## Deferrals (P4+)

- Full dataset pipeline under `src/nfo/datasets/{raw,normalized,features,trade_universe,study_inputs}.py` (today: only `staleness.py`)
- Legacy script body replacement for `v3_capital_analysis`, `v3_robustness`, `v3_falsification`, `time_split_validate`
- `scripts/nfo/v3_live_rule_backtest.py` is still the wired legacy path; a follow-up replaces its body with `studies.live_replay.run_live_replay(...)` + wrap_legacy_run
- Monitor migration (`src/nfo/monitor/`)
- Master summary generator
- Archival of old narrative reports (`tier1_report.md`, etc.) under `results/nfo/legacy/`

## Next: Phase 4

P4 brings in the dataset pipeline, the final legacy-script body replacement, monitor migration, and master summary — master design §13.4 deliverables. With engine + entry + selection all locked in, P4 is mostly plumbing.
