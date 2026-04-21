# Session Review — NIFTY Regime & V3 Filter Pipeline

**Session date:** 2026-04-20 through 2026-04-21 IST.
**Scope:** Build an 8-signal regime watcher for NIFTY credit spreads, calibrate thresholds
from 2y of Dhan data, validate via time-split + cross-reference with 70 real backtest trades,
expose winner filter (V3) as read-only advisory in the live TUI, stress-test at ₹10L sizing.

Review this doc and the linked artefacts. **Be adversarial** — I've tried to call out my own
gaps below but expect I missed some. Score rubric suggestion at the end.

---

## 1 — Objectives (ordered by session progression)

| # | Objective | Status |
|---|---|---|
| 1 | Expand `regime_watch.py` from 4 to 8 signals with Parallel.ai enrichment | ✅ shipped |
| 2 | Calibrate thresholds against 2.5y cached Dhan backtest (70 trades) | ✅ shipped |
| 3 | Fix 5 correctness bugs found in an external code review (P1 + four P2) | ✅ shipped |
| 4 | Add entry-timing technical indicators (Bollinger / MACD / Stochastic) | ✅ shipped |
| 5 | 2-year historical backtest of the 8-signal grade | ✅ shipped — found 0/8 never fires |
| 6 | Iterative redesign loop (7 variants V0–V6) | ✅ shipped — **V3 emerged as winner** |
| 7 | Time-split validate V3 (train 2024 / test 2025+) | ⚠️ ran but **test sample = 2 trades, inconclusive** |
| 8 | V3 gate live in `regime_watch.py` as read-only advisory | ✅ shipped |
| 9 | ₹10L capital deployment analysis | ⚠️ ran, **1 data anomaly and PT/HTE confusion surfaced** |
| 10 | Pull 2 missing cycles via custom-entry Dhan backtest | ✅ shipped |
| 11 | PT=0.50 vs HTE comparison | ✅ shipped, **HTE is the right choice per this data** |

---

## 2 — Files touched (exhaustive)

### New files

| Path | Purpose | ~LOC |
|---|---|---:|
| `src/nfo/signals.py` | All pure-math indicators (IV rank, ATR/ADX/RSI, trend, skew, term, Bollinger, MACD, Stoch, entry-timing, composite) | 480 |
| `src/nfo/parallel_client.py` | Caching+offline wrapper around parallel-web SDK | 240 |
| `src/nfo/events.py` | Events parquet + `event_risk_flag` + **v3_event_risk_flag** | 220 |
| `src/nfo/enrich.py` | Macro brief + FII/DII + news via Parallel Task API | 195 |
| `src/nfo/calibrate.py` | Empirical POP table, Sharpe/Sortino stats, grid search | 215 |
| `scripts/nfo/tune_thresholds.py` | Offline threshold grid runner (no network) | 180 |
| `scripts/nfo/refresh_events.py` | Cron-friendly Parallel refresh | 85 |
| `scripts/nfo/historical_backtest.py` | 495-day 8-signal walk with hardcoded event calendar | 360 |
| `scripts/nfo/redesign_variants.py` | V0–V6 filter variant orchestrator + verdict | 430 |
| `scripts/nfo/time_split_validate.py` | Train/test OOS split validator | 160 |
| `scripts/nfo/v3_capital_analysis.py` | ₹10L sizing + compound/non-compound equity curves | 230 |
| `scripts/nfo/v3_fill_gaps.py` | Custom-entry backtest for 2 V3 fires missing from `spread_trades.csv` | 125 |
| `tests/nfo/test_signals.py` | 39 unit tests covering all signals + entry-timing | ~270 |
| `tests/nfo/test_parallel_client.py` | 6 SDK-wrapper tests with mocks | ~90 |
| `tests/nfo/test_events.py` | 6 events tests | ~80 |
| `tests/nfo/test_enrich.py` | 3 enrich tests | ~55 |
| `tests/nfo/test_calibrate.py` | 9 calibrate tests | ~130 |
| `docs/REVIEW.md` | **this document** | — |

