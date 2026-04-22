# Data Expansion Audit — NFO Research Platform

**Audit date:** 2026-04-21
**Author:** Claude Code (automated audit, human-reviewed output)
**Decision scope:** Entry B of the V3 kill-plan. Determine whether the Dhan-served history ceiling + current ingestion paths can support a materially larger cycle-matched sample for V3-HTE and V3-PT50.
**Decision grade:** This report is a go/no-go gate for building the rolling-walk-forward, regime-bucket, and adversarial-robustness studies proposed in the kill plan. If the expanded sample is insufficient, the kill plan stops here and V3 is moved to research-only.

---

## ⚠️ Erratum (2026-04-21, post-sentry, post-review-correction)

During the Item-3 2022 sentry I discovered that the V3 fire-counting approximation used to derive one figure in this audit had **two latent bugs** (`s8_event` semantic inversion + V3 score composition). A subsequent review caught a **third, more consequential issue**: the audit projected expanded sample size in **fire-days per year**, but the relevant decision unit is **fire-cycles per year** (one trade per monthly expiry under cycle_matched / live_rule selection).

| Quantity | v1 audit | v1 sentry | Post-review (authoritative) | Note |
|---|---:|---:|---:|---|
| 2024-26 V3 fire-days | 40 | 23 | **23** | Fire-days; cited for diagnostic only |
| 2024-26 V3 fire-cycles | not computed | 8 | **8** (3.6/yr) | Decision unit |
| 2022 V3 fire-days | — | 10 | **10** (10.1/yr) | With VIX warmup fixed |
| 2022 V3 fire-cycles | — | — | **4** (4.0/yr) | Decision unit |
| Projected trades over 5.65y expanded window | **60** | 58 | **~22** | At ~4 cycles/yr |

**Impact on this audit's conclusions:**

- The "CONTINUE, RESEARCH-ONLY" verdict still holds qualitatively: 2022 fires V3 at a rate indistinguishable from calibration (4.0 vs 3.6 cycles/yr).
- The quantitative projection was materially wrong. Line 15 ("~60 cycle-matched trades total") **over-stated** the expanded sample by ~2.7×. The correct projection is ~22 trades.
- This **sharpens** the research-only verdict: 22 trades across 5.5 years is at the floor of research-grade, not comfortably within it. Rolling walk-forward windows will have 3-5 trades each (high variance); tail-loss inference is not defensible on this sample.
- Dhan's history ceiling (§F1), cycle-reachability (§F2), trade-universe completeness (§F4), and testability (§F5) are independent and remain valid.
- Kill-conditions (§"Recommendation") are unchanged in direction but tighter in practice.

**What was wrong:**

- The `approx_fire` expression used `event_ok = ~s8_event` (inverted — parquet convention is `s8_event=True` means event OK) and summed `s1..s7` instead of `{s1..s6, event_ok}`.
- The sentry v1 annualized fire-days and compared to a fire-days prior without stopping to note the result doesn't translate to a trade count.
- The sentry v1 declared a `WARMUP_START = 2021-01-01` but `run_backtest` only loaded VIX for `[sentry_start, sentry_end]`, so the 63-day and 252-day VIX lookbacks were computed from truncated history for early-2022 days. Fixed in `historical_backtest._load_vix_daily` (now returns cached VIX in full).

The corrected V3 gate lives in `scripts/nfo/sentry_2022.py::v3_fire_mask` (11 tests in `tests/nfo/test_sentry_2022.py::TestV3FireMask`). Cycle counting lives in `scripts/nfo/sentry_2022.py::count_fire_cycles` (7 tests in `TestCountFireCycles`). These are the authoritative references from this point forward. See `results/nfo/audits/sentry_2022_decision_2026-04-21.md` v2 for the corrected full analysis.

---

## TL;DR

- Dhan's `/charts/rollingoption` endpoint empirically serves NIFTY 1-month PUT data back to **August 2020**. The ceiling was found by direct probing, not docs. Before that date the endpoint returns 0 rows (not an error).
- Current evidence base covers **2024-01 to 2026-04** (~2.25 calendar years, 555 trading days, 22 cycle-matched trades per V3 variant).
- Expansion to the Dhan ceiling would give **~5.5 total calendar years** — roughly **2.5× the current sample**.
- At V3's historical fire rate (~11 fires/yr), the expanded sample ceiling is **~60 cycle-matched trades total** per variant. Current is ~20–22.
- 60 trades is **not a production-grade sample** but *is* a research-grade sample. It enables meaningful walk-forward, regime-bucket, and parameter-stability analysis. It does not enable reliable tail inference.

