# Backtest rerun — plain-language report (2026-04-20)

## What was run and in what order

1. `refresh_vix_cache.py --from 2023-12-15 --to 2026-04-18` — VIX cache now
   ends **2026-04-17** (18th was Saturday).
2. `load_underlying_daily` for NIFTY + BANKNIFTY through 2026-04-18 — both
   caches now end **2026-04-17**.
3. `backtest_grid.py` with the window extended to 2026-04-30 — regenerated
   `spread_trades.csv` end-to-end. Result: **82 trades** (was 70), latest
   expiry 2026-03-30.
4. `v3_fill_gaps.py` — regenerated the 4 V3 gap trades with the new
   cost-inclusive schema.
5. `tune_thresholds.py --write` — picked a new best threshold combo.
6. `redesign_variants.py` — recomputed V0-V6 against the 82-trade set,
   with the hardcoded baseline Sharpe/max-loss updated to match the new
   universe (was baked to the pre-cost 70-trade numbers).
7. `time_split_validate.py` — train/test split against the fresh data.
8. `v3_capital_analysis.py` for both PT50 and HTE.
9. `historical_backtest.py --end 2026-04-17` — 559-day per-signal scan
   (was 495).

All 115 unit tests still pass.

---

## TL;DR — the important shifts

- **Baseline got worse.** The unfiltered 82-trade set now averages
  ₹-679 per trade with Sharpe -0.84. The original 70-trade gross baseline
  was Sharpe -0.40. The drop comes from two sources: (a) subtracting real
  transaction costs, (b) the Dec 2025 – Mar 2026 cycles had several
  managed/max-loss trades that pulled the average down. There is no
  money to be made without a filter.

- **V3's full-window edge went from "small positive" to "roughly zero."**
  Full-window Sharpe -0.35, avg per trade ₹-221. Win rate 70% on 10
  filtered trades. Directionally still less bad than baseline (a +59%
  Sharpe "lift" relative to -0.84), but no longer profitable after costs.

- **The train/test split still diverges sharply.** Train 2024: 88% win,
  Sharpe +1.65. Test 2025+: 0% win on only 2 trades, Sharpe -2.48. The
  sample is still too small to pronounce V3 broken — but the direction
  is consistent with "edge found in 2024 doesn't generalise to 2025+".

- **HTE still beats PT50 on capital deployment.** HTE: +₹22.76L
  compounding, 0 drawdown, 8/8 wins. PT50: +₹7.07L compounding, 13.3%
  max drawdown, 6/8 wins. This happens because the expiry-settlement
  path skips exit brokerage on legs that expire worthless — costs bite
  PT50 harder.

- **Grade dashboard is no longer structurally impossible.** With
  VIX_RICH=15 and 559 days of fresh data, the 2-year scan produces 1 day
  at 6/8, 23 days at 5/8, 36 days at 4/8. Pre-fix this was 0/0/0. The
  daily scorecard is now actually usable for signal-watching.

- **7/8 and 8/8 are still unreachable.** Event-risk signal (s8) still
  fails 535 of 535 resolvable days because any CPI/Fed/Budget in the DTE
  window counts as "high severity," and that's basically every month.
  Skew (s7) is unknown on all 559 days because the call-side option data
  isn't cached. Both remain future work.

---

## 1. Tuning — fresh winner

`tune_thresholds.py` grid-searched against the 82-trade cost-inclusive set.
Winner:

```
VIX absolute threshold     : 18          (was 14 on the narrower dataset)
VIX 3-month percentile     : 0.50        (unchanged)
IV − RV spread             : 2.0 pp      (was 4.0)
Pullback off 10-day high   : 2.0 ATR     (unchanged)
```

Against those rules, 12 trades would have passed the filter. 92% win
rate, Sharpe 2.48 per-trade. Worst single trade -₹1,955. Sharpe came
down from the earlier 13.38 (which was on 6 winners-only) — the fresh
data introduced a loss into the filtered set, which is actually
healthier as a statistic.

The higher VIX threshold (18 vs 14) reflects the fact that the tuner
now has access to the Feb–Mar 2026 cycles where VIX was elevated but
the cycles lost money anyway; a stricter bar kept those out.

This new combo is persisted to `results/nfo/tuned_thresholds.json` and
will be loaded by `regime_watch` on next run.

## 2. Variant comparison on fresh data

Baseline (82 trades, no filter, cost-inclusive):
**Sharpe -0.84, win rate 80%, max-loss rate 7.3%.**

| Variant | Rule | Fires/yr | Trades | Win% | Sharpe |
|---|---|---:|---:|---:|---:|
| V0 | All 7 signals pass | 0 | 0 | — | — |
| V1 | V0 but CPI medium, not high | 0 | 0 | — | — |
| V2 | V1 + event window first 10 days only | 0 | 0 | — | — |
| **V3** | **IV-RV + trend + event OK, plus any of VIX/VIX-pct/IV-rank** | **10.37** | **10** | **70%** | **-0.35** |
| V4 | V3 with slightly tuned thresholds | 10.37 | 10 | 70% | -0.35 |
| V5 | V4 + relaxed "3 of 7 must pass" | 10.37 | 10 | 70% | -0.35 |
| V6 | Drop the filter — broader | 138.85 | 58 | 74% | -0.65 |

Reading:

- V0/V1/V2 still never trigger — the "all 7 must pass" structure is
  unreachable regardless of dataset.
- V3/V4/V5 are collapsed to identical numbers because the thresholds
  that differ between them (vix_rich=20 vs 22) are both above the
  sub-20 VIX levels in the new data — so the specific-pass gate's
  "any-of" clause is what's deciding.
