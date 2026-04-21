# Indian F&O Market Nuances for Systematic Credit Spread Sellers

Research compiled 2026-04-20 via Parallel.ai web search. All facts cross-referenced against SEBI circulars, NSE documentation, peer-reviewed academic papers (Shaikh & Padhi 2014 on India VIX microstructure), and major broker explainers (Zerodha, mStock, RK Global). This document is specific to NIFTY monthly put credit spread selling, but most findings generalize to any short-vol strategy on Indian indices.

---

## Table of contents

1. [Strategic context: what you're actually doing](#strategic-context)
2. [SEBI October 2024 F&O framework (six measures)](#sebi-framework)
3. [STT: rates, calculation, and the trap](#stt)
4. [India VIX microstructure and seasonality](#india-vix)
5. [NIFTY–India VIX correlation decay post-2019](#correlation-decay)
6. [Weekly vs monthly — now a binary choice](#weekly-vs-monthly)
7. [Sizing reality on ₹2.34L margin per lot](#sizing)
8. [Highest-leverage additions to regime detection](#regime-additions)
9. [Institutional comparables in India](#comparables)
10. [What this means for the "0/504 days hit 8/8" issue](#zero-days)
11. [Open questions deliberately not researched](#open-questions)

---

## 1. Strategic context: what you're actually doing <a id="strategic-context"></a>

The strategy running in `scripts/nfo/regime_watch.py` and `src/nfo/spread.py` is **systematic put credit spread selling on NIFTY** with an 8-signal regime gate. In industry terminology this is:

- **Defined-risk volatility risk premium harvesting** on an index
- Structurally similar to CBOE PUT / CNDR index methodologies, adapted for NIFTY
- Closer to what Harvest Volatility Management or Capstone run as SPX sleeves than to pure retail CSP selling

The VRP (Volatility Risk Premium) is a real, documented risk premium: implied vol averages 3-5% higher than realized vol because tail hedgers pay up for downside protection. You are being compensated for bearing that risk — this is not proprietary edge, it is compensation for a specific risk posture. Every institutional and retail vol seller harvests the same premium. What varies is the quality of the filter, selection, and exit rules.

Your specific edge sources (in descending order of realism):

1. **Discipline substitute for judgment** — systematic rules eliminate emotional trading (large, durable benefit)
2. **Defined-risk structure** — max loss capped at `width - credit`; cannot blow up on one trade
3. **Regime filter as defensive moat** — avoids the 1-2 catastrophic setups per year (only useful if calibrated, which is still being worked on)
4. **Calibration framework** — `calibrate.py` with grid search is more rigorous than most retail setups
5. **India F&O has fewer systematic short-vol competitors than SPX** — genuine structural advantage vs US markets

Edge sources you do NOT have:

- Proprietary information or alternative data
- Execution speed (retail fills)
- Scale-based cost advantages

---

## 2. SEBI October 2024 F&O framework — six measures <a id="sebi-framework"></a>

On October 1, 2024, SEBI issued a framework circular (`SEBI/HO/MRD/TPD-1/P/CIR/2024/132`) introducing six measures, phased over Nov 2024 – Apr 2025. These are the most consequential structural changes to retail Indian F&O in a decade and directly affect sizing, exit logic, and available expiries.

| # | Change | Effective date | Direct impact on credit spread strategy |
|---|---|---|---|
| 1 | Min index contract value ₹15-20 lakh; NIFTY lot 25→75 (then 75→65 on Dec 30, 2025) | Nov 20, 2024; Dec 30, 2025 | Margin per NIFTY lot went from ~₹73k to ~₹2.34L (~3x). Sizing on $41k drops to 1-2 lots max. |
| 2 | Weekly expiries limited to one benchmark index per exchange | Nov 20, 2024 | Only **NIFTY weeklies** survive on NSE. BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50 weeklies discontinued. BSE keeps only SENSEX weeklies. |
| 3 | Additional 2% ELM on short options on expiry day | Nov 20, 2024 | Holding short spreads through expiry day now costs meaningfully more margin. |
| 4 | Calendar spread margin benefit removed on expiry day | Feb 1, 2025 | If you hold a spread into expiry, both legs are fully margined. Multi-expiry strategies hit hardest. |
| 5 | Upfront option premium collection on buy side | Feb 1, 2025 | Not directly affecting sellers, but reduces speculative retail flow → liquidity impact on OTM wings. |
| 6 | Intraday position limit monitoring | Apr 1, 2025 | Real-time position checks, not EOD. Affects scaling up. |

### Actionable rules derived from the framework

- **Do not hold any spread through expiry day.** Combined +2% ELM and loss of calendar spread benefit make this strictly worse than closing on T-1 or T-2.
- **Set `manage_at_dte >= 2`** in backtest configuration so exits happen at least two sessions before expiry.
- **Verify current NIFTY lot size on each run.** SEBI mandates semi-annual reviews (June and December). The Dec 30, 2025 revision took NIFTY 75→65. Next review: June 2026.
- **Drop BANKNIFTY/FINNIFTY/MIDCPNIFTY weekly references from any code.** They don't exist anymore.

### Lot size history reference

| Index | Pre-Nov 2024 | Nov 20, 2024 | Dec 30, 2025 |
|---|---|---|---|
| NIFTY 50 | 25 | 75 | 65 |
| BANKNIFTY | 15 | 30 | 30 |
| FINNIFTY | 25 | 65 | 60 |
| MIDCPNIFTY | 50 | 120 | 120 |
| NIFTYNXT50 | 10 | 25 | 25 |
| SENSEX | 10 | 20 | 20 |
| BANKEX | 15 | 30 | 30 |
| SENSEX50 | 25 | 60 | 60 |

---

## 3. STT: rates, calculation, and the trap <a id="stt"></a>

Securities Transaction Tax is the single largest transaction cost for active Indian options traders and has a specific trap structure for credit spread sellers.

### Current STT rates (F&O)

| Transaction | Rate | Paid by | Basis |
|---|---|---|---|
| Sale of option (premium received) | 0.1% (post-Budget 2024; may be 0.15% post-Budget 2026 — verify current NSE page) | Seller | Premium × lot size |
| Option exercised / settled ITM | 0.125% | Buyer / holder | Intrinsic value (settlement − strike) × lot size |
| Sale of futures | 0.02% (post-Budget 2024) | Seller | Trade price × lot size |

Sources conflict on the very latest rate (ClearTax lists 0.1%, Upstox references a Budget 2026 hike to 0.15%). Always verify against the current NSE circular before sizing.

### Why STT on exercised options matters to you (the trap)

In a **NIFTY put credit spread**, you sell a higher-strike put and buy a lower-strike put:

```
Short leg: 25000 PE (sold for credit)
Long leg:  24900 PE (bought for protection)
Width:     100 points
```

At expiry, if NIFTY closes at 24850 (below both strikes):

- **Short leg (25000 PE)**: ITM by 150 points. The BUYER of your short put exercises it. They pay STT 0.125% × 150 × 65 = ~₹12.19. This is not your cost.
- **Long leg (24900 PE)**: ITM by 50 points. YOU are the holder of this long put. If you let it auto-exercise, YOU pay STT 0.125% × 50 × 65 = ~₹4.06.

This sounds trivial at small ITM amounts but scales with how deep ITM your long leg is. If NIFTY crashes and closes at 24500, your long 24900 PE is ITM by 400 points, and your STT is 0.125% × 400 × 65 = ~₹32.50 per contract. Still small in absolute terms for NIFTY, but on BANKNIFTY or multi-lot positions the costs compound.

The **real trap** is more subtle: if you hold to expiry and the long leg auto-exercises, the settlement flow goes through settlement STT rather than premium STT. On deeply ITM long legs, this is materially more expensive than closing the long leg a day earlier at market price (which is charged as option sale at 0.1% of premium, not 0.125% of intrinsic).

### Rule in code

Always close any leg of a spread that is ITM before expiry. Never allow auto-exercise on a long protective leg. Equivalent to:

```
if leg_itm_at_eod_before_expiry:
    force_exit = True
```

Combined with the +2% ELM rule (Section 2), the general principle is: **exit all credit spreads at least 1 trading day before expiry, ideally 2.**

### Other transaction costs to model

For a complete cost picture, include:

- **Exchange transaction charges** (NSE): ~0.0503% of premium (varies; check NSE)
- **SEBI turnover fees**: ~0.0001% of premium
- **GST**: 18% on (brokerage + transaction charges + SEBI fees)
- **Stamp duty**: 0.003% on buy side of premium (state-dependent; marginal)
- **Brokerage**: broker-dependent. Zerodha and Dhan are flat ₹20 per executed order on F&O. Full-service brokers can be 10-20x higher.

All-in round-trip cost on a typical NIFTY credit spread of ₹100 credit is approximately 1.5-2% of credit received. This is material for high-frequency strategies but modest for monthly-cycle strategies.

---

## 4. India VIX microstructure and seasonality <a id="india-vix"></a>

The foundational academic work on India VIX seasonality is Shaikh & Padhi (2014), "The behavior of option's implied volatility index: a case of India VIX," *Business: Theory and Practice*. They analyzed 1,361 trading days (Nov 2007 – Apr 2013) using AR-DOLS and AR-GARCH(1,1) frameworks. Key findings have been replicated in Akhtar, Ansari et al (2017) for March 2009 – February 2016.

### Day-of-week seasonality (peer-reviewed)

| Day | VIX return (full sample, GARCH) | Statistical significance | Interpretation |
|---|---|---|---|
| Monday | **+2.44%** | p < 0.001 | Weekend accumulated uncertainty prices in on open |
| Tuesday | **-1.29% to -1.52%** | p < 0.001 | Relief rally as early-week uncertainty resolves |
| Wednesday | ≈ 0 | Not significant | Neutral |
| Thursday | ≈ 0 on normal Thursdays | Not significant | Neutral except on expiry |
| Friday | ≈ 0 | Not significant | Neutral |

### Options expiration effect

On options expiration day, India VIX falls ~**-2.64%** (p < 0.001). The pattern surrounds expiration:
- T-2: VIX close 28.61 (sample mean), return -1.14%
- T-1: VIX close 28.29, return -1.39%
- **T=0 (expiry)**: VIX close 27.47, return **-2.76%**
- T+1: return -2.14%
- T+2: return -0.57%

### Month-of-year effects

- **March, December**: negative (year-end / fiscal-year position clearing)
- **May**: positive (pre-election uncertainty, budget residuals)
- **April, July, October, January**: slight negative (quarterly results resolving uncertainty)

### Practical implications for entry timing

- **Monday AM entries are systematically the worst** — you sell into the lowest VIX of the week, right before Monday's upward VIX move reprices premiums
- **Thursday PM / Friday AM entries are systematically the best** — you sell after expiry-day VIX crush when IV is momentarily compressed relative to its forward path
- **March and December monthly expiries**: negative VIX drift makes short vol favorable (reinforces the selling edge)
- **May**: watch for pre-election or budget volatility spikes; entries here face adverse VIX path

### Heuristic vol thresholds for India VIX

Cross-referenced from retail broker research (Zerodha, PL Capital, Stoxra) and matched against the academic distribution (2007-2015 median ~21, 2019-2025 median ~13):

| India VIX | Regime | Short vol attractiveness |
|---|---|---|
| < 12 | Compressed | Premiums cheap; marginal expected value |
| 12 – 15 | Typical | Standard setup |
| 15 – 18 | Rich | Favorable short vol window |
| 18 – 22 | Elevated | Reduce size; quality setups only |
| > 22 | Extreme | ~95th percentile event; structural risk high |

These thresholds are roughly **10 points lower** than US CBOE VIX equivalents. Translating US research (tastytrade, CBOE PUT Index, Harvest Vol) without adjustment produces filters that never trigger — this is the likely mechanism behind the "0 out of 504 days hitting 8/8" observation.

---

## 5. NIFTY–India VIX correlation decay post-2019 <a id="correlation-decay"></a>

Kannan & Sripriya (2020), "Effect of the Launch of Nifty Weekly Options on the Relationship between Nifty and India VIX" documented a structural break in NIFTY–VIX correlation after NSE introduced NIFTY weekly options on Feb 11, 2019.

### Correlation before and after

| Window | Pre-weekly options (Nov 2018 – Feb 2019) | Post-weekly options (Feb 2019 – May 2019) |
|---|---|---|
| 15 days | -0.729 | **-0.522** |
| 30 days | -0.455 | **-0.341** |
| 60 days | -0.812 (historical reference) | Weakened |

### Why it weakened

1. Weekly options expire every Thursday, creating persistent short-dated gamma and vega flows
2. India VIX methodology uses near-month and mid-month options bid-ask quotes; weekly option positioning distorts these inputs
3. VIX now reflects more short-term positioning than pure 30-day expected vol
4. Retail participation in weekly options went from near-zero to ~60% of volumes at ATM strikes within weeks of launch

### Implications for your regime filter

- Any filter weighting `VIX_PCT_RICH` heavily is using a noisier-than-expected signal post-2019
- Direct measures outperform VIX-derived measures: ATM IV percentile, realized vol percentile, 25-delta skew
- Pre-2019 academic papers that benchmarked VIX-based strategies overstate how reliable VIX signals are today
- Your existing code has `iv_rank_12mo` and `skew_25d` — these are now more important signals than `vix_pct_3mo`

---

## 6. Weekly vs monthly — now a binary choice <a id="weekly-vs-monthly"></a>

Post-Nov 2024 SEBI changes, the expiry landscape on NSE is:

| Index | Weekly | Monthly | Quarterly |
|---|---|---|---|
| NIFTY 50 | ✅ (only index with weekly) | ✅ | ✅ |
| BANKNIFTY | ❌ DROPPED | ✅ | ✅ |
| FINNIFTY | ❌ DROPPED | ✅ | — |
| MIDCPNIFTY | ❌ DROPPED | ✅ | — |
| NIFTYNXT50 | ❌ DROPPED | ✅ | — |
| SENSEX (BSE) | ✅ (BSE's only weekly) | ✅ | ✅ |
| Stock F&O | Never existed | ✅ | — |

### Strategic implications

- Weekly credit spreads on NIFTY: shorter vega exposure, faster theta accrual, higher turnover
- Monthly credit spreads on NIFTY / BANKNIFTY / FINNIFTY: longer vega exposure, better for regime-driven entries, more DTE buffer for management
- The IV crush pattern (IV peaks Monday, crashes Tuesday) is more pronounced on weeklies
- Expiry day is currently Tuesday for NIFTY weeklies (verify current NSE calendar — BANKNIFTY's expiry day has moved multiple times: Thu → Wed in 2023, back to Thu in Jan 2025, then Monday in March 2025)

### Worth parallel-testing

Your current strategy runs monthly at ~35 DTE. A parallel weekly strategy on NIFTY (~7-10 DTE) with tighter deltas (~15-20Δ instead of 30Δ) would capture:
- More frequent theta harvesting
- Higher trade count for statistical stability
- Different regime exposure (weekly events dominate, monthly events dilute)

---

## 7. Sizing reality on ₹2.34L margin per lot <a id="sizing"></a>

Assuming $41,000 ≈ ₹34 lakh (at ~₹83/USD; verify user's capital denomination):

### NIFTY credit spread lot capacity

Margin per NIFTY put credit spread (100-point width, 30Δ short):
- Spread max loss: (100 − credit) × 65 lot = ~₹5,850 to ~₹6,500 per contract
- SPAN margin typically 1.5-2x max loss due to short option margin requirement
- Net: ~₹10,000 to ~₹15,000 blocked per credit spread contract (much less than naked NIFTY short at ~₹2.34L)

At ₹34L capital:
- **Per-cycle sizing**: 3-5 contracts comfortably, 8-10 aggressively
- **Total margin deployed**: 30-50% of capital, keeping buffer for adverse moves and margin calls
- Higher deployments invite margin call risk on VIX spike days

### Target yield reality check

The $500/mo target on $41k = 14.6% annual gross. For Indian F&O credit spreads:

- CBOE PUT Index US equivalent: ~9% long-term nominal
- PUTW ETF 10-year net: ~6-7% after fees
- Harvest Vol institutional: 8-12% with 10-15% max drawdown
- Retail defined-risk credit spread with strict regime filter: 10-18% plausible, with meaningful tail risk

14.6% is achievable on the upper end but implies either aggressive sizing, high trade frequency, or higher-delta shorts. This is not "safe yield" — it is compensation for bearing a specific tail risk profile.

### Buffer requirements

Always maintain:
- 40-50% free capital for margin top-up on VIX spikes
- Separate bucket for max-loss-scenario across all open positions simultaneously (they're all short vol, all correlated on a crash)
- Capital that is NOT in F&O so you can re-enter after a drawdown

---

## 8. Highest-leverage additions to regime detection <a id="regime-additions"></a>

Ranked by expected ROI per hour of implementation:

### Tier 1 — cheap, high-leverage

1. **Day-of-week entry gate** — skip Monday AM entries; prefer Thursday-after-expiry. 20 lines of code. Expected: +50-100 bps annual based on Shaikh-Padhi Monday effect.
2. **Close-before-expiry rule** — set `manage_at_dte >= 2`. Avoids STT trap on long leg, +2% ELM on expiry day, and calendar spread margin penalty. Critical for post-Nov-2024 regime.
3. **Month-of-year overlay** — reduce size in May (pre-election/budget vol); normal in March/December. 10 lines of code.

### Tier 2 — moderate effort, meaningful benefit

4. **Reweight signals post-2019** — downweight VIX-derived signals, upweight direct IV rank + skew given VIX correlation decay.
5. **India-specific VIX thresholds** — recalibrate `VIX_RICH` from 22 down to 14-15 (for 70th percentile). Directly addresses 0/504 days issue.
6. **Skew percentile ranking** — you have skew level but not its historical rank. Ranking makes the signal regime-aware.

### Tier 3 — larger effort, strategic

7. **Weekly NIFTY parallel strategy** — separate regime filter tuned to weekly expiry dynamics; captures IV crush pattern
8. **Book-level vega and delta aggregation** — institutional-grade; tracks correlated risk across open positions
9. **Tail hedge overlay** — allocate 2-5% of notional to far-OTM long puts as catastrophic hedge; pays for itself in one black swan event

### Out of scope / low ROI

- Rewriting in C++ (zero benefit for monthly-cycle strategy)
- More granular intraday data (regime decisions are EOD)
- Machine learning signal fitting on 2-year backtest (overfitting risk dominates)

---

## 9. Institutional comparables in India <a id="comparables"></a>

Firms running systematic NIFTY options strategies at scale:

### Directly comparable

- **True Beacon Global** (Nithin Kamath / Zerodha founder) — systematic NIFTY options PMS. Closest Indian peer.
- **Dolat Capital** — derivatives prop desk, heavy NIFTY F&O exposure
- **Edelweiss Prop Desk** — options market making and directional
- **Tower Research India, WorldQuant India** — systematic, some F&O exposure
- **Quant MF (Sandeep Tandon)** — regime-aware equity with options overlays
- **JM Financial, IIFL prop desks** — Indian derivatives prop

### Retail-facing tools codifying similar logic

- **Sensibull** — options strategy builder and scanner; templates for credit spreads
- **Dhan Options Lab** — your broker's research arm
- **Zerodha Streak** — rule-based strategy automation

### NOT directly comparable but methodologically relevant

- Harvest Volatility Management (US, SPX-focused) — defined-risk vol selling at institutional scale
- Capstone Investment Advisors (US, SPX) — vol-focused, defined-risk structures
- Parametric Portfolio Associates (Morgan Stanley) — systematic options overlays
- Innovator Buffered ETFs — retail ETFs that use your exact spread structure mechanically

These US firms' methodologies are worth studying but their *numerical thresholds* (VIX levels, skew measures) do not transfer to Indian markets.

---

## 10. What this means for the "0/504 days hit 8/8" issue <a id="zero-days"></a>

Cross-referencing this research against the earlier backtest finding that zero of 504 trading days hit all 8 signals simultaneously:

### Most likely causes (ranked)

1. **`VIX_RICH = 22` threshold calibrated for CBOE VIX, not India VIX.** India VIX spends most of 2023-2025 between 11-18. VIX > 22 is a ~95th percentile event — you're demanding ~1-2% of days meet the most stringent signal.
2. **Event filter severity map is too strict.** Labeling too many FOMC/RBI/budget windows as "high severity" makes the gate unreachable.
3. **Trend filter requires 2-of-3 votes across EMA cross + ADX + RSI.** In sideways markets this combination can fail persistently.
4. **Signal independence assumption broken.** Eight signals don't trigger independently; VIX-rich and IV-rank-rich co-move, as do pullback and trend-filter. Requiring "all 8" is harder than a naive probability calculation suggests.

### The V3 winning filter

Your newer `project_winning_filter_v3` finding (11 fires/yr, 90% win, Sharpe 1.75, 0% max-loss) suggests a "specific-pass gate" structure rather than "all 8" gating works much better. Worth checking whether V3 fires correlate with:
- Specific days of week (Monday avoidance?)
- Specific months (March/December preference?)
- Specific VIX regimes (post-12 but pre-18 band?)

If the V3 fires correlate with the microstructure findings in this document, that's strong evidence V3 is implicitly capturing real structure. If uncorrelated, V3 may be overfitted to the backtest window.

### Verification to run

```python
# Sketch — verify V3 fires against calendar structure
fires = load_v3_fires()
print(fires.groupby(fires.entry_date.dt.day_of_week).size())
print(fires.groupby(fires.entry_date.dt.month).size())
print(fires.groupby(pd.cut(fires.india_vix, [0, 12, 15, 18, 30])).size())
```

If V3 concentrates on Thursdays after expiry, avoids May, and clusters around VIX 13-16, it is capturing real structure. If uniformly distributed, investigate overfitting.

---

## 11. Open questions deliberately not researched <a id="open-questions"></a>

To conserve Parallel.ai API budget (memory: each call costs real money; cache aggressively), I deliberately did not pull:

- **Per-stock F&O liquidity tiers** — requires per-symbol data, more useful from direct NSE bhavcopy
- **Election-year specific vol patterns** — can derive from your own `regime_history.parquet`
- **SEBI 2026 pending proposals** — these are a moving target, not actionable until issued as circulars
- **BANKNIFTY specific expiry-day behavior post-March-2025** — your existing strategy is NIFTY-only
- **Current exact STT rate post-Budget-2026** — sources disagree (0.1% vs 0.15%); check NSE circulars directly before production sizing
- **India's position limit structure for retail traders** — unlikely to bind at $41k capital

Run diagnostic scripts on your existing parquet caches for any of these before spending another Parallel.ai call.

---

## Further reading

### Peer-reviewed

- Shaikh, I. & Padhi, P. (2014). "The behavior of option's implied volatility index: a case of India VIX." *Business: Theory and Practice.* DOI: 10.3846/btp.2015.465
- Kannan, S. & Sripriya, P. (2020). "Effect of the Launch of Nifty Weekly Options on the Relationship between Nifty and India VIX." *International Journal of Recent Technology and Engineering*, 9(1).
- Shaikh, I. & Padhi, P. (2014). "Inter-temporal relationship between India VIX and NIFTY." *Decision*, 41(4): 439-448.

### Regulatory primary sources

- SEBI Circular SEBI/HO/MRD/TPD-1/P/CIR/2024/132 (Oct 1, 2024) — six-measure framework
- NSE Circular FAOP64625 (Nov 2024) — lot size revisions
- NSE Circular FAOP70616 (Oct 2025) — Dec 2025 lot size revisions
- SEBI Equity Derivatives Master Circular (Dec 2024) — consolidated rulebook

### Broker explainers

- Zerodha Z-Connect, "SEBI's new rules for index derivatives" (Oct 3, 2024)
- mStock, "SEBI's New 2024 F&O Margin Rules: Explained for Traders"
- Zerodha Market Intel, lot size revision notices (Nov 2024, Dec 2025)

### Retail research

- PL Capital, "Nifty Weekly Options Strategy: Tuesday Expiry Trading Guide" (2026-02-01)
- Stoxra, "Nifty Option Chain Analysis for Weekly Expiry" (2026-03-12)
- Capitalmind, "The Death of Volatility Futures: Why VIX Trading is Dead in India"

---

*Document compiled 2026-04-20. Verify current SEBI circulars, NSE expiry calendar, and STT rates before any production sizing decision. Regulatory facts have short half-lives in Indian F&O.*
