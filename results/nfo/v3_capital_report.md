# V3 capital-deployment analysis — ₹10L sized per trade

Window: **2024-01-15 → 2026-01-09** (1.98 years).
Starting capital: **₹10.00L**.
Exit variant: **pt50** (50% profit-take).

## Summary

| Metric | Non-compounding | Compounding |
|---|---:|---:|
| Trades taken | 8 of 8 fire-cycles | 8 |
| Wins / losses | 7 / 1 (win-rate 88%) | — |
| Total P&L | **₹8.71L** | **₹12.29L** |
| Final equity | — | **₹22.29L** |
| Return on capital | +87.1% | +122.9% |
| Annualised return | +43.9% | +49.8% |
| Max drawdown (compounding) | — | 8.2% |
| Sharpe (per-trade, annualised) | +2.35 | — |

## Per-trade detail

| V3 first fire | Trade entry | Expiry | Outcome | BP/lot | P&L/lot | Lots (fixed) | P&L (fixed) | Lots (compound) | P&L (compound) | Equity after |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2024-02-22 | 2024-02-22 | 2024-03-28 | profit_take | ₹8,643 | ₹+738 | 115 | ₹84,841 | 115 | ₹84,841 | ₹10.85L |
| 2024-05-03 | 2024-05-03 | 2024-05-30 | profit_take | ₹7,410 | ₹+793 | 134 | ₹1.06L | 146 | ₹1.16L | ₹12.01L |
| 2024-05-23 | 2024-05-23 | 2024-06-27 | profit_take | ₹8,531 | ₹+796 | 117 | ₹93,161 | 140 | ₹1.11L | ₹13.12L |
| 2024-09-06 | 2024-08-22 | 2024-09-26 | profit_take | ₹8,482 | ₹+1,732 | 117 | ₹2.03L | 154 | ₹2.67L | ₹15.79L |
| 2024-11-22 | 2024-11-21 | 2024-12-26 | expired_worthless | ₹8,170 | ₹+1,053 | 122 | ₹1.28L | 193 | ₹2.03L | ₹17.82L |
| 2025-01-06 | 2025-01-06 | 2025-01-30 | profit_take | ₹7,244 | ₹+1,658 | 138 | ₹2.29L | 246 | ₹4.08L | ₹21.90L |
| 2025-04-24 | 2025-04-22 | 2025-05-27 | managed | ₹8,268 | ₹-682 | 120 | -₹81,900 | 264 | -₹1.80L | ₹20.10L |
| 2025-10-30 | 2025-10-21 | 2025-11-25 | profit_take | ₹8,312 | ₹+910 | 120 | ₹1.09L | 241 | ₹2.19L | ₹22.29L |

## Interpretation

**Two crucial caveats** to set expectations honestly:

1. **BP per lot ≈ ₹8,500** — a ₹10L allocation runs **~118 lots per trade**. That's    *enormous* leverage on one cycle. A single max-loss event would wipe 40–50% of    equity at that sizing. Retail prudence is **10–20% of capital per trade**, not 100%.

2. **Sample size is 8 trades** — even if the filter is real, 8 trades is too few to    estimate true long-run return. The V3 backtest metrics    (90% win, Sharpe 1.75) could easily degrade to 70% / 0.5 in a different regime.

### What to actually take away

- The **shape of the answer** — not the magnitude — is what matters.
- V3 produces a small number of high-quality trades. Winners outnumber losers, and
  losers (if any) come from specific cycles the filter didn't catch early enough.
- At **realistic retail sizing (1–2 lots)** over these 8 trades, the total P&L is
  roughly 2 × 218k — few lakh over 2 years.

See `results/nfo/v3_capital_trades.csv` for the raw per-trade data.