- V6 shows the cost of dropping the filter entirely: 58 trades, 74%
  wins, but one-or-two bad cycles drag Sharpe to -0.65 and max-loss
  rate to 8.6% (higher than baseline).

None of them pass the "all four criteria" bar this time. The original
success criteria were written before costs and before the 2026 Q1
cycles; they are probably too strict to use as a published winner
metric now.

## 3. Train / test integrity

`time_split_validate.py` cuts at 2025-01-01.

**V3 (identical for V3 / V4 / V5):**

| Window | Trades | Win% | Sharpe |
|---|---:|---:|---:|
| Full 2024 – 2026 | 10 | 70% | -0.35 |
| Train (2024) | 8 | 88% | +1.65 |
| **Test (2025+)** | **2** | **0%** | **-2.48** |

The script's own label: "Inconclusive — test-set has only 2 matched
trade(s); need ≥ 10 for a meaningful OOS verdict."

Two test trades is not a sample. But pre-cost those were both small
winners; cost-inclusive they're both losers. And the test set is what
would actually resemble deploying this filter live today — data it
hasn't seen.

**V6 (no filter) holds up better statistically** but with bad absolute
numbers: train Sharpe -0.22, test Sharpe -0.84. Losing money
consistently.

## 4. Capital deployment (₹10L notional)

`v3_capital_analysis.py` scales up V3's 8 unique monthly cycles to a
₹10L allocation per trade. Note: this is still ~118 NIFTY lots per
cycle, which is retail-unrealistic leverage.

| Scenario | Wins / losses | Total P&L | Annualised | Max DD | Sharpe |
|---|---|---:|---:|---:|---:|
| **PT50, fixed size** | 6 / 2 (75%) | +₹6.10L | +27.1% | — | +1.13 |
| **PT50, compounding** | (above, reinvested) | +₹7.07L | +26.8% | 13.3% | — |
| **HTE, fixed size** | 8 / 0 (100%) | +₹13.08L | +58.1% | — | +2.90 |
| **HTE, compounding** | (above, reinvested) | +₹22.76L | +69.3% | 0.0% | — |

The new cycles (Dec 2025 – Mar 2026) all appear to be safely in the
"expired worthless" bucket for HTE, which is why HTE's 8/0 record
survives. PT50 took 2 losses because the early-exit rule closed trades
at a loss when spot dipped mid-cycle; those trades would have recovered
if held to expiry.

The per-trade Sharpe dropped for both variants relative to the previous
run (PT50: 2.40→1.13, HTE: 3.06→2.90) because per-trade standard
deviation widened with the new cycles.

## 5. Historical 2-year+ signal scan

`historical_backtest.py` walked 559 trading days (2024-01-15 to
2026-04-17).

Per-signal pass count:

| Signal | What it checks | Days passed |
|---|---|---:|
| s1 | VIX above 15 (threshold) | 41 |
| s2 | VIX in top 30% of 3-month range | 151 |
| s3 | Implied vol ≥ realized vol | 240 |
| s4 | Pullback ≥ 2% off 10-day high | 188 |
| s5 | IV rank ≥ 60% of 12-month range | 77 |
| s6 | Uptrend (EMA+ADX+RSI vote) | 451 |
| s7 | Skew tame | 0 (data unavailable) |
| s8 | No macro event in DTE window | 0 |

Score distribution:

| Score | Days | % |
|---:|---:|---:|
| 6/8 | 1 | 0.2% |
| 5/8 | 23 | 4.1% |
| 4/8 | 36 | 6.4% |
| 3/8 | 90 | 16.1% |
| 2/8 | 216 | 38.6% |
| 1/8 | 181 | 32.4% |
| 0/8 | 12 | 2.1% |

Compared to the prior run through 2026-01-09 (495 days), the extra
64 trading days added mostly 3/8 and 4/8 days. No new 7/8 or 8/8 days
because the event filter still blocks every resolvable day. The
dashboard ceiling remains 6/8.

---

## Artifacts regenerated

```
data/nfo/index/VIX_2023-12-15_2026-04-18.parquet        (579 rows)
data/nfo/index/NIFTY_2023-12-15_2026-04-18.parquet      (579 rows)
data/nfo/index/BANKNIFTY_2023-12-15_2026-04-18.parquet  (579 rows)
results/nfo/spread_trades.csv                           (82 trades)
results/nfo/spread_trades_v3_gaps.csv                   (4 trades)
results/nfo/spread_summary.csv
results/nfo/tuned_thresholds.json                       (vix_rich=18)
results/nfo/redesign_comparison.md + .csv
results/nfo/time_split_report.md
results/nfo/v3_capital_report_pt50.md + v3_capital_trades_pt50.csv
results/nfo/v3_capital_report_hte.md  + v3_capital_trades_hte.csv
results/nfo/historical_signals.parquet + historical_summary.md
```

Pre-refresh CSVs are preserved as `*.pre-recost-*.csv` timestamped
backups in `results/nfo/`.

## Honest caveats

- The 82-trade universe is still small. Any Sharpe or win-rate number
  with n < 15 is more storytelling than statistic. The test-set n=2
  V3 numbers are essentially anecdotes.

- The fresh Q1 2026 cycles hurt V3 because V3 was tuned against 2024
  data. This is a textbook overfit warning; the filter needs more live
  samples before it earns trust.

- None of this touches rolling option-parquet data beyond what was
  already cached. A full regenerate-from-scratch would need live Dhan
  calls for hundreds of missing strike series, which we did not do.

- Events (RBI/FOMC/Budget/CPI) are still read from a hardcoded calendar
  in `historical_backtest.py`. The `refresh_events.py` Parallel.ai
  refresh was deliberately NOT run — it costs real money per call.