### Modified files

| Path | Changes |
|---|---|
| `scripts/nfo/regime_watch.py` | Expanded `RegimeSnap` from 17 to 43 fields; expanded `_compute_signals` from 4 to 8 signals; added `_compute_v3_gate`; tuned thresholds auto-load from JSON; 4 new TUI panels; 3 new CLI flags (`--refresh-events`, `--no-parallel`, `--deep-brief`); auto-warmup of Parallel caches; strike-specific IV used for POP; empirical POP lookup; history parquet grew from ~22 to ~45 columns. **~1800 LOC total.** |
| `src/nfo/config.py` | Added `load_dotenv(ROOT/".env", override=True)` so project `.env` wins over shell env |
| `src/nfo/strategy.py`, `src/nfo/spread.py` | Reviewed: already used strike-specific IV via `r["iv"]` per row. **No change needed.** |
| `pyproject.toml` | Added `pydantic>=2.5`, `parallel-web>=0.1` |
| `.env.example` | Added `PARALLEL_API_KEY` and `PARALLEL_OFFLINE` lines |
| `.env` | Added `PARALLEL_API_KEY=<user-supplied>` per explicit permission |

### Generated artefacts

| Path | Content |
|---|---|
| `results/nfo/tuned_thresholds.json` | Grid-search winner from `tune_thresholds.py`. Legacy checked-in file still shows `vix_rich=22` (CBOE-calibrated). Re-running the tuner with the current India-calibrated grid (`(13,14,15,16,18)`) picks `vix_rich=14` as the new best-Sharpe. Run `scripts/nfo/tune_thresholds.py --write` to refresh. |
| `results/nfo/empirical_pop.parquet` | Win-rate by (\|Δ\|, DTE) bucket from 70 trades |
| `results/nfo/tier1_report.md` | Before/after report for Tier 1+2 signal additions |
| `results/nfo/historical_signals.parquet` | 495 rows × 24 columns — per-day signal pass/fail + raw numerics |
| `results/nfo/historical_summary.md` | 2y backtest distribution |
| `results/nfo/redesign_comparison.md` / `.csv` | V0–V6 variant metrics |
| `results/nfo/redesign_winner.json` | V3 machine-readable config |
| `results/nfo/time_split_report.md` | Train/test OOS validation per variant |
| `results/nfo/v3_capital_report_{pt50,hte}.md` / `v3_capital_trades_{pt50,hte}.csv` | ₹10L sizing analysis. Outputs are variant-suffixed so PT50 and HTE runs don't clobber each other. The legacy unsuffixed `v3_capital_report.md` / `v3_capital_trades.csv` pre-date that fix and should be treated as historical. |
| `results/nfo/spread_trades_v3_gaps.csv` | 2 custom-entry trades for V3 fires not in original `spread_trades.csv` — 4 rows (2 cycles × 2 exit variants) |
| `data/nfo/events.parquet` | Parallel-fetched RBI/FOMC/Budget/CPI calendar |
| `data/nfo/macro_brief.json` | Cached RBI / flow / earnings narrative with 7 citations |
| `data/nfo/fii_dii_flow.parquet` | Daily flow (Parallel-fetched, v1) |
| `data/nfo/parallel_cache/*.json` | SHA-keyed raw Parallel responses |
| `data/nfo/parallel_cost_log.parquet` | Every Parallel call with ts, method, processor, ms |
| `data/nfo/index/VIX_*.parquet` | VIX daily history pulled once |

### Memory files (Claude Code persistence)

| Path | Content |
|---|---|
| `~/.claude/projects/.../memory/project_tier1_regime.md` | Tier 1+2 shipping notes |
| `~/.claude/projects/.../memory/project_2yr_backtest_result.md` | 2y finding (0 days at 8/8) |
| `~/.claude/projects/.../memory/project_winning_filter_v3.md` | V3 rule config |
| `~/.claude/projects/.../memory/feedback_parallel_cost.md` | Don't re-run Parallel without programmatic purpose |

