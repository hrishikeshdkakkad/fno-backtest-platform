# Lot-Size Call-Site Migration Plan

**Date:** 2026-04-21
**Scope:** Item 2 follow-up to `data_expansion_audit_2026-04-21.md`.
**Status:** Documented only — call-site migration deferred until the dataset regeneration in the user's Item 4 (post-sentry-ingest).

---

## Why this is a separate deliverable

`nfo.universe.lot_size_on(name, as_of)` landed with 14 passing tests (+ 1 documented xfail covering the BANKNIFTY REGISTRY-vs-docs discrepancy). The primitive is correct.

However, **migrating the call sites will silently change every backtest metric** for any trade entered before 2025-12-30:

- Pre-2024-11-20 entries: current code uses lot=65, correct code uses lot=**25** (scalar is 2.6× too large).
- 2024-11-20 to 2025-12-29: current code uses lot=65, correct code uses lot=**75** (scalar is ~13% too small).

Flipping the scalar to `lot_size_on(...)` without regenerating `results/nfo/spread_trades.csv` would break `test_execution_parity.py` — the parity test compares engine output against the legacy CSV snapshot, which was computed with the buggy scalar. The parity failure would be real (the engine is now correct) but it would look like a regression.

Therefore: **migrate call sites only as part of the same change that regenerates the canonical datasets.**

---

## Call sites that must migrate

Each entry lists the exact change. `entry_date` is always in scope at the call site.

| # | File | Current line | Target replacement |
|---|---|---|---|
| 1 | `src/nfo/engine/execution.py:80` | `lot = int(under.lot_size)` | `lot = lot_size_on(under.name, entry_date)` |
| 2 | `src/nfo/backtest.py:101` | `lot = under.lot_size` | `lot = lot_size_on(under.name, cycle.entry_target_date)` |
| 3 | `scripts/nfo/backtest_one.py:64` | `lot = n.lot_size` | `lot = lot_size_on(n.name, entry_date)` (entry_date is in scope via the CLI) |
| 4 | `scripts/nfo/v3_fill_gaps.py:59` | `lot = under.lot_size` | `lot = lot_size_on(under.name, cycle.entry_target_date)` |
| 5 | `scripts/nfo/recost_trades.py:137` | `return int(get_under(u).lot_size)` | `return int(lot_size_on(u, entry_date))` — requires threading `entry_date` through the recost helper |

Secondary consumers (assertion-only, no behavior change) that should also be updated for consistency:

- `tests/nfo/engine/test_execution.py` lines 143, 148, 153, 196, 200, 202, 230, 357 — replace `under.lot_size` with `lot_size_on("NIFTY", entry_date)` in expected-value math. The tests will still pass because the entry dates they use fall in the post-2024-11-20 / pre-2025-12-30 window, where the lookup returns **75** and the current scalar is 65. After migration the assertions will match the production-code lot, as they should.

Not migrating:

- `src/nfo/instruments.py:43,64` — these read the CSV master's `lot_size` column. Not historical; just metadata passthrough. Leave alone.
- `src/nfo/universe.py` `Underlying.lot_size` field — stays as the "current scalar" for live code paths. Verified against `lot_size_on(name, today)` by the invariant tests.

---

## What breaks when we flip these five lines

Expected regressions (all load-bearing, all expected):

1. **`test_execution_parity.py`** will fail on every cycle with entry_date < 2025-12-30 (= essentially every row in `spread_trades.csv`). Fix: regenerate `results/nfo/spread_trades.csv` via `scripts/nfo/historical_backtest.py` + `scripts/nfo/v3_fill_gaps.py` in the same PR that flips the lines.
2. **`test_v3_capital_analysis_body_parity.py`, `test_v3_falsification_body_parity.py`, `test_v3_robustness_body_parity.py`, `test_v3_live_rule_backtest_body_parity.py`, `test_time_split_validate_body_parity.py`** — same story. All consume `spread_trades.csv` either directly or via the derived datasets.
3. **`data/nfo/datasets/trade_universe/trade_universe_nifty_2024-01_2026-04/dataset.parquet`** — all numeric columns (net_credit, pnl_contract, gross_pnl_contract, txn_cost_contract, buying_power) will shift. Hashes in `configs/nfo/.registry.json` plus every run manifest's `dataset_hashes` will flag stale. Expected.
4. **`results/nfo/master_summary.md`, `results/nfo/index.md`** — regenerated downstream.

The regeneration cascade is the entire reason this migration is tied to the dataset rebuild.

---

## Sequencing (aligning with the user's approved order)

1. ~~Land the `lot_size_on` primitive with tests.~~ ← **done (this PR).**
2. **Next:** Backfill the 2020-08 → 2023-12 event calendar with sourced RBI/FOMC/CPI/Budget dates (user's Item 2 / audit's Item 3).
3. **After that:** narrow 2022 sentry ingest (user's Item 3). The sentry uses cached options data if present; for any missing cycles it will either (a) fetch from Dhan, or (b) skip. The sentry does *not* need the call-site migration — it just needs the primitive available to be correct.
4. **If the sentry passes the continuation gate:** flip the five call-site lines + regenerate `spread_trades.csv` + rebuild canonical datasets + update run manifests. This is one PR.
5. **Then:** resume the kill plan with Entry A (rolling walk-forward) on the correctly-sized expanded dataset.

---

## Verification the primitive is ready

From this session:

```
$ .venv/bin/python -m pytest tests/nfo/test_universe.py -v
...
14 passed, 1 xfailed in 0.04s
```

The one xfail is intentional: `test_registry_banknifty_lot_matches_current_lookup` documents the NSE-circular-vs-REGISTRY disagreement for BANKNIFTY (doc says 30, REGISTRY says 35). Reconciling this requires looking up a fresh NSE circular — it is the second item tracked for follow-up.
