# Multi-fire-cycle Entry Audit

**Generated:** 2026-04-22
**Question:** When V3 fires multiple times on the same expiry, how many of those entries were tradable and profitable? Did `first_fire` represent the cycle or just one lucky draw?

**Scope:** NIFTY-only, V3-frozen. Non-canonical. Outputs under `results/nfo/audits/`.
**Gate:** `scripts/nfo/sentry_2022.v3_fire_mask` (canonical V3 event + score gate).
**Engine:** `nfo.engine.execution.run_cycle_from_dhan` (same path walk-forward uses).
**Lot sizing:** `nfo.universe.lot_size_on(name, entry_date)` per-trade dated lookup.

## Full-union aggregate

- Multi-fire cycles evaluated: **18**
- Cycles where *all* tradable fire-days were profitable: **66.7%**
- Cycles where *some but not all* tradable fire-days were profitable: **22.2%**
- Cycles where `first_fire` itself was profitable: **77.8%**
- Cycles where *at least one* tradable fire-day was profitable: **88.9%**
- Avg first_fire P&L minus avg cycle's tradable-mean P&L: **₹-38**
- Avg best-day P&L minus avg first_fire P&L: **₹901**

## Walk-forward OOS-only aggregate

- Multi-fire cycles in OOS: **8**
- Cycles where *all* tradable fire-days were profitable: **75.0%**
- Cycles where *some but not all* tradable fire-days were profitable: **25.0%**
- Cycles where `first_fire` itself was profitable: **100.0%**
- Cycles where *at least one* tradable fire-day was profitable: **100.0%**
- Avg first_fire P&L minus avg cycle's tradable-mean P&L: **₹493**
- Avg best-day P&L minus avg first_fire P&L: **₹254**

## Per-cycle detail

| Expiry | In OOS? | Fire-days | Tradable | Untradable | Profitable | Losing | First-fire P&L | Best P&L | Worst P&L | first_fire rank | All tradable profitable? |
|---|:---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|
| 2020-09-24 |  | 6 | 6 | 0 | 0 | 6 | ₹-5,980 | ₹-4,986 | ₹-6,264 | middle |  |
| 2020-11-26 |  | 3 | 3 | 0 | 3 | 0 | ₹599 | ₹1,461 | ₹528 | middle | ✓ |
| 2021-01-28 |  | 7 | 6 | 1 | 5 | 1 | ₹178 | ₹1,552 | ₹-720 | middle |  |
| 2021-03-25 |  | 11 | 10 | 1 | 3 | 7 | ₹-5,773 | ₹1,808 | ₹-6,079 | middle |  |
| 2021-04-29 |  | 2 | 1 | 1 | 1 | 0 | — | ₹1,574 | ₹1,574 | first_fire_untradable | ✓ |
| 2021-05-27 |  | 5 | 5 | 0 | 5 | 0 | ₹274 | ₹1,524 | ₹274 | worst | ✓ |
| 2021-09-30 |  | 2 | 2 | 0 | 2 | 0 | ₹1,306 | ₹1,452 | ₹1,306 | worst | ✓ |
| 2021-10-28 |  | 3 | 3 | 0 | 3 | 0 | ₹614 | ₹696 | ₹237 | middle | ✓ |
| 2021-11-25 |  | 2 | 2 | 0 | 0 | 2 | ₹-5,664 | ₹-5,664 | ₹-5,933 | best |  |
| 2022-07-28 |  | 7 | 6 | 1 | 6 | 0 | ₹790 | ₹1,791 | ₹185 | middle | ✓ |
| 2023-09-28 | ✓ | 5 | 3 | 2 | 3 | 0 | ₹765 | ₹765 | ₹163 | best | ✓ |
| 2024-01-25 | ✓ | 9 | 8 | 1 | 7 | 1 | ₹611 | ₹1,553 | ₹-1,515 | middle |  |
| 2024-03-28 | ✓ | 11 | 10 | 1 | 10 | 0 | ₹685 | ₹1,612 | ₹141 | middle | ✓ |
| 2024-05-30 | ✓ | 4 | 4 | 0 | 4 | 0 | ₹1,491 | ₹1,491 | ₹1,306 | best | ✓ |
| 2024-06-27 | ✓ | 3 | 3 | 0 | 3 | 0 | ₹759 | ₹759 | ₹402 | best | ✓ |
| 2024-12-26 | ✓ | 2 | 2 | 0 | 1 | 1 | ₹884 | ₹884 | ₹-6,096 | best |  |
| 2025-05-27 | ✓ | 2 | 2 | 0 | 2 | 0 | ₹970 | ₹970 | ₹591 | best | ✓ |
| 2025-11-25 | ✓ | 4 | 4 | 0 | 4 | 0 | ₹1,279 | ₹1,443 | ₹1,279 | worst | ✓ |