---

## 3 — The V3 filter (what actually shipped)

```
ENTER if ALL of these hold (each row-wise on live data):

  1. IV − RV ≥ −2 pp                    (strike-specific IV, RV-30d from NIFTY)
  2. Trend filter ≥ 2/3 votes            (EMA20>EMA50, ADX-14>20, RSI-14>40)
  3. V3 event check: no RBI / FOMC /
     Union Budget in first 10 days       (CPI demoted to medium)
  4. ≥ 1 of:
     VIX > 20   OR
     VIX 3-mo %ile ≥ 0.80   OR
     IV Rank 12-mo ≥ 0.60
```

Live integration: `scripts/nfo/regime_watch.py::_compute_v3_gate` (around line 590).
Renders in brief, TUI header, TUI enrichment panel, text dashboard advisory block.

**V3 is advisory only — not auto-execution, not a gate on existing 8-signal grade.**

---

## 4 — Measured performance (what to challenge)

### 8-signal regime grade over 2 years

- 495 trading days walked.
- Score 6/8: 2 days. Score 7/8: 0 days. Score 8/8: 0 days.
- Event-risk signal fails 474 of 474 resolvable days with strict rule (CPI = high).
- **Verdict: original 8-signal grade is structurally unachievable.** This finding motivated V3.

### V3 filter (backtest, current `results/nfo/time_split_report.md`)

| Window | Fires | Fires/yr | Matched real trades | Win% | Sharpe | MaxLoss% |
|---|---:|---:|---:|---:|---:|---:|
| Full 2024-02 → 2026-04 | 23 | 11.71 | 10 (at any width/delta) | 90% | +1.75 | 0% |
| Train 2024 | 16 | 16.87 | 8 | 88% | +1.98 | 0% |
| **Test 2025+** | **7** | **6.89** | **2** | **100%** | **+8.33** | **0%** |

### ₹10L capital-deployment, 8 distinct cycles

Current artifacts (`v3_capital_report_pt50.md` and `v3_capital_report_hte.md`),
after the post-hoc cost adjustment (`scripts/nfo/recost_trades.py`) deducted
STT + NSE/SEBI/GST + Dhan brokerage from each trade:

| Metric | PT50 non-comp | PT50 comp | HTE non-comp | HTE comp |
|---|---:|---:|---:|---:|
| Wins / losses | 7 / 1 (88%) | — | 8 / 0 (100%) | — |
| Total P&L | +₹9.49L | +₹13.81L | +₹13.15L | +₹22.92L |
| Annualised | +47.8% | +54.8% | +66.2% | +82.3% |
| Max DD (comp) | — | 9.5% | — | 0.0% |
| Sharpe (per-trade) | +2.41 | — | +3.07 | — |

**These numbers are too good to be real in live trading.** Reasons listed in §5.

---

## 5 — Known limitations & where I cut corners (BE SKEPTICAL HERE)

### A. Sample-size problems

- V3 has **10 in-sample matched trades** and **2 out-of-sample trades.** That's nowhere
  near enough to reject overfitting. The time-split's "holds up" verdict was too generous;
  the correct verdict is *inconclusive*.
- Metrics like "Sharpe +5.06" or "100% win rate" are artefacts of a small sample where all
  observed trades happened to be wins.

### B. Overfitting risk

- V3 rule structure was chosen by iterating against the same 70-trade backtest it's
  measured on. Classical in-sample overfitting.
- The "specific-pass gate" (require s3 + s6 + s8 + ≥1 vol signal) was designed to fit the
  pattern I saw in the May-2024 election-window trades. It may not generalise.

### C. Data quality concerns

- **2025-01-06 PT=0.50 trade**: `net_close_at_exit = −16.10` is physically impossible for a
  put credit spread (value must be in [0, width]). Flagged in-report. Inflates P&L by ~₹6L
  at ₹10L compounding. HTE version of the same trade is clean (+₹1,638/lot, sensible).
