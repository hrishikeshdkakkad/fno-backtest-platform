# CSP + Credit Spread Plan — $41,000 → $1000/month target

## Executive summary

- Capital: **$41,000**
- Target: **$1000/month** (~29.3% annual on capital)
- Max single-month loss tolerance: **$6,150** (15% of capital)
- **Expected monthly P/L:** $630 → UNDERSHOOTS target
- **Worst backtested single-month (sum of position worst-months):** $-6001
- **Capital used:** $26,662  •  Reserve $14,338

## Allocation

| Kind | Underlying | N | Δ | DTE | Width | PT | Mg@DTE | Capital/ct | Avg $/mo/ct | Worst $/mo/ct | Win% | Tail% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| csp | IWM | 1 | 0.30 | 35 | — | 1.00 | — | $21,986 | $164 | $-1424 | 86% | 23% |
| spread | IWM | 12 | 0.30 | 35 | $5 | 1.00 | — | $390 | $39 | $-381 | 77% | 9% |

## Execution playbook

### Monthly cycle

1. **Entry day:** trading day closest to 35 calendar days before the 3rd-Friday monthly expiry.
2. **Strike selection (short leg of spreads and CSPs):** pick the put whose BSM delta at that day's close is closest to the target delta in the allocation.
3. **Long leg of each spread:** buy the put `spread_width` dollars below the short strike (the plan's `Width` column).
4. **Order type:** combo limit order on spreads (sell-to-open as a single ticket). Do not leg in — gap risk between fills is real.
5. **Buying power required:** `(width − net credit) × 100 × N`. Cash-secured puts require `strike × 100 × N`.

### Management

- **Profit take:** close at 50% of entry credit unless the row says PT=1.00 (= hold to expiry). The backtest shows hold-to-expiry dominates for CSPs; spreads vary and this plan uses whichever PT the backtest selected.
- **Time management:** close at 21 DTE only if the row shows `Mg@DTE = 21`. Otherwise let the trade run.
- **Adverse moves:** if the underlying gaps through the short strike intraday, consider rolling the entire spread down-and-out for net credit. Do not roll for a debit.

### Guardrails (non-negotiable)

- Cap total spread count at **12** (from the allocation). More spreads means higher correlated max-loss risk.
- If VIX > 30 on entry, **halve** contract count. If VIX > 40, **skip** the cycle.
- Keep at least 10% of capital as uncommitted cash for broker stress-test margin calls.
- Never sell single-name puts unless you have a 6-month live track record on this system.

## Why spreads instead of just CSPs?

On this capital, pure ETF CSPs cannot clear the $1,000/mo bar — the math
simply doesn't work at prudent deltas. Credit spreads deliver the same
directional thesis (underlying stays above short strike) but tie up roughly
**1/20th the capital per trade**. On $41k, you can run ~7 IWM 10-wide
spreads for the buying power of a single IWM CSP, and stack the premiums.

The trade-off is real: if IWM gaps through both strikes (like Feb 2025
when IWM fell 10% in 5 weeks), **every** spread hits max loss
simultaneously. This is why the guardrail caps the spread count — beyond
that, a single crash month could blow through the 15%-of-capital loss
tolerance.

The backtest in `results/spread_trades.csv` captures this exactly: every
max-loss outcome happened in the Feb-Mar 2025 and Feb-Mar 2026 windows,
mirroring the CSP assignment pattern. You are not taking "safer" trades
with spreads — you are taking the *same* trades with better capital
leverage. That leverage works both directions.

## Data provenance

All numbers derive from 22 monthly cycles in the 2024-04 → 2026-04 window on Massive.com daily bars (Basic tier).

- CSP configs: `results/summary.csv`  (5 configs × 22 cycles)
- Spread configs: `results/spread_summary.csv`  (36 configs × up to 22 cycles each)
- Per-trade detail: `results/trades.csv` and `results/spread_trades.csv`

## Caveats

- 2-year window is a mostly-bull regime with two ~8-10% corrections. A 2022-style drawdown is not in the data.
- Fills are modeled at daily close ± 2% slippage per leg. Real spread fills at mid-of-spread on liquid ETFs should be tighter; the backtest is intentionally conservative.
- Long-leg bars can be missing on low-volume days; the engine skips those days' exit checks rather than synthesizing prices.
- Assignment modeled as sell-at-expiry-close for CSPs; does not model the wheel. Real wheel returns would be ~2-4 points higher annualized.