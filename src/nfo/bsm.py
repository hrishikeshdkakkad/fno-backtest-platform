"""Analytic Black-Scholes put delta — no solver needed.

Dhan's rollingoption returns IV per candle, so we never need to back-solve from
option price. All we do is: plug (S, K, T, r, q, σ) into the Black-Scholes
closed form and return the delta.

σ from the wire is noisy on illiquid deep OTM candles (we've seen 0.0 and 65.0
outliers). Callers should either sanity-bound σ in their own pipeline or pass
already-filtered values. This module bounds σ to [0.05, 1.5] as a last-ditch
guard but doesn't try to recover from true outliers.
"""
from __future__ import annotations

import math

from .config import RISK_FREE_RATE

_SIGMA_MIN = 0.05
_SIGMA_MAX = 1.5


def _phi(x: float) -> float:
    """Standard-normal CDF via erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def put_delta(
    spot: float,
    strike: float,
    years_to_expiry: float,
    sigma: float,
    *,
    risk_free: float = RISK_FREE_RATE,
    div_yield: float = 0.0,
) -> float:
    """Return analytic European put delta. Always ≤ 0.

    At or past expiry: intrinsic regime. Put is −1 if ITM, 0 if OTM/ATM.
    """
    if years_to_expiry <= 0:
        return -1.0 if spot < strike else 0.0
    sigma = max(_SIGMA_MIN, min(_SIGMA_MAX, float(sigma)))
    if spot <= 0 or strike <= 0:
        return 0.0
    sqrt_t = math.sqrt(years_to_expiry)
    d1 = (math.log(spot / strike) + (risk_free - div_yield + 0.5 * sigma * sigma) * years_to_expiry) / (sigma * sqrt_t)
    return math.exp(-div_yield * years_to_expiry) * (_phi(d1) - 1.0)


def put_prob_otm(
    spot: float,
    strike: float,
    years_to_expiry: float,
    sigma: float,
    *,
    risk_free: float = RISK_FREE_RATE,
    div_yield: float = 0.0,
) -> float:
    """Risk-neutral probability that a European put finishes OTM — N(d₂).

    The delta-heuristic `1 − |Δ|` equals `N(d₁)` (ignoring dividends); the true
    expiry probability is `N(d₂) = N(d₁ − σ√T)`. For σ·√T ≈ 0.07 (18 % vol,
    35 DTE NIFTY) the two differ by a few percentage points — small enough to
    call "approximately equal" in casual use, too big to call "exact" when
    sizing positions against it.

    At or past expiry: indicator. Returns 1.0 if `spot ≥ strike` (OTM terminal),
    else 0.0.
    """
    if years_to_expiry <= 0:
        return 1.0 if spot >= strike else 0.0
    sigma = max(_SIGMA_MIN, min(_SIGMA_MAX, float(sigma)))
    if spot <= 0 or strike <= 0:
        return 0.0
    sqrt_t = math.sqrt(years_to_expiry)
    d1 = (math.log(spot / strike) + (risk_free - div_yield + 0.5 * sigma * sigma) * years_to_expiry) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return _phi(d2)