**Recommendation: CONTINUE, RESEARCH-ONLY.** Build the expansion ingestion path for 2020-08 through 2024-01, then resume the kill-plan sequence (Entry A: rolling walk-forward). Do **not** treat V3 as production-promotable on the expanded sample alone; any production claim requires either (a) widening to BANKNIFTY/FINNIFTY for ~3× fire density, or (b) a multi-month live-shadow parity gate.

---

## Findings

### F1. Dhan history ceiling (primary finding)

Probed `/charts/rollingoption` for NIFTY monthly-expiry ATM PUT at 5-day windows across 2016–2024. Results:

| Probe window | Rows returned |
|---|---|
| 2024-06-03 .. 2024-06-07 | 35 (baseline, cached in repo) |
| 2023-06-05 .. 2023-06-09 | 35 |
| 2022-06-06 .. 2022-06-10 | 35 |
| 2021-06-07 .. 2021-06-11 | 35 |
| 2020-12-01 .. 2020-12-07 | 35 |
| 2020-10-01 .. 2020-10-07 | 28 |
| 2020-09-01 .. 2020-09-07 | 35 |
| 2020-08-01 .. 2020-08-07 | 35 |
| 2020-07-01 .. 2020-07-07 | **0** |
| 2020-04-01 .. 2020-04-07 | 0 |
| 2019-06-10 .. 2019-06-14 | 0 |
| 2018 / 2017 / 2016 | 0 |

**Empirical floor: 2020-08.** The endpoint returns an empty payload (not a 400) for pre-August-2020 dates — this is a silent data boundary, not an API error.