- **Hardcoded events calendar** in `historical_backtest.py::HARD_EVENTS`: best-effort from
  my training knowledge, not cross-verified against RBI / FOMC authoritative calendars.
  Errors here would shift the 22 V3 firing days.
- **Signal 7 (25Δ skew) never computed in historical backtest.** Set to `None` for all 495
  days. V3 doesn't depend on it, but live `regime_watch.py` does use live skew from chain.
  So backtest and live filter are subtly different.
- **No slippage / commission / bid-ask** modelled anywhere. Real P&L would be 20–40% lower.

### D. Rate-limit / cost leaks during development

- 5 macro-brief Parallel calls spent during schema-bug debugging (should have been 1).
- 2 FindAll calls that hit the 10-minute poll timeout due to `.matches` vs `.candidates`
  SDK-field mismatch.
- Total accidental Parallel spend: unknown but real. Logged in `parallel_cost_log.parquet`.

### E. Assumptions I made without asking

- Hardcoded FOMC April 28–29, 2026 — based on my knowledge, not verified.
- Treated CPI demotion (high → medium) as correct per backtest, but a reviewer may argue
  CPI is a first-order macro event and should stay high.
- Sized capital at 100% per trade in the ₹10L analysis scenarios — user asked "if we took a
  trade with 10L capital", I interpreted as full deployment. Alternative interpretation is
  10L capital with fractional sizing (more realistic for retail).
- Picked `PT=0.50` as the default variant initially. `plan_v2.md` actually specifies `PT=1.00`.
  Discovered only after user asked about hold-to-expiry. Fixed in final iteration.

### F. Tests I didn't write

- No integration tests for TUI rendering (only verified by eye).
- No tests for `_compute_v3_gate` — it's live code but only exercised via end-to-end runs.
- No tests for the historical backtest's per-day signal computation.
- No tests for the redesign variant filter logic (`_row_passes` in `redesign_variants.py`).

### G. Structural gaps

- **Signal 7 skew** still NaN in historical backtest (would need ~450 Dhan calls for the
  call-side rolling data). User and I agreed to skip; V3 works without it, but live filter
  may differ.
- **Events parquet** is Parallel-fetched in live mode vs hardcoded in backtest. Divergence
  between backtest and live V3 behaviour not quantified.
- **Intraday price action** — V3 evaluates on EOD data only. Live trade sizing is done
  intraday; strike selection could drift.

---

## 6 — Reproducibility steps

From a clean checkout with `.env` populated:

```bash
# 1. Install (pinned via pyproject.toml)
.venv/bin/pip install -e .

# 2. Run tests (should be 115 passing — 102 original + 13 added for
#    Tier-1 India-F&O changes: DoW/month helpers + cost model)
.venv/bin/python -m pytest tests/nfo/ -q

# 3. Offline smoke of the live pipeline (no Parallel, no Dhan).
#    Reads cached data/nfo/index/{NIFTY,VIX}_*.parquet. Exits 0 on any
#    successful evaluation — grade is reported in stdout, not exit code.
#    Add --alert-on-low-grade if you want the old "exit 1 on B+-or-lower"
#    behaviour for cron.
.venv/bin/python scripts/nfo/regime_watch.py --brief --no-parallel

# 4. Re-run the 2-year historical backtest (uses cached Dhan + hardcoded events)
.venv/bin/python scripts/nfo/historical_backtest.py

# 5. Re-run the redesign variants comparison
.venv/bin/python scripts/nfo/redesign_variants.py

# 6. Time-split validation
.venv/bin/python scripts/nfo/time_split_validate.py

# 7. Capital analysis — writes variant-suffixed outputs so both runs
#    persist (no more PT50→HTE clobber).
#      results/nfo/v3_capital_report_pt50.md + v3_capital_trades_pt50.csv
#      results/nfo/v3_capital_report_hte.md  + v3_capital_trades_hte.csv
.venv/bin/python scripts/nfo/v3_capital_analysis.py --pt-variant pt50
.venv/bin/python scripts/nfo/v3_capital_analysis.py --pt-variant hte

# 7b. If you re-ran the backtest pre-cost (old schema), bring the CSVs up
#     to the new cost-inclusive schema without needing Dhan:
.venv/bin/python scripts/nfo/recost_trades.py

# 8. Live TUI (requires Parallel key in .env + Dhan credentials)
.venv/bin/python scripts/nfo/regime_watch.py --tui
```

