# Expansion Ingest — PR1 Coverage & Anomaly Report

Window: 2020-08-01 → 2023-12-31 (expansion target)
Spot cache window: 2019-08-01 → 2024-02-15
Trading days ingested: **848**
Monthly cycles with rolling data: **42 / 42**

## IV filter drops (in-process, applied at per-contract snapshot)

| Class | Rows dropped |
|---|---:|
| IV ≤ 0 | 961 |
| IV > 100% | 24 |
| **Total dropped** | **985** |

Drops are applied inside `_daily_snapshot_for_cycle`. The raw rolling-option parquets under `data/nfo/rolling/` are untouched — forensics remain possible.

## Residual IV anomalies in computed features (post-filter)

These are at the feature-aggregate level (atm_iv / short_strike_iv columns). Any remaining anomalies indicate the per-contract filter was insufficient — e.g. the atm_row lookup picked up a strike whose individual IV survived the filter but the aggregate derived in `evaluate_day` still looks wrong.

| Field | Anomaly class | Rows |
|---|---|---:|
| atm_iv | ≤ 0 (physically impossible) | 0 |
| atm_iv | > 100% annualized (implausible for NIFTY) | 0 |
| short_strike_iv | ≤ 0 | 0 |
| short_strike_iv | > 100% annualized | 0 |

Affected dates (any field):
_None._

## Policy

Anomalies are **dropped, not clamped**. The filter in `nfo.data.drop_iv_anomalies` is applied at the per-contract level inside the rolling-option fetch path (see PR1 wiring). Affected rows are reported above so that the defect remains visible and auditable. Clamping would invent data; dropping keeps the signal.

## Coverage by year/month

| Year | Month | Trading days | s3_iv_rv computable |
|---|---:|---:|---:|
| 2020 | 08 | 21 | 11 |
| 2020 | 09 | 22 | 9 |
| 2020 | 10 | 21 | 11 |
| 2020 | 11 | 20 | 6 |
| 2020 | 12 | 22 | 12 |
| 2021 | 01 | 20 | 10 |
| 2021 | 02 | 20 | 10 |
| 2021 | 03 | 21 | 9 |
| 2021 | 04 | 19 | 11 |
| 2021 | 05 | 20 | 10 |
| 2021 | 06 | 22 | 9 |
| 2021 | 07 | 21 | 12 |
| 2021 | 08 | 21 | 9 |
| 2021 | 09 | 21 | 12 |
| 2021 | 10 | 20 | 11 |
| 2021 | 11 | 20 | 8 |
| 2021 | 12 | 23 | 13 |
| 2022 | 01 | 20 | 10 |
| 2022 | 02 | 20 | 7 |
| 2022 | 03 | 21 | 12 |
| 2022 | 04 | 19 | 11 |
| 2022 | 05 | 21 | 8 |
| 2022 | 06 | 22 | 12 |
| 2022 | 07 | 21 | 11 |
| 2022 | 08 | 20 | 9 |
| 2022 | 09 | 22 | 12 |
| 2022 | 10 | 19 | 9 |
| 2022 | 11 | 21 | 9 |
| 2022 | 12 | 22 | 13 |
| 2023 | 01 | 21 | 11 |
| 2023 | 02 | 20 | 8 |
| 2023 | 03 | 21 | 10 |
| 2023 | 04 | 17 | 9 |
| 2023 | 05 | 22 | 10 |
| 2023 | 06 | 21 | 11 |
| 2023 | 07 | 21 | 8 |
| 2023 | 08 | 22 | 13 |
| 2023 | 09 | 20 | 11 |
| 2023 | 10 | 20 | 8 |
| 2023 | 11 | 21 | 11 |
| 2023 | 12 | 20 | 10 |