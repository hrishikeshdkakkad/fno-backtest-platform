# Filter redesign — variant comparison

Baseline (unfiltered 82-trade cost-inclusive set):
- Sharpe: **-0.843**
- Win rate: **80%**
- Max-loss rate: **7.32%**

## Success criteria (all four must hold):

1. Firing rate ∈ [5, 20] days/year
2. Win-rate on filtered trades ≥ 85%
3. Sharpe lift ≥ +30% vs baseline
4. Max-loss rate on filtered trades < baseline (7.32%)

## Variant results (ranked)

| # | Variant | Fires/yr | Filt Trades | Win% | Sharpe | Δ Sharpe | MaxLoss% | ✓All? |
|---|---|---:|---:|---:|---:|---:|---:|:-:|
| 1 | **V3** | 10.44 | 10 | 70% | -0.35 | +59% | 0.0% | — |
| 2 | **V4** | 10.44 | 10 | 70% | -0.35 | +59% | 0.0% | — |
| 3 | **V5** | 10.44 | 10 | 70% | -0.35 | +59% | 0.0% | — |
| 4 | **V6** | 139.39 | 58 | 74% | -0.65 | +22% | 8.6% | — |
| 5 | **V0** | 0.0 | 0 | 0% | +nan | +0% | 0.0% | — |
| 6 | **V1** | 0.0 | 0 | 0% | +nan | +0% | 0.0% | — |
| 7 | **V2** | 0.0 | 0 | 0% | +nan | +0% | 0.0% | — |

## Per-variant detail

### V3 — V2 + specific-pass gate (IV-RV + trend + event + ≥1 of VIX/IV-rank)

- Firing days (total): **23**  (≈ 10.44 / year)
- Filtered trades: **10**  (of 70 real trades)
- Win rate: **70.0%**
- Avg PnL / contract: **₹-221**
- Sharpe: **-0.347** (Δ +59% vs baseline)
- Sortino: -0.253
- Max-loss rate: **0.00%**
- Passes ALL criteria: **NO**

First 20 firing dates: 2024-02-22, 2024-02-26, 2024-02-27, 2024-02-28, 2024-02-29, 2024-03-01, 2024-05-03, 2024-05-06, 2024-05-07, 2024-05-08, 2024-05-23, 2024-05-24, 2024-05-27, 2024-09-06, 2024-11-22, 2024-11-25, 2025-01-06, 2025-04-24, 2025-04-25, 2025-10-30

### V4 — V3 + tuned thresholds (vix_rich=22, pullback_atr=1.5)

- Firing days (total): **23**  (≈ 10.44 / year)
- Filtered trades: **10**  (of 70 real trades)
- Win rate: **70.0%**
- Avg PnL / contract: **₹-221**
- Sharpe: **-0.347** (Δ +59% vs baseline)
- Sortino: -0.253
- Max-loss rate: **0.00%**
- Passes ALL criteria: **NO**

First 20 firing dates: 2024-02-22, 2024-02-26, 2024-02-27, 2024-02-28, 2024-02-29, 2024-03-01, 2024-05-03, 2024-05-06, 2024-05-07, 2024-05-08, 2024-05-23, 2024-05-24, 2024-05-27, 2024-09-06, 2024-11-22, 2024-11-25, 2025-01-06, 2025-04-24, 2025-04-25, 2025-10-30

### V5 — V4 + relaxed grade (score ≥ 3 of 7, keep specific gate)

- Firing days (total): **23**  (≈ 10.44 / year)
- Filtered trades: **10**  (of 70 real trades)
- Win rate: **70.0%**
- Avg PnL / contract: **₹-221**
- Sharpe: **-0.347** (Δ +59% vs baseline)
- Sortino: -0.253
- Max-loss rate: **0.00%**
- Passes ALL criteria: **NO**

First 20 firing dates: 2024-02-22, 2024-02-26, 2024-02-27, 2024-02-28, 2024-02-29, 2024-03-01, 2024-05-03, 2024-05-06, 2024-05-07, 2024-05-08, 2024-05-23, 2024-05-24, 2024-05-27, 2024-09-06, 2024-11-22, 2024-11-25, 2025-01-06, 2025-04-24, 2025-04-25, 2025-10-30

### V6 — V4 minus specific-pass gate — broadest variant that kept tuned thresholds

- Firing days (total): **307**  (≈ 139.39 / year)
- Filtered trades: **58**  (of 70 real trades)
- Win rate: **74.1%**
- Avg PnL / contract: **₹-487**
- Sharpe: **-0.653** (Δ +22% vs baseline)
- Sortino: -0.592
- Max-loss rate: **8.62%**
- Passes ALL criteria: **NO**

First 20 firing dates: 2024-01-17, 2024-01-18, 2024-01-19, 2024-01-20, 2024-01-23, 2024-01-29, 2024-01-30, 2024-01-31, 2024-02-05, 2024-02-06, 2024-02-07, 2024-02-08, 2024-02-09, 2024-02-12, 2024-02-13, 2024-02-14, 2024-02-15, 2024-02-16, 2024-02-19, 2024-02-20

### V0 — Baseline — current thresholds, CPI=high, full-DTE window, 7/7 required

- Firing days (total): **0**  (≈ 0.0 / year)
- Filtered trades: **0**  (of 70 real trades)
- Win rate: **0.0%**
- Avg PnL / contract: **₹0**
- Sharpe: **None** (Δ +0% vs baseline)
- Sortino: None
- Max-loss rate: **0.00%**
- Passes ALL criteria: **NO**

First 20 firing dates: —

### V1 — Demote CPI to medium — only RBI/FOMC/Budget count as 'high'

- Firing days (total): **0**  (≈ 0.0 / year)
- Filtered trades: **0**  (of 70 real trades)
- Win rate: **0.0%**
- Avg PnL / contract: **₹0**
- Sharpe: **None** (Δ +0% vs baseline)
- Sortino: None
- Max-loss rate: **0.00%**
- Passes ALL criteria: **NO**

First 20 firing dates: —

### V2 — V1 + event window = first 10 days of cycle only

- Firing days (total): **0**  (≈ 0.0 / year)
- Filtered trades: **0**  (of 70 real trades)
- Win rate: **0.0%**
- Avg PnL / contract: **₹0**
- Sharpe: **None** (Δ +0% vs baseline)
- Sortino: None
- Max-loss rate: **0.00%**
- Passes ALL criteria: **NO**

First 20 firing dates: —

## ⚠️ No variant satisfies all four criteria

- Best Sharpe: **V3** (-0.347)
- Best win-rate: **V6** (74%)

Trade-offs surfaced — pick a variant based on which criterion matters most, or iterate on new variants targeting the specific gap.