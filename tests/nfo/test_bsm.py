"""Analytic put-delta sanity checks."""
from __future__ import annotations

import math

from nfo.bsm import put_delta, put_prob_otm


def test_atm_delta_near_half() -> None:
    # ATM 35 DTE 15% vol, zero dividend, 6.5% rate → put delta ≈ -0.45
    d = put_delta(spot=24000, strike=24000, years_to_expiry=35 / 365, sigma=0.15)
    assert -0.55 < d < -0.35, d


def test_deep_otm_put_delta_near_zero() -> None:
    d = put_delta(spot=24000, strike=22000, years_to_expiry=35 / 365, sigma=0.15)
    assert -0.15 < d < 0, d


def test_deep_itm_put_delta_near_minus_one() -> None:
    d = put_delta(spot=22000, strike=24000, years_to_expiry=35 / 365, sigma=0.15)
    assert d < -0.85, d


def test_expired_intrinsic_branch() -> None:
    assert put_delta(24000, 25000, years_to_expiry=0, sigma=0.15) == -1.0
    assert put_delta(24000, 23000, years_to_expiry=0, sigma=0.15) == 0.0
    # Negative T falls through the same branch.
    assert put_delta(24000, 25000, years_to_expiry=-0.01, sigma=0.15) == -1.0


def test_sigma_bounded() -> None:
    # sigma=0 would blow up; the clamp to 0.05 keeps the formula defined.
    d = put_delta(24000, 23800, years_to_expiry=35 / 365, sigma=0.0)
    assert -1 <= d <= 0


def test_delta_monotone_in_strike() -> None:
    # Higher strike → deeper put delta (more negative).
    t = 35 / 365
    d_low = put_delta(24000, 23500, years_to_expiry=t, sigma=0.15)
    d_mid = put_delta(24000, 24000, years_to_expiry=t, sigma=0.15)
    d_high = put_delta(24000, 24500, years_to_expiry=t, sigma=0.15)
    assert d_low > d_mid > d_high


# ── put_prob_otm: the real N(d₂) vs the 1 − |Δ| heuristic ───────────────────


def test_put_prob_otm_bounded_in_unit_interval() -> None:
    p = put_prob_otm(24000, 23500, years_to_expiry=35 / 365, sigma=0.18)
    assert 0.0 < p < 1.0


def test_put_prob_otm_monotone_in_strike() -> None:
    # Higher strike (deeper ITM put) → lower probability the put finishes OTM.
    t = 35 / 365
    p_low = put_prob_otm(24000, 23000, years_to_expiry=t, sigma=0.18)
    p_mid = put_prob_otm(24000, 24000, years_to_expiry=t, sigma=0.18)
    p_high = put_prob_otm(24000, 25000, years_to_expiry=t, sigma=0.18)
    assert p_low > p_mid > p_high


def test_put_prob_otm_expiry_indicator() -> None:
    # At expiry the BS risk-neutral probability collapses to an indicator:
    # OTM (spot ≥ strike) → 1.0, ITM → 0.0.
    assert put_prob_otm(24000, 23500, years_to_expiry=0, sigma=0.18) == 1.0
    assert put_prob_otm(24000, 24000, years_to_expiry=0, sigma=0.18) == 1.0
    assert put_prob_otm(24000, 24500, years_to_expiry=0, sigma=0.18) == 0.0


def test_put_prob_otm_differs_from_delta_heuristic() -> None:
    # This is the mathematical claim from the regime_watch finding: `1 − |Δ|`
    # (which equals N(d₁) ignoring dividends) over-states the true expiry
    # probability (N(d₂) = N(d₁ − σ√T)). The gap is σ√T in z-space — small
    # but non-zero at 35 DTE / 18 % vol.
    spot, strike, t, sigma = 24000, 23500, 35 / 365, 0.18
    delta = put_delta(spot, strike, years_to_expiry=t, sigma=sigma)
    heuristic = 1.0 - abs(delta)
    exact = put_prob_otm(spot, strike, years_to_expiry=t, sigma=sigma)
    assert heuristic > exact           # systematic over-statement
    assert abs(heuristic - exact) < 0.05  # bounded: σ√T ≈ 0.067 → tiny pp diff
    assert abs(heuristic - exact) > 0.005  # but not zero — catches "no-op fix"


def test_put_prob_otm_zero_spot_degenerate() -> None:
    # Guard branch — pathological inputs must return a finite value without
    # blowing up on log(0) so upstream callers can degrade gracefully.
    p = put_prob_otm(0.0, 100.0, years_to_expiry=35 / 365, sigma=0.2)
    assert math.isfinite(p)
    assert 0.0 <= p <= 1.0
