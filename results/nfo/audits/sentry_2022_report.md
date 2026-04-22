# 2022 NIFTY V3 Sentry Report

Window: 2022-01-01 → 2022-12-31
Trading days evaluated: **248**
V3 fire-days: 10 (4.0%, ≈ 10.1/yr)
V3 fire-**cycles** (distinct expiries): **4** (≈ 4.0/yr) — this is the decision unit.

## Why cycles, not days

V3 runs in `cycle_matched` or `live_rule` mode — multiple fire-days within the same monthly expiry collapse to one canonical trade. `results/nfo/redesign_winner.json` shows `filtered_trades: 10` over ~1.96 calibration years = **~5.1 cycles/yr**. That is the prior to compare against, not the `firing_per_year: 11.71` figure (which is fire-days).

## Decision framework (cycle units)

- **Materially more** (>8 cycles/yr): 2022 regime was richer for V3 than calibration → expansion is worthwhile and may produce a larger-than-projected sample.
- **About the same** (3-7 cycles/yr): research-only verdict stands; expansion still worthwhile.
- **Materially less** (<3 cycles/yr): V3 may be overfit or mis-specified for high-event regimes → consider kill or redesign before spending on full backfill.

## Per-signal pass counts

| Signal | Pass | Fail | Unknown |
|---|---:|---:|---:|
| s1_vix_abs | 91 | 157 | 0 |
| s2_vix_pct | 44 | 204 | 0 |
| s3_iv_rv | 113 | 10 | 125 |
| s4_pullback | 100 | 148 | 0 |
| s5_iv_rank | 31 | 217 | 0 |
| s6_trend | 189 | 59 | 0 |
| s7_skew | 0 | 0 | 248 |
| s8_event | 14 | 234 | 0 |

## V3 fire days (full list)

| Date | Spot | VIX | IV-RV | Trend | Events |
|---|---:|---:|---:|:---:|---|
| 2022-02-28 | ₹16,794 | 28.6 | +5.3pp | 2 | 2022-03-10 US CPI; 2022-03-16 FOMC |
| 2022-05-27 | ₹16,352 | 21.5 | +1.4pp | 2 | 2022-06-08 RBI MPC; 2022-06-15 FOMC |
| 2022-06-24 | ₹15,699 | 20.6 | +0.4pp | 2 | 2022-07-13 US CPI; 2022-07-27 FOMC |
| 2022-06-27 | ₹15,832 | 21.0 | +4.5pp | 2 | 2022-07-13 US CPI; 2022-07-27 FOMC |
| 2022-06-28 | ₹15,850 | 21.4 | +8.4pp | 2 | 2022-07-13 US CPI; 2022-07-27 FOMC |
| 2022-06-29 | ₹15,799 | 21.9 | +12.6pp | 2 | 2022-07-13 US CPI; 2022-07-27 FOMC |
| 2022-06-30 | ₹15,780 | 21.8 | +52.1pp | 2 | 2022-07-13 US CPI; 2022-07-27 FOMC |
| 2022-07-01 | ₹15,752 | 21.2 | +8.0pp | 2 | 2022-07-13 US CPI; 2022-07-27 FOMC |
| 2022-07-04 | ₹15,835 | 21.0 | +7.3pp | 2 | 2022-07-13 US CPI; 2022-07-27 FOMC |
| 2022-10-03 | ₹16,887 | 21.4 | +7.2pp | 2 | 2022-10-13 US CPI |