All scripts write markdown/CSV/parquet artefacts to `results/nfo/`.

---

## 7 — Recommended reviewer checklist

### Correctness (must-check)

- [ ] Confirm `src/nfo/signals.py` math matches standard formulae (BB, MACD, Stoch,
      Wilder ATR/ADX/RSI). Compare ~5 known historical values against a second source.
- [ ] Verify `historical_backtest.py::HARD_EVENTS` against authoritative RBI, FOMC, BLS,
      and Indian Budget sources. Report any wrong dates.
- [ ] Reproduce the 2025-01-06 `net_close = −16.10` anomaly. Is it a Dhan data quirk, a
      bug in `_merge_series`, or a real illiquidity print?
- [ ] Audit the V3 rule's conditions (in `_compute_v3_gate`) against my English description
      of V3 in §3. Are they equivalent?
- [ ] Spot-check 3 random V3 firing days: do all four V3 conditions truly pass on those
      days? Redesign variants stores the raw booleans.
- [ ] Check `redesign_variants._row_passes` — does the "specific-pass gate" match my
      definition? (Require s3 + s6 + s8 core AND ≥ 1 of s1/s2/s5.)

### Robustness (should-check)

- [ ] Run the pipeline on a fresh date range (extend 2026-01 → 2026-04). Does V3 still fire
      at all? Metrics should degrade but not catastrophically.
- [ ] Simulate 1–3 injected `max_loss` outcomes at random trade positions. What's the DD
      distribution?
- [ ] Check slippage realism: if each per-lot P&L is reduced by ₹500 (typical live fill
      cost for a 100-wide NIFTY spread), do V3's numbers still clear baseline?
- [ ] Verify `src/nfo/parallel_client.py` caching: same call twice → second hits cache
      (should be verifiable in `parallel_cost_log.parquet` time deltas).
- [ ] Try `PARALLEL_OFFLINE=1` path — does `regime_watch.py --tui` degrade gracefully?

### Bias check (meta)

- [ ] Did I cherry-pick the V3 rule structure? Re-run `redesign_variants.py` with 3
      DIFFERENT structural gates and see if V3 still wins. (I only tested V3's specific
      structure plus 6 others.)
- [ ] Time-split: I declared V3 "holds up" with only 2 test trades. Reviewer should
      downgrade this verdict to "inconclusive".
- [ ] PT=0.50 vs HTE: I ran PT=0.50 first by default, then switched to HTE when user
      asked. Did I re-tune V3 rules under HTE? **No** — rules same for both. Reviewer
      should check whether V3 would differ under HTE sampling.

---

## 8 — Suggested scoring rubric (1–10 per dimension)

| Dimension | My self-assessment | Reviewer notes |
|---|---:|---|
| Code correctness (ignoring known bugs) | 7 | Pure-math signals are well-tested; integration code less so |
| Statistical rigour | 4 | Small samples everywhere, overfitting risk acknowledged but not addressed |
| Backtest realism (slippage, fees, tails) | 3 | None modelled |
| Documentation & reproducibility | 8 | Every artefact is scripted, rerunnable |
| Live-system safety (V3 as advisory, not gate) | 8 | Deliberately read-only; no auto-trade |
| Data-quality guardrails | 5 | One known anomaly; no systematic bad-data detection |
| Test coverage of pure-math code | 8 | 102 passing; all new math has unit tests |
| Test coverage of integration / live code | 3 | No integration tests for regime_watch, _compute_v3_gate, TUI render |
| Cost discipline (Parallel API spend) | 5 | Some waste during debug; memory rule saved now |
| Overall craftsmanship | 6 | Lots shipped, clear structure, weak statistical bar |

