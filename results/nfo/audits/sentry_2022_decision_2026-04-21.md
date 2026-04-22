# 2022 NIFTY V3 Sentry — Decision (v2, post-review correction)

**Date:** 2026-04-21
**Scope:** Item 3 of the V3 kill-plan (narrow 2022 sentry to decide whether the full 2020-08 → 2023-12 backfill is justified).
**Supersedes:** the v1 of this document in the same path, which compared **fire-days** to a **fire-cycle** prior (unit mismatch flagged in review).
**Artifacts:** Raw signals at `results/nfo/audits/sentry_2022_signals.parquet`; auto-generated per-signal + fire-day listing at `results/nfo/audits/sentry_2022_report.md`.

---

## Bottom line

| Window | Fire-days | Fire-cycles | Cycles/yr |
|---|---:|---:|---:|
| 2022 sentry | 10 | **4** | **4.0** |
| 2024-2026 calibration (same gate) | 23 | 8 | **3.6** |
| redesign_winner filtered_trades (1.96y) | — | 10 | 5.1 |

**Verdict: CONTINUE, but the expanded sample projection is smaller than previously stated.** 2022's ~4 cycles/yr is statistically indistinguishable from the 2024-2026 calibration's ~3.6 cycles/yr — V3 is not overfit to the 2024-2026 regime. However, the linear projection over the 5.65-year expanded window is **~22 trades**, not the ~55-60 trades quoted in the v1 sentry and in `data_expansion_audit_2026-04-21.md`. The research-only verdict is now tighter.

## What changed since v1

Three bugs in the v1 sentry path were fixed during review:

### 1. Unit mismatch: fire-days vs fire-cycles (P1)

The v1 report annualized the per-row fire-day count (11.2/yr) and compared it to the "~11 fires/yr" prior — but that prior was `redesign_winner.firing_per_year`, which is also fire-**days**. The **decision unit** is cycles (one trade per monthly expiry under `cycle_matched`/`live_rule` selection). 7 of the 11 v1 fire-days in 2022 clustered on the same 2022-07-28 expiry; by cycles, 2022 has 4 fires/yr, not 11.

Fixed in `scripts/nfo/sentry_2022.py::count_fire_cycles` and the sentry's `_summarise`. Seven new unit tests in `tests/nfo/test_sentry_2022.py::TestCountFireCycles`.

### 2. VIX warmup not applied (P1)

The sentry computed signals like `vix_pct_3mo` (63-day lookback) and `iv_rank_12mo` (252-day lookback) against a VIX frame truncated to the sentry window. The `WARMUP_START` constant in `sentry_2022.py` was never propagated into `run_backtest`, and `_load_vix_daily` filtered to `[start, end]` even when the cache had earlier data. Early-2022 signals were therefore computed from 0-3 prior bars.

Fixed in `scripts/nfo/historical_backtest.py::_load_vix_daily`: cached VIX is returned in full (not sliced to `[start, end]`) so evaluate_day's per-day slicing can use arbitrary lookback. A single Dhan call populated `data/nfo/index/VIX_2019-08-01_2024-02-15.parquet` (1123 rows, 4.5 years of warmup).

**Effect of fix:** 2022 fire-days dropped from 11 to 10 (one early-2022 fire turned out to depend on the truncated VIX lookback). Fire-cycles dropped from 5 to 4.

### 3. Unresolved-entry warning (P2)

`load_sourced_backfill` dropped `status: unresolved` entries silently, so the 18 unresolved CPI rows were indistinguishable from genuine no-event days downstream. V3 itself is safe (it demotes CPI to medium), but any non-V3 consumer of the expanded event set would understate event risk on those dates.

Fixed in `src/nfo/events.py::load_sourced_backfill`: emits a single WARN log per load with per-kind unresolved counts. YAML remains authoritative for forensics. Two new tests in `tests/nfo/test_event_backfill.py::TestUnresolvedWarning`.

## Decision framework (cycle units)

| 2022 cycle rate | Verdict |
|---|---|
| > 8 cycles/yr | Materially more — regime richer for V3; expansion yields larger-than-projected sample |
| 3-7 cycles/yr | About the same — research-only verdict stands; expansion still worthwhile |
| < 3 cycles/yr | Materially less — V3 may be overfit; consider kill or redesign |

**2022: 4 cycles/yr → "About the same"** (low end of the band).

## Why the projection update matters

Prior v1 projection (wrong): ~11 fires/yr × 5.65 years ≈ 60 trades.
Correct cycle-based projection: ~4 cycles/yr × 5.65 years ≈ **22 trades**.

That difference changes the flavor of the "research-only" verdict:

- **22 trades across 5.5 years** barely supports rolling walk-forward (each window has 3-5 trades — high variance).
- It does not support defensible tail-loss inference (needs N ≥ 50 typically).
- It still supports regime-bucket analysis and parameter-stability sweeps, with the caveat that conclusions in each bucket carry wide confidence intervals.

The path to a production-grade sample on NIFTY-monthly alone is now clearly **not reachable** — confirming a point the Entry-B audit had already flagged. The only paths are:

- Widen to BANKNIFTY/FINNIFTY (~3× fire density from broader universe) — out of scope for this kill plan per your NIFTY-only constraint.
- Relax V3's gate — but that's redesign, not validation.
- Multi-month live-shadow parity — qualitative evidence, not trade count.

## Data quality finding (non-blocking, NIFTY-only)

Six days in 2022 had implausible per-strike IV values in Dhan's rolling_option payload. These do not affect V3's fire-cycle count (none coincide with fire-days at the strike V3 picks), but they would contaminate any study reading `atm_iv` / `short_strike_iv` directly.

**Landed as part of PR1:** `src/nfo/data.drop_iv_anomalies` (drops rows with IV ≤ 0 or IV > 100% annualized, 10 unit tests). Wired into `historical_backtest._daily_snapshot_for_cycle` so signal computation uses filtered IV while the raw rolling cache remains untouched for forensics.

## Erratum propagation

The v1 of this document overstated the expansion yield. The v1 `data_expansion_audit_2026-04-21.md` has been updated with a matching erratum; the cycle-unit projection (~22 trades, not ~55-60) is the authoritative figure.

## Current kill-plan status

| Step | Status |
|---|---|
| Item 1 — NIFTY lot-size lookup | ✅ landed 2026-04-21 |
| Item 2 — sourced event backfill + unresolved-warn log | ✅ landed 2026-04-21 |
| Item 3 — 2022 NIFTY sentry (corrected) | ✅ **this document** (verdict: continue) |
| Item 4 / PR1 — expansion plumbing | **in progress** (IV filter landed; ingest script written; 2020-08 → 2023-12 Dhan fetch not yet run) |
| Item 4 / PR2 — lot-size migration + canonical regen | pending PR1 |
| Item 4 / PR3 — Entry A rolling walk-forward | pending PR1+PR2 |

## Recommendation for PR1 continuation

The three fixes above do not unblock further ingest — they correct the record. With the corrected cycle-unit projection, the expansion is still worth doing (it's cheap: ~45 seconds of Dhan calls + the existing plumbing) and the 22-trade sample still supports the kill plan's research goals.

Recommended immediate next step: run `scripts/nfo/expand_history.py` to populate 2020-08 → 2023-12 spot/VIX/rolling caches, then emit the PR1 coverage + anomaly report. Do **not** regenerate canonical datasets in this PR (that's PR2, gated on lot-size migration).
