# V3 robustness report

Window span: **2.23** years. Capital per trade: **₹10.00L**. Bootstrap iterations: **10,000**.

- PT50: **8** V3-matched trades
- HTE: **8** V3-matched trades

## 1. Slippage sweep — break-even analysis

Extra round-trip slippage is applied as a flat ₹/lot reduction to `pnl_contract`. Compound P&L uses the standard `v3_capital_analysis` sizing (deploy running equity, integer lots).

| ₹/lot slip | PT50 compound P&L | PT50 positive? | HTE compound P&L | HTE positive? |
|---:|---:|:-:|---:|:-:|
| 0 | ₹7.07L | ✓ | ₹22.76L | ✓ |
| 100 | ₹5.21L | ✓ | ₹19.49L | ✓ |
| 250 | ₹2.75L | ✓ | ₹15.10L | ✓ |
| 500 | -₹56,813 | ✗ | ₹9.04L | ✓ |
| 750 | -₹3.12L | ✗ | ₹4.29L | ✓ |
| 1000 | -₹5.06L | ✗ | ₹61,189 | ✓ |

- PT50: break-even slippage ≈ **₹457/lot** round-trip (linear interpolation between adjacent grid rows).
- HTE: does not cross zero inside the tested grid.

## 2. Leave-one-out — single-cycle dependency

Each row drops one V3-matched trade, recomputes Sharpe / win-rate / total-P&L on the remaining set, and records the impact. The 'worst-case LOO' is the cycle whose removal hurts the headline most.

### PT50

| dropped expiry | outcome | dropped P&L | remaining win% | remaining Sharpe (₹10L) | remaining total (fixed size) |
|---|---|---:|---:|---:|---:|
| 2025-01-30 | profit_take | ₹1,536 | 71% | +0.81 | ₹3.98L |
| 2024-05-30 | profit_take | ₹672 | 71% | +0.84 | ₹4.29L |
| 2024-06-27 | profit_take | ₹671 | 71% | +0.86 | ₹4.53L |
| 2024-12-26 | expired_worthless | ₹996 | 71% | +0.91 | ₹4.88L |
| 2025-11-25 | profit_take | ₹809 | 71% | +0.95 | ₹5.13L |
| 2024-03-28 | profit_take | ₹638 | 71% | +0.99 | ₹5.37L |
| 2025-05-27 | managed | -₹826 | 86% | +1.58 | ₹7.09L |
| 2024-09-26 | managed | -₹1,134 | 86% | +1.83 | ₹7.43L |

- Worst-Sharpe drop (removing **2025-01-30**): remaining Sharpe +0.81, total ₹3.98L.
- Worst-total drop (removing **2025-01-30**): remaining Sharpe +0.81, total ₹3.98L. If worst-Sharpe and worst-total disagree, the edge sits across multiple cycles — each criterion stresses a different cycle.

### HTE

| dropped expiry | outcome | dropped P&L | remaining win% | remaining Sharpe (₹10L) | remaining total (fixed size) |
|---|---|---:|---:|---:|---:|
| 2025-01-30 | partial_loss | ₹1,564 | 100% | +2.46 | ₹10.92L |
| 2024-06-27 | expired_worthless | ₹759 | 100% | +2.49 | ₹11.30L |
| 2024-12-26 | expired_worthless | ₹996 | 100% | +2.65 | ₹11.87L |
| 2025-05-27 | expired_worthless | ₹931 | 100% | +2.69 | ₹11.96L |
| 2025-11-25 | expired_worthless | ₹906 | 100% | +2.70 | ₹11.99L |
| 2024-09-26 | expired_worthless | ₹793 | 100% | +2.78 | ₹12.15L |
| 2024-03-28 | expired_worthless | ₹685 | 100% | +2.86 | ₹12.29L |
| 2024-05-30 | expired_worthless | ₹1,491 | 100% | +4.67 | ₹9.07L |

- Worst-Sharpe drop (removing **2025-01-30**): remaining Sharpe +2.46, total ₹10.92L.
- Worst-total drop (removing **2024-05-30**): remaining Sharpe +4.67, total ₹9.07L. If worst-Sharpe and worst-total disagree, the edge sits across multiple cycles — each criterion stresses a different cycle.

## 3. Block bootstrap — resampling V3 cycles

Each iteration resamples V3's matched cycles with replacement (one row = one cycle), walks them through the equity simulator, and records total P&L / compounding CAGR / max drawdown. 10,000 iterations, seed recorded in CSV.

### PT50  (P(compound final equity > ₹10L) = **94.0%**, P(fixed-size total P&L > 0) = 95.8%)

| percentile | total P&L (fixed) | final equity (compound) | CAGR compound | max DD |
|---:|---:|---:|---:|---:|
| P5 | ₹40,185 | ₹9.72L | -1.3% | 0.0% |
| P25 | ₹3.88L | ₹13.74L | +15.3% | 10.0% |
| P50 | ₹6.34L | ₹17.47L | +28.4% | 13.3% |
| P75 | ₹8.57L | ₹21.75L | +41.6% | 21.9% |
| P95 | ₹11.40L | ₹28.66L | +60.2% | 32.3% |

### HTE  (P(compound final equity > ₹10L) = **100.0%**, P(fixed-size total P&L > 0) = 100.0%)

| percentile | total P&L (fixed) | final equity (compound) | CAGR compound | max DD |
|---:|---:|---:|---:|---:|
| P5 | ₹9.02L | ₹23.45L | +46.4% | 0.0% |
| P25 | ₹11.03L | ₹27.77L | +58.0% | 0.0% |
| P50 | ₹12.84L | ₹32.15L | +68.7% | 0.0% |
| P75 | ₹14.90L | ₹37.93L | +81.6% | 0.0% |
| P95 | ₹18.16L | ₹49.24L | +104.1% | 0.0% |

## Verdict against the five trust criteria

- ❌ PT50 positive after ₹500/lot extra slippage (compound P&L)
- ✅ HTE positive after ₹500/lot extra slippage (compound P&L)
- ✅ PT50 remains positive ₹10L Sharpe after LOO worst case (worst Sharpe +0.81)
- ✅ HTE remains positive ₹10L Sharpe after LOO worst case (worst Sharpe +2.46)
- ❌ PT50 bootstrap P5 final equity above starting ₹10L
- ✅ HTE bootstrap P5 final equity above starting ₹10L
- ⏳ Tail-loss injection, walk-forward, regime-bucket slicing — phase 2
