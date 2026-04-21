# V3 master analysis — every test, every condition

Generated 2026-04-20 after a fresh end-to-end rerun of:
`tune_thresholds.py` → `redesign_variants.py` → `time_split_validate.py` →
`v3_capital_analysis.py (pt50, hte)` → `historical_backtest.py` →
`v3_robustness.py` → `exit_sweep_backtest.py` →
`entry_perturbation_backtest.py` → `v3_falsification.py`.

Spec frozen at `v3-spec-frozen-2026-04-20` (see `docs/v3-spec-frozen.md`).
150 / 150 unit tests pass. Data window ends 2026-04-17 (cache refreshed,
`time_split_report.md` reflects manual edits to fires/yr arithmetic).

---

## 1. What we're testing

Same V3 filter, two exit rules:

- **PT50** — close at 50 % of credit, manage at DTE = 21.
- **HTE**  — hold to expiry (no profit-take, no manage).

Trade-matching rule: NIFTY, Δ ≈ 0.30, width = 100 points, per frozen spec.

Both variants have **8 distinct V3-firing cycles** in the 2.25-year
window.

---

## 2. Headline numbers at default conditions (no slippage, full capital)

| | PT50 | HTE |
|---|---:|---:|
| Trades matched | 8 | 8 |
| Win rate | 75 % (6/2) | 100 % (8/0) |
| Total P&L (fixed ₹10L) | +₹6.10L | +₹13.12L |
| Final equity (compound ₹10L) | +₹17.07L | +₹32.76L |
| Annualised CAGR | +27.0 % | +70.1 % |
| Max drawdown | 13.3 % | 0.0 % |
| Per-trade Sharpe (capital-aware) | +1.14 | +2.91 |

These are the numbers that looked impressive in isolation. The rest of
this report is the falsification battery.

---

## 3. Filter variant landscape (`redesign_comparison.csv`)

| Variant | Fires/yr | Matched trades | Win% | Sharpe | Max-loss% |
|---|---:|---:|---:|---:|---:|
| V0 – V2 (all-signals gates) | 0.00 | 0 | — | — | — |
| **V3 / V4 / V5** (specific-pass gate) | **10.44** | **10** | **70 %** | **-0.35** | **0 %** |
| V6 (no gate) | 139.39 | 58 | 74 % | -0.65 | 8.6 % |

Baseline (unfiltered 82 trades, cost-inclusive): Sharpe −0.84, win rate
80 %, max-loss rate 7.3 %. **V3 is the only variant whose max-loss rate
is 0 %**, but its Sharpe on the per-trade universe is still negative.
The positive capital returns in §2 come from compounding on a small,
concentrated set of winners.

