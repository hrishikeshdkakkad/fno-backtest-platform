# Phase 2 Completion Report

**Completed:** 2026-04-22
**Bundles:** A (triggers) → B (cycles) → C (entry) → D (selection) → E (studies + redesign_variants shadow) → F (acceptance)
**Commits since p1-complete:** 9
**Test count:** 566 passed (0 failures) in full suite including smoke (~2 min)

## Summary

- `src/nfo/engine/triggers.py` — spec-driven TriggerEvaluator with pluggable event_resolver. V3 firing dates match legacy `redesign_variants.get_firing_dates` byte-exact (23 dates over 2024-01-15 → 2026-04-18 window).
- `src/nfo/engine/cycles.py` — `group_fires_by_cycle` reproduces `_v3_cycles` output for V3.
- `src/nfo/engine/entry.py` — `resolve_entry_date` is the only code path allowed to produce entry dates for `live_rule` specs (master design §12 item 3). Matches legacy `_first_session_on_or_after` on all tested targets.
- `src/nfo/engine/selection.py` — `select_day_matched` + `select_cycle_matched` implemented and parity-tested vs `robustness.pick_trade_for_expiry`; `select_live_rule` stubbed (awaits P3 engine.execution).
- `src/nfo/studies/variant_comparison.py` — `run_variant_comparison_v3` composes engine pipeline for the V3 spec; metrics match legacy `evaluate_variant` on matched-trade count + win_rate.
- `scripts/nfo/redesign_variants.py` — `_shadow_v3_via_engine` runs the engine path alongside legacy on every invocation; drift is logged as warning.

## Parity outcomes

| Parity | Result |
|---|---|
| TriggerEvaluator vs redesign_variants.get_firing_dates (V3) | PASS byte-exact set equality on 23 firing dates |
| group_fires_by_cycle vs v3_live_rule_backtest._v3_cycles | PASS byte-exact (first_fire_date, target_expiry) set |
| resolve_entry_date vs _first_session_on_or_after | PASS matched across 7 sampled targets incl. weekends + NSE holidays |
| select_cycle_matched vs robustness.pick_trade_for_expiry (V3 hte) | PASS byte-exact on key columns |
| studies.run_variant_comparison_v3 vs evaluate_variant (V3) | PASS n_fires, n_matched, win_rate all match |

## Deferrals (scope for P3)

- `engine/exits.py` — exit-condition evaluators (PT25/PT50/PT75/HTE/DTE2, manage_at_dte)
- `engine/execution.py` — simulate_cycle (entry → exit tape walk)
- `engine/capital.py` — equity-curve / lot-sizing logic currently in `src/nfo/robustness.py`
- `engine/metrics.py` — currently covered by `src/nfo/calibrate.py`; keep as-is until P3 or explicit refactor
- `src/nfo/datasets/` pipeline stages (raw → normalized → features → trade_universe → study_inputs) with dataset manifests
- Legacy body replacement in `v3_capital_analysis`, `v3_robustness`, `v3_falsification`, `v3_live_rule_backtest`, `time_split_validate` (currently emit manifests but still carry full legacy logic)
- `select_live_rule` — requires engine.execution
- Master summary generator, monitor migration

## Next: Phase 3

P3 brings the engine extraction to completion (exits/execution/capital/metrics) and begins the dataset pipeline. `select_live_rule` ships as a first-class mode with live-valid simulation. See master design §13.3.
