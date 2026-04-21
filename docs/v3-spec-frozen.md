# V3 spec — frozen for falsification (2026-04-20)

Validation rule: **no thresholds, cost constants, match rules, or universe
parameters change below while the falsification battery is running**. Every
realism test (tail-loss, capital allocation, exit sweep, walk-forward, entry
perturbation) is measured against this exact configuration.

If a falsification test surfaces a reason to change any of these, we first
lock in a new spec version, then re-run the affected tests — not the reverse.

## Filter rule (V3)

Source: `scripts/nfo/redesign_variants.py::make_variants()` variant `V3`,
evaluated via `_row_passes` (line 209).

V3 fires on a day when **all** of these core gates pass:

| Gate | Check |
|---|---|
| s3 (IV − RV) | `(short-strike IV − RV_30) ≥ -2.0 vol-pts` |
| s6 (trend)   | `trend_score ≥ 2` (of 3 votes: EMA20>EMA50, ADX>20, RSI>40) |
| s8 (events)  | No RBI / FOMC / Union Budget in first 10 days of cycle; CPI demoted to medium |

**AND at least one** of the vol signals passes:

| Signal | Check |
|---|---|
| s1 (VIX abs)     | `VIX > 20` |
| s2 (VIX 3-mo %)  | `VIX_pct_3mo ≥ 0.80` |
| s5 (IV Rank 12m) | `IV_rank ≥ 0.60` |

`min_score = 4` (over the seven countable signals) is enforced after the
specific-pass gate, but in practice the four-gate combination already
implies score ≥ 4.

## Cost model

Source: `src/nfo/costs.py`. All costs are charged per contract-leg,
deducted from gross PnL inside `backtest._run_cycle`.

| Constant | Value | Basis |
|---|---|---|
| `STT_OPTION_SALE` | 0.001 | 0.1% of premium (seller) |
| `STT_OPTION_SETTLEMENT` | 0.00125 | 0.125% of intrinsic on ITM auto-exercise |
| `NSE_EXCHANGE_CHARGE` | 0.000503 | 0.0503% of premium |
| `SEBI_TURNOVER_FEE` | 0.000001 | 0.0001% of premium |
| `STAMP_DUTY_BUY` | 0.00003 | 0.003% of premium, buy side |
| `GST_RATE` | 0.18 | 18 % on brokerage + NSE + SEBI fees |
| `DHAN_FLAT_BROKERAGE` | 20.0 | ₹ per executed order |

## Trade-matching rules

Source: `src/nfo/robustness.py::pick_trade_for_expiry` + the legacy
`scripts/nfo/v3_capital_analysis._pick_trade` (they resolve cycles
identically).

Per V3 firing cycle, select **exactly one** trade where:
- `param_delta == 0.30`
- `param_width == 100.0`
- `expiry_date` equals the cycle's target monthly expiry
- `pt_variant == "pt50"` → prefer `param_pt == 0.50` (fallback to first row)
- `pt_variant == "hte"` → prefer `param_pt == 1.00` (fallback to first row)

If no row matches, the cycle is logged as `trade_found=False` and skipped
in the equity simulator — **not imputed**.

## Universe

Source: `src/nfo/universe.py`.

| Underlying | Lot size | Strike step | Margin multiplier |
|---|---:|---:|---:|
| NIFTY | 65 | 50 | 1.5 |

## Input data

- `results/nfo/spread_trades.csv` (82 rows, cost-inclusive, 2024-02 →
  2026-03, 0.30Δ × 100-width + 150-width + BANKNIFTY configs)
- `results/nfo/spread_trades_v3_gaps.csv` (4 rows — 2 custom-entry
  cycles × 2 exit variants — covers V3 fires that sit off the standard
  35-DTE grid)
- `results/nfo/historical_signals.parquet` (559 days, 2024-01-15 →
  2026-04-17)
- `data/nfo/index/VIX_2023-12-15_2026-04-18.parquet`
- `data/nfo/index/NIFTY_2023-12-15_2026-04-18.parquet`
- `data/nfo/rolling/*` (per-cycle strike-level option parquets)

## What is NOT frozen

- Rolling option data caches: fresh refreshes for new strikes are allowed.
- New derived columns on trade rows: allowed (e.g. tail-loss simulation
  adds a `synthetic` flag) as long as the original columns are preserved.
- Report formatting / new output files under `results/nfo/` — additive
  only.

## Version

`v3-spec-frozen-2026-04-20`. Changes after this date require a new spec
document and re-running the falsification battery against it.
