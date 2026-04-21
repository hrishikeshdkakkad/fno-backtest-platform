"""Black-Scholes-Merton pricing and delta/IV for European puts.

American-style puts trade at a slight premium to European puts when there's
early-exercise value (mostly on deep ITM puts when rates or dividends matter).
For short puts at reasonable deltas (0.15-0.35), the European approximation is
tight enough for strike selection and delta estimation — the goal here is not
to price with tick precision but to pick strikes consistently across history.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.stats import norm

SQRT_2PI = math.sqrt(2.0 * math.pi)


@dataclass(slots=True)
class PutResult:
    price: float
    delta: float  # negative for a long put; -0.3 is a typical "0.30-delta" short put
    iv: float


def _d1_d2(s: float, k: float, t: float, r: float, q: float, sigma: float) -> tuple[float, float]:
    if sigma <= 0 or t <= 0:
        return float("nan"), float("nan")
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    return d1, d2


def put_price(s: float, k: float, t: float, r: float, q: float, sigma: float) -> float:
    if t <= 0:
        return max(k - s, 0.0)
    d1, d2 = _d1_d2(s, k, t, r, q, sigma)
    return k * math.exp(-r * t) * norm.cdf(-d2) - s * math.exp(-q * t) * norm.cdf(-d1)


def put_delta(s: float, k: float, t: float, r: float, q: float, sigma: float) -> float:
    if t <= 0:
        return -1.0 if s < k else 0.0
    d1, _ = _d1_d2(s, k, t, r, q, sigma)
    return math.exp(-q * t) * (norm.cdf(d1) - 1.0)


def implied_vol_put(
    market_price: float,
    s: float,
    k: float,
    t: float,
    r: float = 0.04,
    q: float = 0.0,
    tol: float = 1e-4,
    max_iter: int = 100,
) -> float:
    """Solve for IV of a European put using Brent's method on [1e-4, 5.0]."""
    if market_price <= 0 or t <= 0:
        return float("nan")
    intrinsic = max(k - s, 0.0)
    if market_price < intrinsic * math.exp(-r * t):
        return float("nan")

    lo, hi = 1e-4, 5.0
    flo = put_price(s, k, t, r, q, lo) - market_price
    fhi = put_price(s, k, t, r, q, hi) - market_price
    if flo * fhi > 0:
        return float("nan")
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        fmid = put_price(s, k, t, r, q, mid) - market_price
        if abs(fmid) < tol:
            return mid
        if flo * fmid < 0:
            hi, fhi = mid, fmid
        else:
            lo, flo = mid, fmid
    return 0.5 * (lo + hi)
