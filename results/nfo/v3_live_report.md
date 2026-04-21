# V3 live-rule backtest

Entry date is forced to the first V3 firing session (or the next NSE trading
day if the fire lands off-session). This removes the look-ahead bias present
in the canonical 35-DTE-grid capital report — four of the eight V3 cycles
had their canonical entry *before* V3 fired, which is information a live
system cannot use.

Window: 2024-01-15 → 2026-04-10 (2.23 years).
Capital: ₹10.00L per cycle.

## PT50

- Trades: **8**
- Win rate: **75%**
- Total per-lot P&L: **₹58** (per-contract)   (canonical look-ahead total per-lot: ₹3,362; delta vs live rule: -₹3,304)
- Final equity (compound ₹10L): **₹18.20L**
- CAGR (compound): **+30.7%**
- Max DD: **9.1%**
- Per-trade Sharpe (capital-aware): **+1.52**

| cycle first-fire | entry used | expiry | outcome | per-lot P&L |
|---|---|---|---|---:|
| 2024-02-22 | 2024-02-22 | 2024-03-28 | profit_take | ₹10 |
| 2024-05-03 | 2024-05-03 | 2024-05-30 | profit_take | ₹10 |
| 2024-05-23 | 2024-05-23 | 2024-06-27 | profit_take | ₹10 |
| 2024-09-06 | 2024-09-06 | 2024-09-26 | managed | -₹2 |
| 2024-11-22 | 2024-11-22 | 2024-12-26 | profit_take | ₹7 |
| 2025-01-06 | 2025-01-06 | 2025-01-30 | profit_take | ₹24 |
| 2025-04-24 | 2025-04-24 | 2025-05-27 | managed | -₹11 |
| 2025-10-30 | 2025-10-30 | 2025-11-25 | profit_take | ₹10 |

## HTE

- Trades: **8**
- Win rate: **100%**
- Total per-lot P&L: **₹140** (per-contract)   (canonical look-ahead total per-lot: ₹8,125; delta vs live rule: -₹7,985)
- Final equity (compound ₹10L): **₹37.45L**
- CAGR (compound): **+80.6%**
- Max DD: **0.0%**
- Per-trade Sharpe (capital-aware): **+3.44**

| cycle first-fire | entry used | expiry | outcome | per-lot P&L |
|---|---|---|---|---:|
| 2024-02-22 | 2024-02-22 | 2024-03-28 | expired_worthless | ₹11 |
| 2024-05-03 | 2024-05-03 | 2024-05-30 | expired_worthless | ₹23 |
| 2024-05-23 | 2024-05-23 | 2024-06-27 | expired_worthless | ₹12 |
| 2024-09-06 | 2024-09-06 | 2024-09-26 | expired_worthless | ₹23 |
| 2024-11-22 | 2024-11-22 | 2024-12-26 | expired_worthless | ₹14 |
| 2025-01-06 | 2025-01-06 | 2025-01-30 | partial_loss | ₹24 |
| 2025-04-24 | 2025-04-24 | 2025-05-27 | expired_worthless | ₹15 |
| 2025-10-30 | 2025-10-30 | 2025-11-25 | expired_worthless | ₹20 |