**My honest composite guess: 5–6 / 10.** Good enough for paper-trading / advisory, not for
live capital deployment without further rigour.

---

## 9 — The three things I'd do if given another session

1. **Pull `max_loss` events into V3's test window explicitly.** Instead of cross-referencing
   against `spread_trades.csv` (which was itself a specific backtest), run a fresh daily
   backtest where V3 fires AND take the 35-DTE-before-expiry trade regardless, to see
   whether V3 avoids the observed max-loss events in the unfiltered data.
2. **Model slippage and fees.** `-₹500/lot` per trade round-trip is realistic for NIFTY
   options on Dhan. Rerun all P&L under this assumption.
3. **Randomised tail stress.** Per-month draw a random outcome from the unfiltered
   distribution (including the 4.3% max-loss tail), run 1000 trials, report DD and equity
   distribution percentiles. This is the honest way to communicate risk at sample n=8.

---

_Document written by Claude in the same session as the work. Reviewer: weight that bias
when scoring. Everything above should be independently verifiable from the artefacts
listed._

---

## Addendum — Reviewer findings fixed (2026-04-21)

### F1 (High) — V3 really demotes CPI now

- **Was:** `src/nfo/events.py::v3_event_risk_flag` returned `severity="high"` on any event whose `.severity == "high"`, and `refresh_macro_events` force-sets CPI to `high`. Net effect: CPI blocked V3.
- **Fix:** `v3_event_risk_flag` now decides severity **by kind only** (V3_HIGH_KINDS = {RBI, FOMC, BUDGET}). CPI returns as `"medium"`.
- **Verified:** stub parquet with one CPI row + V3 call returns `medium` (was `high` before fix).

### F2 (High) — Strike-specific IV in V3 gate

- **Was:** `_compute_v3_gate` used `atm_iv` (nearest-to-spot strike) — contradicting the docs claim of "strike-specific IV".
- **Fix:** `_compute_v3_gate` now takes `short_strike_iv` and prefers it. Falls back to ATM when no candidate exists. Reasoning line shows which IV source was used.
- **Caveat:** historical_backtest.py still computes `atm_iv` per day and feeds that into V3. To fully close the gap you'd re-walk the 495 days storing short-strike IV; this is not done. Live and backtest V3 now differ — **live is strike-specific, backtest remains ATM-based**. Flag in §5.

### F3 (Medium/High) — Net-close bounds guard live

- **Was:** `_merge_series` in `src/nfo/backtest.py` accepted `net_close=-16.10` for a put credit spread (physically impossible; spread value ∈ [0, width]). Triggered false profit-take.
- **Fix:** `_merge_series` now drops rows with `net_close < 0` or `short_close < long_close`. Anomalous bars are skipped instead of driving exits.
- **Verified:** re-pulled gap trades. 2025-01-06 PT=0.50 now exits at **₹25.50/sh (₹1,657/lot)** — sensible — instead of the phantom **₹41.80/sh (₹2,717/lot)**.

### F4 (Medium) — CPI calendar corrected

- **Was:** `HARD_EVENTS` had 2026-01-14, 2026-02-11, 2026-04-14.
- **Fix:** updated to BLS-official 2026-01-13, 2026-02-13, 2026-04-10 (reviewer-supplied). Earlier 2024-2025 CPI dates retained as-is (reviewer did not flag those).

### F5 (Medium) — OOS inconclusive threshold

- **Was:** `time_split_validate.py` reported "Holds up" for any test split with ≥ 2 trades.
- **Fix:** threshold raised to ≥ 10 OOS trades. At n < 10 the verdict is **"Inconclusive"** with explicit train/test win-rates shown. V3's 2-trade test split now correctly reads "Inconclusive" (previously "Holds up").

### Updated numbers after fixes

