# V3 capital-deployment analysis — ₹10L sized per trade

Window: **2024-01-15 → 2026-04-10** (2.23 years).
Starting capital: **₹10.00L**.
Exit variant: **hte** (hold to expiry).

## Summary

| Metric | Non-compounding | Compounding |
|---|---:|---:|
| Trades taken | 8 of 8 fire-cycles | 8 |
| Wins / losses | 8 / 0 (win-rate 100%) | — |
| Total P&L | **₹13.08L** | **₹22.76L** |
| Final equity | — | **₹32.76L** |
| Return on capital | +130.8% | +227.6% |
| Annualised return | +58.5% | +70.1% |
| Max drawdown (compounding) | — | 0.0% |
| Sharpe (per-trade, annualised) | +2.91 | — |

## Per-trade detail

| V3 first fire | Trade entry | Expiry | Outcome | BP/lot | P&L/lot | Lots (fixed) | P&L (fixed) | Lots (compound) | P&L (compound) | Equity after |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2024-02-22 | 2024-02-22 | 2024-03-28 | expired_worthless | ₹8,643 | ₹+685 | 115 | ₹78,767 | 115 | ₹78,767 | ₹10.79L |
| 2024-05-03 | 2024-05-03 | 2024-05-30 | expired_worthless | ₹3,705 | ₹+1,491 | 269 | ₹4.01L | 291 | ₹4.34L | ₹15.13L |
| 2024-05-23 | 2024-05-23 | 2024-06-27 | expired_worthless | ₹4,266 | ₹+759 | 234 | ₹1.78L | 354 | ₹2.69L | ₹17.81L |
| 2024-09-06 | 2024-08-22 | 2024-09-26 | expired_worthless | ₹8,482 | ₹+793 | 117 | ₹92,746 | 210 | ₹1.66L | ₹19.48L |
| 2024-11-22 | 2024-11-21 | 2024-12-26 | expired_worthless | ₹8,170 | ₹+996 | 122 | ₹1.22L | 238 | ₹2.37L | ₹21.85L |
| 2025-01-06 | 2025-01-06 | 2025-01-30 | partial_loss | ₹7,244 | ₹+1,564 | 138 | ₹2.16L | 301 | ₹4.71L | ₹26.56L |
| 2025-04-24 | 2025-04-22 | 2025-05-27 | expired_worthless | ₹8,268 | ₹+931 | 120 | ₹1.12L | 321 | ₹2.99L | ₹29.55L |
| 2025-10-30 | 2025-10-21 | 2025-11-25 | expired_worthless | ₹8,312 | ₹+906 | 120 | ₹1.09L | 355 | ₹3.22L | ₹32.76L |

## Interpretation

**Two crucial caveats** to set expectations honestly:

1. **BP per lot ≈ ₹8,500** — a ₹10L allocation runs **~118 lots per trade**. That's    *enormous* leverage on one cycle. A single max-loss event would wipe 40–50% of    equity at that sizing. Retail prudence is **10–20% of capital per trade**, not 100%.

2. **Sample size is 8 trades** — even if the filter is real, 8 trades is too few to    estimate true long-run return. The V3 backtest metrics    (90% win, Sharpe 1.75) could easily degrade to 70% / 0.5 in a different regime.

### What to actually take away

- The **shape of the answer** — not the magnitude — is what matters.
- V3 produces a small number of high-quality trades. Winners outnumber losers, and
  losers (if any) come from specific cycles the filter didn't catch early enough.
- At **realistic retail sizing (1–2 lots)** over these 8 trades, the total P&L is
  roughly 2 × 327k — few lakh over 2 years.

See `results/nfo/v3_capital_trades_hte.csv` for the raw per-trade data.