# V3 capital-deployment analysis — ₹10L sized per trade

Window: **2024-01-15 → 2026-04-10** (2.23 years).
Starting capital: **₹10.00L**.
Exit variant: **pt50** (50% profit-take).

## Summary

| Metric | Non-compounding | Compounding |
|---|---:|---:|
| Trades taken | 8 of 8 fire-cycles | 8 |
| Wins / losses | 6 / 2 (win-rate 75%) | — |
| Total P&L | **₹6.10L** | **₹7.07L** |
| Final equity | — | **₹17.07L** |
| Return on capital | +61.0% | +70.7% |
| Annualised return | +27.3% | +27.0% |
| Max drawdown (compounding) | — | 13.3% |
| Sharpe (per-trade, annualised) | +1.14 | — |

## Per-trade detail

| V3 first fire | Trade entry | Expiry | Outcome | BP/lot | P&L/lot | Lots (fixed) | P&L (fixed) | Lots (compound) | P&L (compound) | Equity after |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2024-02-22 | 2024-02-22 | 2024-03-28 | profit_take | ₹8,643 | ₹+638 | 115 | ₹73,338 | 115 | ₹73,338 | ₹10.73L |
| 2024-05-03 | 2024-05-03 | 2024-05-30 | profit_take | ₹3,705 | ₹+672 | 269 | ₹1.81L | 289 | ₹1.94L | ₹12.67L |
| 2024-05-23 | 2024-05-23 | 2024-06-27 | profit_take | ₹4,266 | ₹+671 | 234 | ₹1.57L | 297 | ₹1.99L | ₹14.67L |
| 2024-09-06 | 2024-08-22 | 2024-09-26 | managed | ₹8,482 | ₹-1,134 | 117 | -₹1.33L | 172 | -₹1.95L | ₹12.72L |
| 2024-11-22 | 2024-11-21 | 2024-12-26 | expired_worthless | ₹8,170 | ₹+996 | 122 | ₹1.22L | 155 | ₹1.54L | ₹14.26L |
| 2025-01-06 | 2025-01-06 | 2025-01-30 | profit_take | ₹7,244 | ₹+1,536 | 138 | ₹2.12L | 196 | ₹3.01L | ₹17.27L |
| 2025-04-24 | 2025-04-22 | 2025-05-27 | managed | ₹8,268 | ₹-826 | 120 | -₹99,165 | 208 | -₹1.72L | ₹15.55L |
| 2025-10-30 | 2025-10-21 | 2025-11-25 | profit_take | ₹8,312 | ₹+809 | 120 | ₹97,098 | 187 | ₹1.51L | ₹17.07L |

## Interpretation

**Two crucial caveats** to set expectations honestly:

1. **BP per lot ≈ ₹8,500** — a ₹10L allocation runs **~118 lots per trade**. That's    *enormous* leverage on one cycle. A single max-loss event would wipe 40–50% of    equity at that sizing. Retail prudence is **10–20% of capital per trade**, not 100%.

2. **Sample size is 8 trades** — even if the filter is real, 8 trades is too few to    estimate true long-run return. The V3 backtest metrics    (90% win, Sharpe 1.75) could easily degrade to 70% / 0.5 in a different regime.

### What to actually take away

- The **shape of the answer** — not the magnitude — is what matters.
- V3 produces a small number of high-quality trades. Winners outnumber losers, and
  losers (if any) come from specific cycles the filter didn't catch early enough.
- At **realistic retail sizing (1–2 lots)** over these 8 trades, the total P&L is
  roughly 2 × 152k — few lakh over 2 years.

See `results/nfo/v3_capital_trades_pt50.csv` for the raw per-trade data.