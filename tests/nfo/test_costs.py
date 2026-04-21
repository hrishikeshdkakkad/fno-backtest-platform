"""Transaction-cost model sanity checks (docs/india-fno-nuances.md §3)."""
from __future__ import annotations

import pytest

from nfo import costs


def test_option_sale_stt_charged_on_sell_not_buy() -> None:
    # Sell leg pays STT on premium; buy leg does not.
    sell_cost = costs.leg_entry_cost(100.0, 65, "sell")
    buy_cost = costs.leg_entry_cost(100.0, 65, "buy")
    # Sell-leg STT = 0.1% × 100 × 65 = 6.50
    # Buy-leg stamp = 0.003% × 100 × 65 = 0.195
    # Both pay NSE + SEBI + brokerage + GST so the delta is (STT_sell − stamp_buy).
    assert sell_cost - buy_cost == pytest.approx(6.50 - 0.195, rel=1e-6)


def test_leg_entry_cost_rejects_invalid_side() -> None:
    with pytest.raises(ValueError):
        costs.leg_entry_cost(100.0, 65, "neither")


def test_leg_exit_flips_side() -> None:
    # Closing a short is a buy-back (buy-side costs); closing a long is a sell.
    close_short = costs.leg_exit_cost(100.0, 65, "sell")
    close_long = costs.leg_exit_cost(100.0, 65, "buy")
    assert close_short == pytest.approx(costs.leg_entry_cost(100.0, 65, "buy"))
    assert close_long == pytest.approx(costs.leg_entry_cost(100.0, 65, "sell"))


def test_settlement_cost_only_itm() -> None:
    assert costs.settlement_cost(0.0, 65) == 0.0
    assert costs.settlement_cost(-5.0, 65) == 0.0     # negative intrinsic → 0
    # ITM 50 pts × 65 lot × 0.125% = 4.0625
    assert costs.settlement_cost(50.0, 65) == pytest.approx(4.0625, rel=1e-6)


def test_spread_roundtrip_closed_vs_settled_otm() -> None:
    """Closing both legs is more expensive than letting both expire OTM —
    four order-level brokerage & GST hits vs two.
    """
    closed = costs.spread_roundtrip_cost(
        short_entry_premium=150, short_exit_premium=30,
        long_entry_premium=50, long_exit_premium=15,
        lot=65, closed_before_expiry=True,
    )
    otm_settled = costs.spread_roundtrip_cost(
        short_entry_premium=150, short_exit_premium=0,
        long_entry_premium=50, long_exit_premium=0,
        lot=65, closed_before_expiry=False, settle_intrinsic_long=0,
    )
    assert otm_settled < closed


def test_spread_roundtrip_itm_settlement_adds_stt() -> None:
    """ITM long leg auto-exercising at expiry triggers the settlement-STT
    trap (docs §3). Cost must exceed the OTM-settled equivalent.
    """
    otm = costs.spread_roundtrip_cost(
        short_entry_premium=150, short_exit_premium=0,
        long_entry_premium=50, long_exit_premium=0,
        lot=65, closed_before_expiry=False, settle_intrinsic_long=0,
    )
    itm = costs.spread_roundtrip_cost(
        short_entry_premium=150, short_exit_premium=0,
        long_entry_premium=50, long_exit_premium=0,
        lot=65, closed_before_expiry=False, settle_intrinsic_long=50,
    )
    # Difference should equal exactly one settlement-STT charge on 50 pts × 65.
    assert itm - otm == pytest.approx(50.0 * 65 * costs.STT_OPTION_SETTLEMENT, rel=1e-6)


def test_cost_band_matches_doc_estimate() -> None:
    """Round-trip cost on a typical NIFTY spread (doc §3: ₹100/sh credit)
    should land in the doc-stated 1.5-2% band.
    """
    credit_per_share = 100.0
    credit_total = credit_per_share * 65
    cost = costs.spread_roundtrip_cost(
        short_entry_premium=150, short_exit_premium=30,
        long_entry_premium=50, long_exit_premium=15,
        lot=65, closed_before_expiry=True,
    )
    pct = cost / credit_total
    assert 0.01 < pct < 0.025, f"cost band {pct:.3%} outside doc estimate"
