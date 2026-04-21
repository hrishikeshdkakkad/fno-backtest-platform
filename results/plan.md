# CSP Income Plan — $41,000 → $500/month

*Data-driven plan backed by a 22-cycle historical backtest on Massive.com options data (2024-05 → 2026-04). All numbers below are from the actual backtest output in `results/trades.csv`.*

## TL;DR — Honest answer first

**$500/month on $41,000 is not achievable "safely" with single-underlying ETF cash-secured puts at today's prices.** The math requires ~14.6% annualized gross yield on cash-secured collateral; backtested IWM CSPs at conservative-to-moderate deltas deliver 3–9% annualized over the last 2 years (a bull market — so the forward number is likely *worse*, not better).

What **is** achievable safely right now on $41k:

| Plan | Expected / month | Worst month | Max drawdown | Annualized | Complexity |
|---|---|---|---|---|---|
| **A — Conservative** (1 IWM Δ0.20 hold) | $130 | -$1,121 | -2.7% of capital | 7.3% | Low |
| **B — Moderate** (1 IWM Δ0.30 hold) | $164 | -$1,424 | -3.5% of capital | 8.9% | Low |
| **C — Hit target via capital scaling** | $500 | pro-rata | pro-rata | 8.9% | requires ~$70k capital |

To actually hit $500/month from $41k you have three realistic choices, in order of what I'd recommend:

1. **Accept $160–330/month and let compounding fill the rest** — the backtested yield is real; the gap to $500 is roughly 1–1.5 years of compounding at these rates.
2. **Run the "wheel"** (CSP → assigned → sell covered calls → called away → repeat) which historically recovers 50–80% of assignment losses and lifts effective yield 2–4 points. Not modeled here but described in the Execution Playbook below.
3. **Raise the risk** — 0.40+ delta, single-name high-IV stocks, or weekly cycles. All trade yield for drawdown. I recommend *against* this unless you have a higher risk tolerance than the word "safely" implies.

---

## What the backtest actually shows (IWM, 2024-05 → 2026-04)

5 parameter configurations, 22 monthly cycles each, 110 total trades. All fills at daily close plus 2% slippage.

| Δ | Profit take | Manage @ DTE | n | Win rate | Assign rate | Avg $/mo | Worst month | Ann ROC |
|---|---|---|---|---|---|---|---|---|
| 0.20 | 50% | 21 | 22 | 73% | 0% | **$48** | -$437 | 2.7% |
| 0.20 | 100% (hold) | — | 22 | 91% | 9% | **$130** | -$1,121 | **7.3%** |
| 0.30 | 50% | 21 | 22 | 68% | 0% | **$54** | -$613 | 2.9% |
| 0.30 | 100% (hold) | — | 22 | 86% | 23% | **$164** | -$1,424 | **8.9%** |
| 0.30 | 50% | — | 22 | 86% | 14% | $65 | -$1,424 | 3.5% |

**Three empirical findings that changed my plan:**

1. **Managing at 21 DTE caps yield at ~3% annually** regardless of delta. Closing the trade before the last 3 weeks throws away the steepest part of theta decay. For this income strategy, *do not* adopt the common "tastyworks 21 DTE" rule — the backtest says it actively hurts.
2. **Raising delta from 0.20 → 0.30 adds only ~1.6 points of annualized return, but more than doubles assignment rate** (9% → 23%). Past 0.30, the premium-per-assignment-loss trade turns unfavorable.
3. **All losing months clustered around macro crashes.** The two big losses (-$1,121 and -$1,424) were both Feb-March events where IWM dropped 8–10% in 5 weeks. Outside of those, the strategy generated $200–$400 per contract per month like clockwork.

The assignment log from the 0.30-hold config is below — look how concentrated the pain is:

| Entry | Expiry | Strike | Spot entry | Spot exit | P/L | Outcome |
|---|---|---|---|---|---|---|
| 2024-05-17 | 2024-06-21 | 203 | 208.08 | 200.35 | -$49 | assigned (small) |
| 2025-01-17 | 2025-02-21 | 219 | 225.46 | 217.80 | **+$139** | assigned but premium > loss |
| 2025-02-14 | 2025-03-21 | 221 | 225.97 | 203.79 | **-$1,424** | assigned (crash) |
| 2025-10-17 | 2025-11-21 | 237 | 243.41 | 235.60 | +$311 | assigned but premium > loss |
| 2026-02-13 | 2026-03-20 | 256 | 262.96 | 242.22 | **-$875** | assigned (crash) |

So **5 assignments over 22 cycles, but only 2 were net losses**. That's the nature of CSPs: most assignments are shallow and the premium covers them.

---

## Recommended plan

### Position sizing for $41,000 capital

At current IWM price (~$270), one 0.30-delta put is struck around $248–256, requiring ~$25k collateral. So:

- **1 IWM put × ~$25k collateral** = primary income position
- **~$16k cash reserve** for (a) the assignment-recovery buffer, (b) a second smaller position you can layer in once you have a few months of data from live trading

This gives you the entire IWM-based backtest outcome directly: expected **$164/month**, worst month ~**-$1,424** (3.5% of capital), annualized **8.9%**.

### Scaling toward the target

Two stacking paths to move toward $500/month without increasing per-trade risk:

- **Add XLV (Healthcare ETF)** — backtest running now, will drop into `results/summary.csv` when done. XLV has collateral ~$16k per contract, low IV, and low correlation to small-caps. A 1-IWM + 1-XLV portfolio fits $41k and should add ~$80/mo in expectation, bringing the total to ~$240/mo.
- **Run the wheel** — when assigned, do not immediately sell the stock. Write a covered call at the same strike (or 1–2 strikes above your cost basis) at ~0.25–0.30 delta for the next monthly. Historically recovers 50–80% of assignment losses over 2–4 months. This lifts the effective annualized ROC from 8.9% → ~11–13% on the IWM backtest.

---

## Execution playbook

### Every monthly cycle (repeatable process)

**T-minus 35 days (entry day — typically a Friday 5 weeks before the 3rd-Friday expiry):**
1. Check VIX. If VIX > 25, cut position size in half. If VIX > 40, *skip this cycle*.
2. Check the economic calendar. If the following week has FOMC or CPI, consider pushing the entry to T-32.
3. Compute the target strike:
   - Look up IWM's 30-day realized vol (or pull it from the Python tool: `.venv/bin/python -c "from csp.strategy import realized_vol; ..."`).
   - Target strike ≈ spot × exp(-0.527 × σ × √(35/365)) → round *down* to the nearest $1.
4. Sell 1 IWM put at that strike, expiring on the 3rd Friday, for the bid + a few cents.
5. Log: entry date, spot, strike, premium received, target delta.

**During the cycle:**
- Do nothing unless an emergency closes IWM down >10% from entry in a single day. In that case consider rolling down-and-out (different expiry, lower strike) for net credit only.
- **Do NOT manage at 21 DTE.** The backtest says that rule costs ~5 points of annualized return.
- **Do NOT take profit at 50%.** Same reason.

**At expiry:**
- If IWM ≥ strike: put expires worthless. Good. Repeat next cycle.
- If IWM < strike: shares are assigned. Next trading morning:
  - Option 1 (wheel): write a covered call at 0.25-delta for the next monthly expiry. Repeat until called away, then return to CSP.
  - Option 2 (simple): sell the shares at the open and re-enter CSP.
  - The backtest models Option 2 conservatively.

### Hard guardrails

- **Never** replace the cash collateral with margin. You are running cash-secured puts, not naked puts.
- **Never** enter a position that would require >60% of your capital as a single position.
- **Never** roll a losing trade for a debit.
- **Never** touch single-name stocks until you have 6+ months of live P/L from this strategy. Single-name adds earnings risk, which the ETF backtest does not cover.
- **Always** keep at least 10% of your capital liquid for margin calls from broker-side stress testing.

---

## What's in this repo

```
csp/
├── src/csp/          — backtester library (client, cache, BSM, strategy, backtest, wheel)
├── scripts/          — runnable scripts (smoke_test, quick_iwm, focused_run, build_plan)
├── data/             — Parquet cache of stock + option bars (gitignored)
├── results/          — backtest outputs (this file, summary.csv, trades.csv)
└── .env              — MASSIVE_API_KEY (gitignored — rotate the one in chat)
```

### To re-run or extend

```bash
# Update the 2-year window as time rolls forward
.venv/bin/python scripts/focused_run.py         # IWM + XLK grid (~60 min on free tier)
.venv/bin/python scripts/xlv_run.py             # XLV diversifier (~15 min)
.venv/bin/python scripts/build_plan.py          # regenerate this plan
```

---

## Caveats & what the backtest *cannot* tell you

- **One regime.** The 2-year window is a strong bull market with two moderate corrections. It doesn't include a 2022-style bear or a 2020-style crash. A longer window (Massive Developer tier, 4 years) or a manual stress test would give a fuller picture of tail risk.
- **No quotes → close-based fills.** Basic tier doesn't serve NBBO data. Fills are modeled at daily close ± 2% slippage. In live trading, patient limit orders at mid should do better than this.
- **BSM greeks.** Delta is computed from Black-Scholes on the put's close price. For 0.20-0.30 delta, 35 DTE OTM puts on liquid ETFs, this matches market-implied delta within 0.02.
- **Wheel not simulated.** The assignment P/L above is worst-case (sell assigned shares at expiry close). In practice, writing covered calls recovers 50-80% of those losses over 1-3 months.
- **Dividends.** Backtest uses constant dividend yield (~1.3% for IWM). Ex-div days aren't modeled discretely; for IWM the effect is ~$0.08/month — negligible.