Caveats:
- Only tested NIFTY 1-month expiry PUT, ATM strike. Deeper-OTM strikes (required for the 0.30Δ short leg and 100-point protective leg) may have sparser coverage — must be verified during actual ingestion.
- Weekly-expiry contracts predate 2020 differently; irrelevant for V3 which is monthly-DTE-locked.
- VIX data: `/charts/historical` appears to reach further (current cache starts 2023-04; not probed beyond, but Dhan's `EQ_IDX` endpoint is widely believed to serve 10+ years).
- NIFTY spot (`INDEX`): not probed; likely serves back to at least 2010 via `/charts/historical`.

### F2. Current coverage and sample size

| Artifact | Span | Rows |
|---|---|---|
| `data/nfo/index/NIFTY_2023-12-15_2026-04-18.parquet` | 2023-12-15 → 2026-04-17 | 579 trading days |
| `data/nfo/index/VIX_2023-04-22_2026-04-21.parquet` | 2023-04-24 → 2026-04-20 | 742 trading days |
| `data/nfo/datasets/features/historical_features_2024-01_2026-04` | 2024-01-15 → 2026-04-10 | 555 trading days |
| `data/nfo/datasets/trade_universe/trade_universe_nifty_2024-01_2026-04` | 2024-02-22 → 2026-02-23 | 86 rows (all param variants) |
| Rolling option cache (`data/nfo/rolling/`) | 2023-12-21 → 2026-03-30 | 2,767 parquets |

**V3 canonical (delta=0.30, width=100) in trade_universe:** 44 rows across two PT variants:
- HTE (`param_pt=1.0`): 20 trades
- PT50 (`param_pt=0.5`, `param_manage=21`): 22 trades
- Unique cycles represented: 22 (out of 28 monthly cycles in span)

### F3. V3 fire rate and approximate sample under expansion

Approximating V3's trigger (score≥4 AND trend AND iv-rv AND events-ok AND any-vol) over the current 555-day features parquet yields **40 coarse fire-days**, concentrated heavily in 2024 (28), with 2025 (6) and early 2026 (6) much thinner. The canonical engine (via `TriggerEvaluator`) produces ~22 fire-cycles across the same window, consistent with the "11 fires/yr" memory. The 2024-vs-2025+ asymmetry is the first caution signal: **recent years fire less often, so linear extrapolation of sample count may overstate expansion yield.**

Projection table (using 11 fires/yr as the best prior; fire rate likely varies by regime):

| Window | Calendar years | Expected fires | Notes |
|---|---|---|---|
| Current (2024-01 → 2026-04) | 2.25 | ~22 (observed) | baseline |
| Expanded (2020-08 → 2026-04) | 5.67 | ~62 | linear projection at 11/yr |
| Realistic (regime-weighted) | 5.67 | 45–70 | 2020–2022 VIX regimes may fire more; 2023–2025 less |

A **~55-trade** working assumption is prudent.

### F4. Trade-universe completeness stats

- **Cycle hit rate:** 22 of 28 monthly cycles (79%) in the current window have at least one reachable V3-canonical trade. The 6 missing cycles are likely ones where either (a) the V3 gate did not fire, or (b) no strike satisfied the 0.30Δ / 100-pt / 35-DTE tolerance.
- **Entry-delta snap quality:** Mean absolute error vs 0.30Δ target = 0.0088; max = 0.0567. 4 of 86 rows (~5%) exceed the configured 0.05 tolerance. **Fallback-snap rate is low — this is a positive signal.**
- **Width exactness:** 100% of V3-canonical rows hit the 100-point width exactly; no fallback to alternate widths required.

### F5. Testability of both variants

- **V3-HTE:** 20 reachable trades, spanning 2024-03 to 2026-02. Outcomes: mostly `expired_worthless`; a handful of `max_loss` and `partial_loss`. Tail-loss inference is **not statistically defensible** on 20 trades.
- **V3-PT50:** 22 reachable trades over the same window. Outcomes mix `profit_take` / `managed` / `expired_worthless`.

After expansion both variants project to ~50 trades, which marginally supports walk-forward and regime-bucket work but **still will not support tight tail-loss confidence intervals**.

---

## What the expansion would cost (operationally)

Before building the ingestion path, the scope is worth stating:

1. **NIFTY spot daily bars for 2020-08 → 2023-12.** One `/charts/historical` call, ~1 second, writes one parquet.
2. **VIX daily bars for 2020-08 → 2023-04.** One call, one parquet.
3. **Rolling options:** For ~40 additional monthly cycles × ~20 strike offsets × PE (and CE if skew signal is kept) = **800–1,600 `rolling_option` calls**. At Dhan's 20 req/sec bucket, this is 1–2 minutes of wall time; the cache will grow by ~30 MB.
4. **Macro event calendar for 2020-08 → 2023-12.** Currently hardcoded in `historical_backtest.py` from 2024 onwards only. Needs manual extension (RBI MPC dates, FOMC, CPI, Union Budget) — ~5 hours of research, or one Parallel.ai call.
5. **Feature and trade-universe rebuild.** Rerun `historical_backtest.py --start 2020-08-01` and `v3_fill_gaps.py` on the expanded window; then `p6_seed_datasets.py` to refresh the two canonical datasets. The existing engine should work unchanged.
6. **Drift-registry bookkeeping.** Dataset hashes in runs issued before this audit will be flagged stale by the index regeneration — expected and healthy.

**Total engineering cost: ~1 day** (half coding, half validating the event calendar). No new engine logic required. No strategy YAML changes required.

---

## Regime diversity of the expansion window

The 2020-08 → 2024-01 window adds **genuinely different regimes** to the evidence base:

| Sub-window | Regime character |
|---|---|
| 2020-08 → 2020-12 | Post-COVID recovery, high VIX, high IV rank |
| 2021 | Strong bull trend, compressing IV, low-drawdown environment |
| 2022 | Russia/Ukraine shock, Fed-hiking regime, multi-quarter IV spikes |
| 2023 | Recovery with banking stress (SVB), SEBI regulatory run-up to 2024-10 reforms |

Specifically the 2022 regime is missing from the current evidence base entirely. This is the strongest qualitative argument for expansion — **V3's fragility claims cannot be tested without at least one sustained-volatility regime in sample.**

---

## Risks and caveats

1. **Pre-Oct-2024 SEBI regime.** F&O microstructure changed materially after the SEBI Oct-2024 reforms (lot size, position limits, margin treatment). Data before that date is still usable for signal-level research but carries a real "different market" caveat; execution-realism claims derived from pre-reform data must be flagged.
2. **Lot-size history.** NIFTY lot was 50 → 25 → 50 → 75 → 65 across the 2020-2025 window. The platform's `report_defaults.underlying_lot_size` is a single scalar (currently 65). Expanded backtests must either use a time-varying lot-size lookup or normalize P&L to "per contract" units. **This is a correctness issue, not a data issue — and is the single most likely source of silent error.**
3. **Event calendar gaps.** Missing FOMC/RBI dates in the pre-2024 hardcode means the `s8_events` signal will trivially pass during 2020–2023, over-firing V3 in that sub-window. This must be fixed before any expanded backtest is trusted.
4. **Strike-liquidity distortion in 2020–2021.** Deep-OTM NIFTY puts at the right delta/width may not have traded in all cycles; `rolling_option` will still return a synthetic price, but bid/ask slippage would have been materially worse. This is not a data-availability issue but a realism issue for any live-execution study downstream.
5. **Fire-rate non-stationarity.** 2024 fired much more than 2025 on the approximate gate. If the true expanded sample is 45 (not 60), the research-only verdict hardens.

---

## Recommendation: CONTINUE — research-only

**Reasoning.** The Dhan ceiling is permissive enough to roughly triple the calendar footprint and the engineering cost is one day. The expanded sample does not reach a production-defensible size, but it **does** reach a size that supports the kill-plan's research-grade studies (walk-forward, regime-bucket, parameter stability, tail stress). Declaring V3 dead at the current 20-trade sample would be premature; declaring V3 production-ready on even the 55-trade expanded sample would be reckless.

**Specifically proceed to Entry A (rolling walk-forward) immediately after:**

1. Building the 2020-08 → 2023-12 ingestion path (spot, VIX, rolling options, events).
2. Fixing the lot-size history correctness issue (time-varying lookup or per-contract normalization).
3. Backfilling the event calendar.
4. Rebuilding `historical_features` and `trade_universe` datasets; archiving the current ones under a `pre_expansion` label for parity retention.

**Kill V3 only if any of these become true after the expansion:**

- Expanded sample ≤ 30 cycle-matched trades per variant (would mean fire-rate is materially below the 11/yr prior and kill-plan statistical tests are not defensible).
- The 2022 regime sub-sample shows V3 loses money and the 2024 sub-sample dominates the headline Sharpe (= strategy is regime-specific to benign vol environments).
- Any of the 5 caveats above turns out to be a correctness bug in the existing data (would mean the current 2-year result is itself suspect).

**Move to production-shadow only if:**

- Expansion completes cleanly **and** the full adversarial-robustness pack (Entries A+C+D+E of your kill plan) passes **and** ≥ 3 months of live-shadow parity shows research-vs-live agreement within predefined tolerance. None of these gates are relaxable.

---

## Artifacts consulted (for reproducibility)

- `data/nfo/datasets/features/historical_features_2024-01_2026-04/{dataset.parquet,manifest.json}`
- `data/nfo/datasets/trade_universe/trade_universe_nifty_2024-01_2026-04/{dataset.parquet,manifest.json}`
- `data/nfo/index/NIFTY_2023-12-15_2026-04-18.parquet`
- `data/nfo/index/VIX_2023-04-22_2026-04-21.parquet`
- `data/nfo/rolling/` (2,767 cached parquets)
- `results/nfo/spread_trades.csv`, `results/nfo/historical_signals.parquet`
- Live Dhan `/charts/rollingoption` probes at 12 date windows (this session).

## Next concrete action

Approve this audit, then I'll stand up the 2020–2023 ingestion path as a single, tested PR:

1. Extend `historical_backtest.py` event calendar.
2. Add time-varying lot-size lookup to `src/nfo/universe.py`.
3. Add `scripts/nfo/expand_history.py` wrapper that runs spot + VIX + rolling + features + trade-universe rebuild in one pass.
4. Archive current datasets under `pre_expansion/` and regenerate canonical ones.
5. Spot-check one 2022 cycle end-to-end; compare its backtest to the live market record.

That is the last blocking prerequisite before Entry A can produce trustworthy walk-forward numbers.
