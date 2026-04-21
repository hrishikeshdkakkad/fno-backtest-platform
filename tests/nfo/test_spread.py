"""Per-share payoff math for put credit spreads."""
from __future__ import annotations

from nfo.spread import spread_payoff_per_share


def test_expired_worthless_when_spot_above_short() -> None:
    pnl, outcome = spread_payoff_per_share(
        short_strike=22500, long_strike=22000, net_credit=40, spot_at_expiry=23000
    )
    assert outcome == "expired_worthless"
    assert pnl == 40


def test_partial_loss_between_strikes() -> None:
    # Spot at 22300 with short 22500 → intrinsic on short = 200
    # long at 22000 still OTM → 0. pnl_per_share = credit - 200
    pnl, outcome = spread_payoff_per_share(22500, 22000, net_credit=40, spot_at_expiry=22300)
    assert outcome == "partial_loss"
    assert pnl == 40 - 200


def test_max_loss_below_long_strike() -> None:
    # Width 500, credit 40 → max loss per share = 460
    pnl, outcome = spread_payoff_per_share(22500, 22000, net_credit=40, spot_at_expiry=21500)
    assert outcome == "max_loss"
    assert pnl == 40 - 500


def test_break_even_at_short_minus_credit() -> None:
    credit = 40
    short = 22500
    pnl, outcome = spread_payoff_per_share(short, 22000, net_credit=credit, spot_at_expiry=short - credit)
    assert outcome == "partial_loss"
    assert pnl == 0