**Tuner's current best** (`tuned_thresholds.json`): `vix_rich=18,
vix_pct_rich=0.5, iv_rv_rich=2.0, pullback_atr=2.0` → 12 trades at
91.7 % win, Sharpe 2.48. This is a different filter than V3 — it's what
the grid-search arrives at if you re-tune on the post-refresh data.

---

## 4. Time-split train/test (`time_split_report.md` — manually curated)

V3 on the same 2024-01 → 2026-03 data, split at 2025-01-01:

| Split | Fires | Trades | Win% | Sharpe |
|---|---:|---:|---:|---:|
| Full  | 23 | 10 | 70 % | −0.35 |
| Train | 16 | 8  | 88 % | +1.65 |
| Test  |  7 | 2  |  0 % | −2.48 |

Verdict in-file: **Inconclusive** (test n = 2). This is the first
sign that the V3 edge doesn't generalise cleanly across calendar time.

---

## 5. Historical signal scan (`historical_summary.md`)

559 trading days (2024-01-15 → 2026-04-17) rated against the 8-signal
scorecard:

| Score | Days | % |
|---:|---:|---:|
| 6/8 | 1 | 0.2 % |
| 5/8 | 23 | 4.1 % |
| 4/8 | 36 | 6.5 % |
| 3/8 | 89 | 16.0 % |
| 2/8 | 216 | 38.9 % |
| 1/8 | 178 | 32.1 % |
| 0/8 | 12 | 2.2 % |

Still **0 days at 7/8 or 8/8** — `s8_event` fails on 535/555 resolvable
days (every day with a CPI / Fed / Budget window); `s7_skew` is
unknown on all 559 days (no cached call-side chain). The all-8 gate
remains structurally unreachable, which is why V3 has to use the
specific-pass gate instead of a raw score.

---

## 6. Robustness trio (`robustness_report.md`)

### 6a. Slippage sweep — compound ₹10L equity after extra ₹/lot drag

| ₹/lot slip | PT50 | HTE |
|---:|---:|---:|
| 0    | ₹7.07L | ₹22.76L |
| 100  | ₹5.21L | ₹19.49L |
| 250  | ₹2.75L | ₹15.10L |
| **500**  | **−₹57k** | **₹9.04L** |
| 750  | −₹3.12L | ₹4.29L |
| 1000 | −₹5.06L | ₹61k |

Break-even: **PT50 ≈ ₹457/lot**, **HTE > ₹1,000/lot**. PT50's edge
lives inside the retail slippage range; HTE's survives until you hit
₹1,000/lot.

### 6b. Leave-one-out (capital-aware Sharpe)

| Variant | Worst Sharpe after one drop | Best Sharpe after one drop | Spread |
|---|---:|---:|---:|
| PT50 | +0.81 (drop 2025-01-30) | +1.83 (drop 2024-09-26 loss) | 1.02 |
| HTE  | +2.46 (drop 2025-01-30) | +4.65 (drop 2024-05-30)     | 2.19 |

Both remain positive — no single V3 trade is load-bearing to the point
of collapse. But the spread between best and worst LOO case tells you
the edge is not evenly spread across cycles.

### 6c. Block bootstrap (10,000 resamples)

| Variant | P(compound ≥ ₹10L) | P5 final equity | P50 | P95 | P95 max DD |
|---|---:|---:|---:|---:|---:|
| PT50 | **94.0 %** | ₹9.72L | ₹17.47L | ₹28.66L | 32.3 % |
| HTE  | **100.0 %** | ₹23.45L | ₹32.15L | ₹49.24L | 0.0 % |

Caveat on HTE: the underlying 8-cycle matched set has zero losses, so
resampling cannot produce a losing draw — the 100 % survival is a
bounded property of the input, not evidence of safety.

---

## 7. Exit sweep — the same 20 NIFTY 0.30Δ cycles under 5 exit rules

| Exit rule | Win% | Avg PnL | Sharpe | Max-loss% |
|---|---:|---:|---:|---:|
| **PT25** | 85 % | +₹131 | **+0.88** | 0 % |
| PT50 | 75 % | +₹153 | +0.78 | 0 % |
| PT75 | 60 % | −₹105 | −0.39 | 0 % |
| **HTE** | 75 % | −₹895 | **−1.08** | **20 %** |
| DTE2 | 65 % | −₹526 | −0.75 | 10 % |

On the unfiltered universe, **PT25 is best and HTE is the worst**.
V3's HTE headline (Sharpe +2.91) only exists because V3 happens to
filter out the 4 HTE max-loss cycles — that filtering ability is
precisely what the rest of this report questions.

---

## 8. Entry perturbation — first fire vs +1 trading day

| | First fire | Plus one day | Worst of two |
|---|---:|---:|---:|
| **PT50 total** | +₹3,776 (Sharpe +2.44) | +₹3,544 (+2.30) | +₹1,423 (+1.07) |
| **HTE total** | +₹9,121 (Sharpe +11.07) | **−₹1,677 (−0.28)** | −₹1,822 (−0.31) |

**PT50 absorbs a one-day delay.** HTE does not — two specific cycles
(2024-11-22, 2025-01-06) flip from wins to −₹6k / −₹1.7k when entered
one session later.

---

## 9. Tail-loss injection — what if one of the 8 wins had been a max loss?

Resample the 8 cycles with replacement 10 000 times, replace `k` random
rows with synthetic max-loss cycles (`(net_credit − width) × 65 − cost`).

| Variant | k = 0 | k = 1 | k = 2 | k = 3 |
|---|---:|---:|---:|---:|
| PT50 P(final ≥ ₹10L) | 94.0 % | **0.3 %** | 0 % | 0 % |
| PT50 P5 final equity | ₹9.72L | **−₹5.82L** | −₹4.88L | −₹4.39L |
| HTE  P(final ≥ ₹10L) | 100 % | **25.3 %** | 0 % | 0 % |
| HTE  P5 final equity | ₹23.45L | **−₹8.17L** | −₹5.99L | −₹4.88L |

One injected max-loss cycle bankrupts PT50 with 99.7 % probability and
HTE with 74.7 % probability. Two injections, both variants go to 0 %.

---

## 10. Capital allocation sweep

Deterministic walk through the 8 cycles at varying deployment fractions:

| deploy % | PT50 final equity | PT50 CAGR | PT50 max DD | HTE final equity | HTE CAGR | HTE max DD |
|---:|---:|---:|---:|---:|---:|---:|
| 10 % | ₹10.61L | +2.7 % | 1.3 % | ₹11.36L | +5.9 % | 0.0 % |
| 20 % | ₹11.24L | +5.4 % | 2.6 % | ₹12.88L | +12.0 % | 0.0 % |
| 30 % | ₹11.90L | +8.1 % | 3.9 % | ₹14.58L | +18.4 % | 0.0 % |
| 50 % | ₹13.29L | +13.6 % | 6.6 % | ₹18.61L | +32.1 % | 0.0 % |
| 100 % | ₹17.07L | +27.0 % | 13.3 % | ₹32.76L | +70.1 % | 0.0 % |

Per-trade Sharpe is invariant across all deployment levels (≈ 1.14 PT50,
≈ 2.92 HTE). Full deployment produces the headline numbers; the more
realistic 10-20 % deployment produces a few-percent-a-year account.

---

## 11. Walk-forward of the **frozen V3 rule** (`falsify_walkforward.csv`)

Rolling 12-month train / 6-month test. No tuning — the same V3 gate is
evaluated on each window.

### PT50

| Train window | Train n | Train Sharpe | Test n | Test win % | Test Sharpe |
|---|---:|---:|---:|---:|---:|
| 2024-01 → 2024-12 | 5 | +1.50 | 2 | 50 % | +0.74 |
| 2024-04 → 2025-03 | 5 | +1.89 | 1 | 0 % | +0.00 |
| 2024-07 → 2025-06 | 4 | +0.37 | 1 | 100 % | +0.00 |
| 2024-10 → 2025-09 | 3 | +1.59 | 1 | 100 % | +0.00 |

### HTE

| Train window | Train n | Train Sharpe | Test n | Test win % | Test Sharpe |
|---|---:|---:|---:|---:|---:|
| 2024-01 → 2024-12 | 5 | +10.02 | 2 | 100 % | +9.66 |
| 2024-04 → 2025-03 | 5 | +10.12 | 1 | 100 % | +0.00 |
| 2024-07 → 2025-06 | 4 | +10.93 | 1 | 100 % | +0.00 |
| 2024-10 → 2025-09 | 3 | +11.58 | 1 | 100 % | +0.00 |

Only one window (2024-full → 2025-H1) has test n ≥ 2. There, HTE's
train/test Sharpes agree (+10.02 / +9.66) — directionally consistent.
All other test rows have n = 1 (Sharpe undefined at 0.00). **Walk-forward
is inconclusive on both variants** because V3 fires too rarely for
6-month windows to produce enough OOS cycles.

---

## 12. Cross-cutting verdict grid

| Test | PT50 | HTE |
|---|:-:|:-:|
| Win rate ≥ 85 % on filtered trades | ❌ 75 % | ✅ 100 % |
| Positive after ₹500/lot slippage | ❌ | ✅ |
| Bootstrap P5 compound ≥ ₹10L | ❌ | ✅ |
| LOO worst-case Sharpe > 0 | ✅ +0.81 | ✅ +2.46 |
| Exit rule beats alternatives | ✅ (#2 of 5 unfiltered) | ❌ (worst of 5 unfiltered) |
| Entry +1-day perturbation survives | ✅ Sharpe 2.44 → 2.30 | ❌ Sharpe 11.07 → −0.28 |
| Tail-loss k=1 survival > 50 % | ❌ 0.3 % | ❌ 25.3 % |
| Walk-forward OOS Sharpe > 0 | Inconclusive (n≤2) | Inconclusive (n≤2) |

---

## 13. What the evidence actually supports

1. **The V3 gate is real enough to filter max losses out of the 2024-2026
   sample.** Unfiltered HTE has a 20 % max-loss rate; V3 matched HTE
   has 0 %. That's a meaningful artefact on 8 cycles, but we cannot
   rule out "V3 is overfit to avoid the four specific HTE blow-ups in
   the sample."

2. **HTE has two load-bearing fragilities**:
   - **Entry-timing**: one-day delay flips the total from +₹9,121 to
     −₹1,677. Retail fills with realistic latency cannot rely on
     catching the first-fire day.
   - **Tail risk**: a single max-loss cycle takes the account from
     ₹10L to ~−₹8L at full deployment, because compounding has grown
     the lot count by then.

3. **PT50 absorbs the entry-timing shock** (Sharpe 2.44 → 2.30) but
   **shares the tail-loss fragility** (survival drops from 94 % to
   0.3 % with one injection). PT50's bank-break margin is mostly
   slippage-driven: at ₹500/lot extra drag it goes from +₹7L to
   −₹57k.

4. **No variant satisfies all robustness criteria simultaneously.**
   The scorecard above is 4/8 green for PT50, 4/8 green for HTE —
   different greens in each case.

5. **Walk-forward cannot yet falsify or validate.** V3's low fire
   rate (~10/yr) against a 2.25-year history means any rolling
   window has 1-2 OOS cycles. We need another 2-3 years of forward
   data, or a filter that fires more often, before walk-forward
   gives a verdict.

---

## 14. What NOT to conclude

- "V3 is a real edge worth ₹10L." The evidence isn't there for
  either variant.
- "V3 is falsified." Only HTE-specific fragilities are documented;
  PT50 survives the perturbation tests it's been exposed to.
- "PT25 would have been better." PT25 wins on the unfiltered
  universe but has not been tested *under the V3 gate*. V3's
  filtering may or may not help PT25; that experiment is not in
  this report.

---

## 15. Recommended next experiments

- **V3 × PT25**: run the exit sweep *after* applying the V3 gate.
  If PT25's headline (Sharpe +0.88 unfiltered) improves further
  under V3, that's a more robust deployable combination than
  HTE. (~5 min of cached backtest.)
- **Longer paper-shadow**: log V3 fires + hypothetical fills for
  3-6 months live. One real out-of-sample quarter at ~3 cycles
  would more than double the OOS sample.
- **Skew data**: cache the call-side chain so `s7_skew` stops
  returning NaN. That's what's keeping the 8-signal scorecard
  capped at 6/8.
- **Event-severity re-weight**: CPI as medium (V1 variant
  direction) may let the raw score actually reach 7/8 or 8/8 on
  real days, which would give V3 a fire count worth walk-forwarding.

---

## 16. Artifact index (all files refreshed in this run)

```
docs/v3-spec-frozen.md
results/nfo/tuned_thresholds.json
results/nfo/redesign_comparison.md + .csv
results/nfo/time_split_report.md                           (manual edits respected)
results/nfo/v3_capital_report_pt50.md + v3_capital_trades_pt50.csv
results/nfo/v3_capital_report_hte.md  + v3_capital_trades_hte.csv
results/nfo/historical_signals.parquet + historical_summary.md
results/nfo/robustness_report.md + 3 CSVs
results/nfo/exit_sweep_trades.csv + exit_sweep_per_trade.csv
results/nfo/entry_perturbation_trades.csv + entry_perturbation_per_trade.csv
results/nfo/falsification_report.md + 3 CSVs
results/nfo/v3_master_analysis.md                          (this file)
```
