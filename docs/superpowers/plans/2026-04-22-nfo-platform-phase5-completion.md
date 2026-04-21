# Phase 5 Completion Report

**Completed:** 2026-04-22
**Bundles:** A (v3_live_rule_backtest) → B (v3_capital_analysis + studies.capital_analysis) → C (time_split_validate + studies.time_split) → D (v3_robustness + studies.robustness) → E (v3_falsification + studies.falsification) → F (acceptance)
**Commits since p4-complete:** 10 (pre-tag; `p5-complete` tag adds the completion-report commit).
**Test count:** 1298 passed (up from 1028 at p4-complete — +270 tests across P5 including parity-per-script, engine-backed study unit tests, and incremental additions).

## Summary

Every V3-era legacy script body has been replaced with a thin wrapper over `src/nfo/studies/*`. The studies layer composes engine primitives end-to-end. Legacy CSV schemas and report layouts preserved for every migration through dedicated reshape helpers.

## Scripts migrated (5)

| Script | Studies module | Script LOC pre-P5 | Script LOC post-P5 | `_legacy_main` active lines |
|---|---|---|---|---|
| `v3_live_rule_backtest.py` | `studies.live_replay` (P3) | 269 | 188 | 85 |
| `v3_capital_analysis.py` | `studies.capital_analysis` | 333 | 277 | 54 |
| `time_split_validate.py` | `studies.time_split` (shadow) | 233 | 290 | 60 |
| `v3_robustness.py` | `studies.robustness` | 512 | 527 | 102 |
| `v3_falsification.py` | `studies.falsification` | 595 | 643 | 83 |

(LOC figures from `wc -l` on the post-P5 tree; active-line counts from `ast.FunctionDef`-scoped non-blank, non-comment lines inside each `_legacy_main`.)

## Studies modules shipped in P5

- `src/nfo/studies/capital_analysis.py` — `run_capital_analysis` — V3 cycle_matched → engine.capital → summary stats.
- `src/nfo/studies/time_split.py` — `run_time_split` — train/test split with verdict.
- `src/nfo/studies/robustness.py` — `run_robustness` — slippage sweep + LOO + block bootstrap orchestration.
- `src/nfo/studies/falsification.py` — `run_falsification` — tail-loss injection + allocation sweep + walkforward.

(P3's `live_replay` and P2's `variant_comparison` round out the six studies modules live in the tree.)

## Parity proof

All 5 migrations are parity-verified via dedicated tests in `tests/nfo/scripts/test_*_body_parity.py`:

| Migration | Parity scope |
|---|---|
| live_rule_backtest | `v3_live_trades_hte.csv` byte-exact on categorical + 1e-6 rel on `pnl_contract` |
| capital_analysis | `v3_capital_trades_*.csv` byte-exact on identity columns + 1e-6 rel on numerics |
| time_split_validate | `time_split_report.md` byte-parity (shadow mode preserved legacy numerics) |
| robustness | All 4 CSVs + `robustness_report.md` byte-exact (diff zero-output) |
| falsification | 3 CSVs 1e-6 rel + `falsification_report.md` byte-exact |

## Platform invariants (master design §16)

- ✅ Item 1: Every study runs from validated spec objects.
- ✅ Item 2: Every study writes a manifest-backed run directory.
- ✅ Item 3: Every report declares methodology explicitly.
- ✅ **Item 4: No business-critical selection logic lives only in scripts.** ← NEW in P5.
- ✅ Item 5: `entry_date >= first_fire_date` enforced by shared `engine.entry.resolve_entry_date`.
- ✅ Item 6: Live monitor and historical replay agree via `engine.triggers`.
- ✅ Item 7: Stale reports flagged via index generator + registry.
- 🟡 Item 8 (scripts thin): `_legacy_main` bodies are all ≤120 active lines (max is `v3_robustness.py` at 102). Total script LOC is still above 200 for 3 scripts (`v3_robustness` 527, `v3_falsification` 643, `time_split_validate` 290) because legacy CSV reshape helpers + report formatters live at module scope. A future pass can split these into `scripts/nfo/_legacy_helpers/*.py` if needed.
- ✅ Item 9: Tests cover every Pydantic model + engine function + study.

## Deferrals to P6

- Full dataset pipeline (`datasets/{raw,normalized,features,trade_universe,study_inputs}.py`) with per-stage manifests.
- Archival of old narrative reports under `results/nfo/legacy/archive/` (currently deprecation is README-only).
- Potential further decomposition of legacy reshape helpers into dedicated `scripts/nfo/_legacy_helpers/*.py` to slim scripts further.
- Potential migration of utility scripts (`v3_fill_gaps.py`, `recost_trades.py`) into platform commands if still useful.

## Next: Phase 6

P6's scope, when needed, is the dataset pipeline. The platform is now functionally complete — every study runs from spec → engine → studies → wrapper → manifest-backed run dir. P6 formalizes what the raw data layer looks like under the master-design §7 five-stage pipeline.
