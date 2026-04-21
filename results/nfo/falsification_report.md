# V3 falsification report

Spec version: `v3-spec-frozen-2026-04-20` (see `docs/v3-spec-frozen.md`).
Window span: **2.23** years. Capital: **₹10.00L**.
- PT50: **8** V3-matched trades
- HTE: **8** V3-matched trades

## Scope caveat

Every result below is reported per exit variant (PT50, HTE) because they produce materially different robustness profiles. HTE in particular is entry-timing fragile and bankrupt-on-tail, while PT50 absorbs most of the same shocks. A conclusion stated for "V3" without a variant qualifier is not supported by the data in this report.

## 1. Tail-loss injection

Each draw resamples the 8-cycle V3 matched set with replacement, then replaces `n_injected` random rows with a synthetic max-loss cycle (`pnl_contract = (net_credit − width) × 65 − cost`). This tells us how many of our observed wins would have to flip into full max losses before the compound ₹10L account finishes below its starting balance.

### PT50

| # injected | P(final ≥ ₹10L) | P5 final equity | P50 | P95 | P95 max DD |
|---:|---:|---:|---:|---:|---:|
| 0 | 94.0% | ₹9.72L | ₹17.47L | ₹28.66L | 32.3% |
| 1 | 0.3% | -₹5.82L | ₹4.66L | ₹7.99L | 100.0% |
| 2 | 0.0% | -₹4.88L | ₹1.08L | ₹2.28L | 100.0% |
| 3 | 0.0% | -₹4.39L | -₹47,087 | ₹66,848 | 100.0% |

### HTE

| # injected | P(final ≥ ₹10L) | P5 final equity | P50 | P95 | P95 max DD |
|---:|---:|---:|---:|---:|---:|
| 0 | 100.0% | ₹23.45L | ₹32.15L | ₹49.24L | 0.0% |
| 1 | 25.3% | -₹8.17L | ₹8.27L | ₹13.12L | 100.0% |
| 2 | 0.0% | -₹5.99L | ₹2.08L | ₹3.47L | 100.0% |
| 3 | 0.0% | -₹4.88L | -₹64,376 | ₹96,413 | 100.0% |

## 2. Capital allocation sweep (deterministic)

No resampling — runs the actual observed matched trades through the equity simulator at each deployment fraction. 100 % matches the existing `v3_capital_analysis` headline; lower fractions hold reserve capital.

### PT50

| deploy % | final equity (compound) | CAGR | max DD | Sharpe |
|---:|---:|---:|---:|---:|
| 10% | ₹10.61L | +2.7% | 1.3% | +1.15 |
| 20% | ₹11.24L | +5.4% | 2.6% | +1.14 |
| 30% | ₹11.90L | +8.1% | 3.9% | +1.13 |
| 50% | ₹13.29L | +13.6% | 6.6% | +1.14 |
| 100% | ₹17.07L | +27.0% | 13.3% | +1.14 |

### HTE

| deploy % | final equity (compound) | CAGR | max DD | Sharpe |
|---:|---:|---:|---:|---:|
| 10% | ₹11.36L | +5.9% | 0.0% | +2.94 |
| 20% | ₹12.88L | +12.0% | 0.0% | +2.93 |
| 30% | ₹14.58L | +18.4% | 0.0% | +2.92 |
| 50% | ₹18.61L | +32.1% | 0.0% | +2.91 |
| 100% | ₹32.76L | +70.1% | 0.0% | +2.91 |

## 3. Exit sweep (PT25 / PT50 / PT75 / HTE / DTE=2)

| exit rule | trades | win% | avg PnL | Sharpe | max_loss% |
|---|---:|---:|---:|---:|---:|
| PT25 | 20 | 85% | ₹131 | +0.88 | 0.0% |
| PT50 | 20 | 75% | ₹153 | +0.78 | 0.0% |
| PT75 | 20 | 60% | -₹105 | -0.39 | 0.0% |
| HTE | 20 | 75% | -₹895 | -1.08 | 20.0% |
| DTE2 | 20 | 65% | -₹526 | -0.75 | 10.0% |

## 4. Walk-forward tuning

Rolling 12-month train / 6-month test. The **frozen V3 rule** is applied to each window separately — we're not tuning thresholds, we're asking whether the same specific-pass gate that fires on the train period also produces positive results in the following 6 months. Train and test Sharpe use the per-lot convention from `calibrate.summary_stats` (√252 annualisation). `—` means V3 did not fire in that window.

### PT50

| train window | test window | train n | train win% | train Sharpe | test n | test win% | test Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| 2024-01-01 → 2024-12-31 | 2025-01-01 → 2025-06-30 | 5 | 80% | +1.50 | 2 | 50% | +0.74 |
| 2024-04-01 → 2025-03-31 | 2025-04-01 → 2025-09-30 | 5 | 80% | +1.89 | 1 | 0% | +0.00 |
| 2024-07-01 → 2025-06-30 | 2025-07-01 → 2025-12-31 | 4 | 50% | +0.37 | 1 | 100% | +0.00 |
| 2024-10-01 → 2025-09-30 | 2025-10-01 → 2026-03-31 | 3 | 67% | +1.59 | 1 | 100% | +0.00 |

### HTE

| train window | test window | train n | train win% | train Sharpe | test n | test win% | test Sharpe |
|---|---|---:|---:|---:|---:|---:|---:|
| 2024-01-01 → 2024-12-31 | 2025-01-01 → 2025-06-30 | 5 | 100% | +10.02 | 2 | 100% | +9.66 |
| 2024-04-01 → 2025-03-31 | 2025-04-01 → 2025-09-30 | 5 | 100% | +10.12 | 1 | 100% | +0.00 |
| 2024-07-01 → 2025-06-30 | 2025-07-01 → 2025-12-31 | 4 | 100% | +10.93 | 1 | 100% | +0.00 |
| 2024-10-01 → 2025-09-30 | 2025-10-01 → 2026-03-31 | 3 | 100% | +11.58 | 1 | 100% | +0.00 |


## 5. Entry perturbation

### PT50

| entry timing | trades | win% | avg PnL | total | Sharpe |
|---|---:|---:|---:|---:|---:|
| first_fire | 8 | 75% | ₹472 | ₹3,776 | +2.44 |
| plus_one_day | 8 | 75% | ₹443 | ₹3,544 | +2.30 |
| worst_of_two | 8 | 62% | ₹178 | ₹1,423 | +1.07 |

### HTE

| entry timing | trades | win% | avg PnL | total | Sharpe |
|---|---:|---:|---:|---:|---:|
| first_fire | 8 | 100% | ₹1,140 | ₹9,121 | +11.07 |
| plus_one_day | 8 | 75% | -₹210 | -₹1,677 | -0.28 |
| worst_of_two | 8 | 75% | -₹228 | -₹1,822 | -0.31 |

