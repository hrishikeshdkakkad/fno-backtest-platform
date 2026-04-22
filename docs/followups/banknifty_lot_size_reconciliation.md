# Follow-up: BANKNIFTY Lot-Size Reconciliation

**Status:** Backlog — out of scope for the NIFTY V3 kill-plan.
**Opened:** 2026-04-21
**Origin:** Surfaced by the invariant test added in `tests/nfo/test_universe.py` as part of the lot-size lookup (2026-04-21 session).

## What was found

The lot-size invariant test asserts that `Underlying.lot_size` for each registered underlying equals the current-day result of `lot_size_on(name, today)`. For BANKNIFTY this fails:

- `src/nfo/universe.py` `REGISTRY["BANKNIFTY"].lot_size = 35`
- `docs/india-fno-nuances.md` §2 (citing NSE Circular FAOP70616, Oct 2025) states BANKNIFTY lot is **30** post-2024-11-20 and **unchanged** on 2025-12-30.
- `src/nfo/universe.py` `LOT_SIZE_HISTORY["BANKNIFTY"]` matches the docs (15 → 30 on 2024-11-20).

## Current state

The invariant test is marked `@pytest.mark.xfail(strict=True, reason=…)` so the discrepancy is visible in CI output, not silent. It does not block the NIFTY kill-plan because BANKNIFTY is out of scope there.

## Resolution path

1. Look up fresh NSE circulars (FAOP64625 Nov 2024, FAOP70616 Oct 2025, and anything after Dec 2025).
2. Confirm the current BANKNIFTY lot size from an authoritative source.
3. Update whichever side is wrong:
   - If docs are wrong, update `docs/india-fno-nuances.md` + `LOT_SIZE_HISTORY`.
   - If REGISTRY is wrong, update `src/nfo/universe.py` `Underlying(...lot_size=…)`.
4. Remove the xfail marker from `test_registry_banknifty_lot_matches_current_lookup`.
5. Re-run the full test suite.

## Why this is out of scope for V3

The NIFTY-only V3 strategy never reads `REGISTRY["BANKNIFTY"].lot_size`. Any BANKNIFTY backtest would, but there is none in the active kill plan. Deferring BANKNIFTY reconciliation until there is a concrete BANKNIFTY workstream avoids:

- Making NIFTY decisions block on verification of an unrelated underlying.
- Touching a production-facing constant (`REGISTRY[].lot_size`) without a clear BANKNIFTY test path to prove the fix is correct.
