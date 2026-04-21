# Time-split validation — V3 and siblings

Split date: **2025-01-01**.

- **Train** = trades entered before the split date.
- **Test**  = trades entered on or after.
- **Full**  = both combined (what `redesign_variants.py` reports).

A variant is **robust** if train and test metrics agree within reasonable tolerance (win-rate within ±15 pp, Sharpe directionally consistent, non-zero fire count in both).

## V3 — V2 + specific-pass gate (IV-RV + trend + event + ≥1 of VIX/IV-rank)

| Split | Fires | Fires/yr | Trades | Win% | Sharpe | MaxLoss% |
|---|---:|---:|---:|---:|---:|---:|
| Full | 23 | 10.44 | 10 | 70% | -0.35 | 0.0% |
| Train | 16 | 16.87 | 8 | 88% | +1.65 | 0.0% |
| Test | 7 | 5.58 | 2 | 0% | -2.48 | 0.0% |

**Inconclusive** — test-set has only 2 matched trade(s); need ≥ 10 for a meaningful OOS verdict. (observed: train 88% win / test 0% win)

## V4 — V3 + tuned thresholds (vix_rich=22, pullback_atr=1.5)

| Split | Fires | Fires/yr | Trades | Win% | Sharpe | MaxLoss% |
|---|---:|---:|---:|---:|---:|---:|
| Full | 23 | 10.44 | 10 | 70% | -0.35 | 0.0% |
| Train | 16 | 16.87 | 8 | 88% | +1.65 | 0.0% |
| Test | 7 | 5.58 | 2 | 0% | -2.48 | 0.0% |

**Inconclusive** — test-set has only 2 matched trade(s); need ≥ 10 for a meaningful OOS verdict. (observed: train 88% win / test 0% win)

## V5 — V4 + relaxed grade (score ≥ 3 of 7, keep specific gate)

| Split | Fires | Fires/yr | Trades | Win% | Sharpe | MaxLoss% |
|---|---:|---:|---:|---:|---:|---:|
| Full | 23 | 10.44 | 10 | 70% | -0.35 | 0.0% |
| Train | 16 | 16.87 | 8 | 88% | +1.65 | 0.0% |
| Test | 7 | 5.58 | 2 | 0% | -2.48 | 0.0% |

**Inconclusive** — test-set has only 2 matched trade(s); need ≥ 10 for a meaningful OOS verdict. (observed: train 88% win / test 0% win)

## V6 — V4 minus specific-pass gate — broadest variant that kept tuned thresholds

| Split | Fires | Fires/yr | Trades | Win% | Sharpe | MaxLoss% |
|---|---:|---:|---:|---:|---:|---:|
| Full | 307 | 139.39 | 58 | 74% | -0.65 | 8.6% |
| Train | 120 | 126.53 | 18 | 83% | -0.22 | 5.6% |
| Test | 187 | 149.13 | 40 | 70% | -0.84 | 10.0% |

**Holds up** — train/test win-rate 83% / 70%, Sharpe -0.22 / -0.84.
