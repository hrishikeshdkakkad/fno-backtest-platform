"""Tests for nfo.universe.lot_size_on — time-varying NSE lot-size lookup.

NSE-published lot-size revision dates (see docs/india-fno-nuances.md §2):
  NIFTY:     25 → 75 on 2024-11-20 → 65 on 2025-12-30
  BANKNIFTY: 15 → 30 on 2024-11-20 (unchanged on 2025-12-30)
  FINNIFTY:  25 → 65 on 2024-11-20 → 60 on 2025-12-30

Sources: NSE Circulars FAOP64625 (Nov 2024), FAOP70616 (Oct 2025).
"""
from __future__ import annotations

from datetime import date

import pytest

from nfo import universe


class TestLotSizeOn:
    """NIFTY lot-size history lookup across the three published regimes."""

    def test_nifty_pre_2024_reform_is_25(self) -> None:
        assert universe.lot_size_on("NIFTY", date(2022, 6, 15)) == 25

    def test_nifty_on_eve_of_first_reform_is_still_25(self) -> None:
        assert universe.lot_size_on("NIFTY", date(2024, 11, 19)) == 25

    def test_nifty_on_first_reform_date_jumps_to_75(self) -> None:
        assert universe.lot_size_on("NIFTY", date(2024, 11, 20)) == 75

    def test_nifty_mid_2025_window_is_75(self) -> None:
        assert universe.lot_size_on("NIFTY", date(2025, 6, 1)) == 75

    def test_nifty_on_eve_of_second_reform_is_still_75(self) -> None:
        assert universe.lot_size_on("NIFTY", date(2025, 12, 29)) == 75

    def test_nifty_on_second_reform_date_drops_to_65(self) -> None:
        assert universe.lot_size_on("NIFTY", date(2025, 12, 30)) == 65

    def test_nifty_current_is_65(self) -> None:
        assert universe.lot_size_on("NIFTY", date(2026, 4, 21)) == 65

    def test_banknifty_pre_reform_is_15(self) -> None:
        assert universe.lot_size_on("BANKNIFTY", date(2022, 6, 15)) == 15

    def test_banknifty_post_first_reform_is_30(self) -> None:
        assert universe.lot_size_on("BANKNIFTY", date(2025, 6, 1)) == 30

    def test_banknifty_current_unchanged_after_second_reform(self) -> None:
        # BANKNIFTY did not change on 2025-12-30 — must stay 30.
        assert universe.lot_size_on("BANKNIFTY", date(2026, 4, 21)) == 30

    def test_lowercase_name_accepted(self) -> None:
        assert universe.lot_size_on("nifty", date(2022, 6, 15)) == 25

    def test_unknown_underlying_raises_keyerror(self) -> None:
        with pytest.raises(KeyError):
            universe.lot_size_on("SENSEX", date(2022, 6, 15))

    def test_far_past_returns_earliest_regime(self) -> None:
        # Dhan data starts 2020-08. Anything earlier is irrelevant to this platform,
        # but the lookup must still be total — returning the earliest known lot size.
        assert universe.lot_size_on("NIFTY", date(2019, 1, 1)) == 25


class TestInvariants:
    """Guard against drift between the static REGISTRY and the historical lookup."""

    def test_registry_nifty_lot_matches_current_lookup(self) -> None:
        under = universe.get("NIFTY")
        assert under.lot_size == universe.lot_size_on("NIFTY", date(2026, 4, 21))

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "REGISTRY.BANKNIFTY.lot_size=35 disagrees with docs/india-fno-nuances.md "
            "§2 (NSE Circular FAOP70616) which states BANKNIFTY=30 post-2024-11-20. "
            "Requires fresh NSE circular lookup to reconcile; tracked as a follow-up "
            "to the data expansion audit (2026-04-21). Do not silently change either "
            "side — the discrepancy itself is the finding."
        ),
    )
    def test_registry_banknifty_lot_matches_current_lookup(self) -> None:
        under = universe.get("BANKNIFTY")
        assert under.lot_size == universe.lot_size_on("BANKNIFTY", date(2026, 4, 21))