| Analysis | Before | After |
|---|---|---|
| **PT=0.50 capital (10L, non-comp)** | +₹10.18L (+51.3%/yr) | **+₹8.71L (+43.9%/yr)** |
| PT=0.50 capital (10L, compound) | +₹14.94L | **+₹12.29L** |
| PT=0.50 Sharpe | +2.00 | **+2.35** |
| HTE capital (unchanged — unaffected by anomaly) | +₹10.76L / +₹17.29L | same |
| V3 time-split verdict | "Holds up" (2 OOS trades) | **"Inconclusive"** (same data, honest verdict) |

The anomalous ₹2,717/lot trade contributed **~₹1.47L to fixed P&L and ~₹3–4L to compound P&L**. The fix reveals the PT=0.50 strategy's real edge is smaller than initially reported; HTE remains clearly better (+₹17.29L compound vs ₹12.29L for PT=0.50).

### Residual gaps not addressed

1. **F2 backtest path**: historical_backtest.py still uses ATM IV. Closing this requires re-walking the 495 days + storing short-strike IV per day. ~30 minutes of work, not done in this addendum.
2. **Remaining hardcoded event dates**: only 2026 CPI was flagged + fixed. RBI / FOMC / Budget dates across 2024-2026 remain as-written; reviewer should audit these against authoritative sources.
3. **Still no integration tests** for `_compute_v3_gate`, TUI rendering, or end-to-end live flow.
4. **Still zero slippage / fees** modelled. A future pass should reduce per-lot P&L by ₹300-500 to reflect Dhan fills.

### Post-fix self-score (round 1)

Correctness ↑ from 7 to **8** (critical bugs fixed). Statistical rigour unchanged at **4**. Live-system safety still **8** (V3 remains advisory). Overall composite guess now **6–7 / 10**; still paper-trade territory, not live-capital territory.

---

## Addendum round 2 — second-pass reviewer findings (2026-04-21)

### F6 (Medium) — Backtest IV-RV now uses strike-specific IV

- **Was:** `historical_backtest.py::evaluate_day` derived `iv_minus_rv` from ATM IV only, so the persisted parquet and `redesign_variants._row_passes` disagreed with live V3.
- **Fix:** added strike-specific IV pick inside `evaluate_day`. For each day with chain data, compute `put_delta` at per-strike IV, pick strike closest to 0.30Δ, use that IV in signal 3. Falls back to ATM IV when chain data is missing. Stored as new `short_strike_iv` column; `iv_minus_rv` now prefers it.
- **Verified:** re-ran historical backtest + redesign variants. V3 firing count went from 22 → 23 days; metrics materially unchanged (90% win, Sharpe +1.75, 0% max-loss, still winner). Robustness to this change is reassuring.

### F7 (Medium) — Historical backtest is reproducibly offline

- **Was:** `_load_vix_daily` required cached min ≤ start AND cached max ≥ end; any shortfall triggered a Dhan network call, and the script raised on connection failure.
- **Fix:** three changes in `_load_vix_daily`:
  1. Accept cached coverage with a **tail gap ≤ 7 calendar days** (Dhan lags 1-2 sessions anyway).
  2. If Dhan client is `None` and cache is partial, return cached rows with a warning (don't raise).
  3. If Dhan fetch raises (offline / 400 / 4xx), fall back to cached data with a warning instead of propagating the exception.
- **Effect:** scripts now run offline when reasonable cache exists. The user's reported `httpx.ConnectError` path no longer kills the backtest.

### Residual gaps after round 2

- **Slippage / fees** still not modelled (same as round 1).
- **No integration tests** for `_compute_v3_gate` / TUI (same as round 1).
- **Other hardcoded event dates** (RBI, FOMC, Budget 2024-2025, earlier CPI) not systematically audited (same as round 1).
- **No authoritative source** for NIFTY-50 earnings calendar — V3 backtest doesn't use earnings, but live V3 might (via Parallel FindAll).

### Post-fix self-score (round 2)

Correctness ↑ from 8 to **9** (backtest-live consistency restored; offline reproducibility fixed). Everything else unchanged. Overall composite now **7 / 10**; paper-trade territory with higher confidence the metrics at least reflect the same rule across live and backtest paths.