## Plain-language conclusion

Three separate questions answered below — they are often conflated but mean different things.

### 1. Within-cycle entry flexibility

**Mixed:** in 66.7% of multi-fire cycles, every tradable fire-day was profitable, but a meaningful minority had at least one losing entry. `first_fire` was profitable in 77.8% of cycles — suggesting entry timing matters more than the walk-forward summary implied.

### 2. Calendar-level opportunity scarcity

The audit evaluates only multi-fire cycles. If that set is small relative to the total fire-cycle count, the story is about *how rare* clusters are, not about the clusters themselves. This audit counts **18** multi-fire cycles in the full union; compare against the **25** total fire-cycles the expansion produced. The rest are single-fire-day cycles where `first_fire` is the only choice.

### 3. Walk-forward evidence sufficiency

**8 multi-fire cycles land in OOS.** Their within-cycle profitability (75.0% all-profitable) is informative about V3's quality during evaluated windows, but does not rescue V3 from the kill rule that triggered on thin-window trade count. Within-cycle entry flexibility and calendar-level trade density are separate failure modes.

### Outright losing cycles

- Pre-OOS (cycles not in any walk-forward test window): **2**
- OOS (cycles inside a walk-forward test window): **0**

Every outright-loser below was invisible to the PR3 walk-forward because it falls before the first reachable test window (2022-08-01). If these had been in OOS, they would have contributed real losses to the walk-forward aggregate — possibly enough to fail kill rule #2 (median per-window OOS P&L ≤ 0) in addition to the thin-windows rule that actually fired.

| Expiry | Fire-days | Tradable | Losing | First-fire P&L | Best P&L | Worst P&L |
|---|---:|---:|---:|---:|---:|---:|
| 2020-09-24 | 6 | 6 | 6 | ₹-5,980 | ₹-4,986 | ₹-6,264 |
| 2021-11-25 | 2 | 2 | 2 | ₹-5,664 | ₹-5,664 | ₹-5,933 |

### Does this change the V3 kill verdict?

**No, but it sharpens it.** The walk-forward killed V3 on thin-window trade density: 4 of 11 test windows had < 3 trades. That is a *calendar* property, not a per-cycle property, and this audit doesn't overturn it — within-cycle flexibility cannot conjure trades on days V3's gate didn't fire.

What this audit *adds* is visibility into V3's losing cycles. The walk-forward reported a 100% by-sign win rate on its 11 executed trades, which could be (mis)read as 'V3 never loses on a real day.' This audit shows that reading is false: V3 has at least **2 pre-OOS outright losing cycles**, with first_fire P&Ls of roughly ₹-5,700 to ₹-6,000 per contract. They were filtered out of the walk-forward only because they sit inside the first 24-month training/warmup zone (2020-08 → 2022-07), not because V3 rejected them.

Honest framing: V3 IS a good filter *on the OOS subset that walk-forward happened to evaluate* (75.0% of OOS multi-fire cycles are all-profitable). V3 is *not* a uniformly-good filter across history; it had at least one regime (early-COVID / recovery, late 2020 — 2021) where it produced multi-day losing clusters. On NIFTY-only monthly expiries, the combination of (a) thin annual trade density and (b) regime-specific losing clusters is the reason production is not viable — not merely scarcity alone.