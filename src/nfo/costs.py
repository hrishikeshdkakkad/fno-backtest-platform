"""Per-trade transaction cost model for NFO options.

Rates cross-referenced against NSE/SEBI circulars and broker explainers as
of docs/india-fno-nuances.md §3 (2026-04-20). Indian F&O costs have short
half-lives (STT changed in Budget 2024 and may change again in Budget
2026) — verify against the current NSE circular before production sizing.

Unit convention
---------------
All function signatures take `premium` in the same units as the exchange
quotes it (rupees per share of the underlying index). `lot` is the lot
size. Costs come back in rupees, at the whole-contract level.

What's modelled
---------------
STT
    - Option SALE: 0.1% of premium × lot (seller pays, both legs).
    - Settlement: 0.125% of intrinsic × lot on ITM auto-exercise (long-leg
      holder pays). Only applied when a leg is settled (not closed before
      expiry); see `settlement_cost`.
Exchange / regulator fees
    - NSE transaction charge: 0.0503% of premium.
    - SEBI turnover fee:       0.0001% of premium.
    - Stamp duty:              0.003% of premium (buy side only).
GST
    - 18% on (brokerage + NSE + SEBI fees). NOT on STT / stamp duty.
Brokerage
    - Dhan flat ₹20 per executed order. A spread is four orders
      (short-entry, long-entry, short-exit, long-exit) → ₹80 round-trip,
      unless a leg expires and incurs no exit order (still pays STT_SETTLEMENT
      on ITM).

What's NOT modelled (deliberately)
----------------------------------
- Slippage / bid-ask cost inside the premium — backtest premiums are
  close-of-day prints, which under-count real execution drag.
- Impact cost on multi-lot orders — not material at $41k capital tier.
- The +2% expiry-day ELM (deferred Tier-2 per the plan).
"""
from __future__ import annotations


# ── Rate constants ──────────────────────────────────────────────────────────

STT_OPTION_SALE       = 0.001     # 0.1% of premium, seller pays
STT_OPTION_SETTLEMENT = 0.00125   # 0.125% of intrinsic, ITM auto-exercise holder
NSE_EXCHANGE_CHARGE   = 0.000503  # 0.0503% of premium
SEBI_TURNOVER_FEE     = 0.000001  # 0.0001% of premium
STAMP_DUTY_BUY        = 0.00003   # 0.003% of premium, buy side only
GST_RATE              = 0.18      # 18% on (brokerage + NSE + SEBI fees)
DHAN_FLAT_BROKERAGE   = 20.0      # ₹ per executed order


# ── Per-leg cost helpers ────────────────────────────────────────────────────


def _premium_scaled_fees(premium: float, lot: int) -> tuple[float, float, float]:
    """Return (nse_fee, sebi_fee, gst_on_fees) for one leg of `lot` contracts
    at `premium` per share. Brokerage is added separately at the order level.
    """
    notional = max(0.0, float(premium)) * max(0, int(lot))
    nse = notional * NSE_EXCHANGE_CHARGE
    sebi = notional * SEBI_TURNOVER_FEE
    # GST applies to brokerage + NSE + SEBI (not STT or stamp duty).
    gst = (nse + sebi + DHAN_FLAT_BROKERAGE) * GST_RATE
    return nse, sebi, gst


def leg_entry_cost(premium: float, lot: int, side: str) -> float:
    """One-side entry cost for one leg.

    side == "sell" → seller pays STT on option sale, no stamp duty.
    side == "buy"  → buyer pays stamp duty, no sale STT.
    Both pay NSE + SEBI + brokerage + GST.
    """
    notional = max(0.0, float(premium)) * max(0, int(lot))
    nse, sebi, gst = _premium_scaled_fees(premium, lot)
    brokerage = DHAN_FLAT_BROKERAGE
    if side == "sell":
        stt = notional * STT_OPTION_SALE
        stamp = 0.0
    elif side == "buy":
        stt = 0.0
        stamp = notional * STAMP_DUTY_BUY
    else:
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    return stt + nse + sebi + stamp + brokerage + gst


def leg_exit_cost(premium: float, lot: int, side: str) -> float:
    """One-side exit cost. Exit flips the side: closing a short = buy-to-cover
    (buy side → stamp duty, no sale STT); closing a long = sell-to-close
    (sell side → sale STT, no stamp duty).
    """
    flipped = "buy" if side == "sell" else "sell" if side == "buy" else side
    return leg_entry_cost(premium, lot, flipped)


def settlement_cost(intrinsic: float, lot: int) -> float:
    """Settlement STT on an ITM long leg that auto-exercises.

    The short-leg buyer (counterparty) pays their own settlement STT — it's
    not your cost. Pass positive intrinsic only (0 if OTM / ATM).
    """
    if intrinsic <= 0:
        return 0.0
    return float(intrinsic) * max(0, int(lot)) * STT_OPTION_SETTLEMENT


# ── Spread round-trip ──────────────────────────────────────────────────────


def spread_roundtrip_cost(
    *,
    short_entry_premium: float,
    short_exit_premium: float,
    long_entry_premium: float,
    long_exit_premium: float,
    lot: int,
    closed_before_expiry: bool = True,
    settle_intrinsic_short: float = 0.0,
    settle_intrinsic_long: float = 0.0,
) -> float:
    """Total round-trip cost for one put credit spread contract.

    `closed_before_expiry=True` (the recommended default per the Oct-2024
    SEBI framework) means both legs get an explicit exit order; no
    settlement STT is incurred. `False` means the position auto-exercised at
    expiry — exit orders/brokerage are NOT charged for settled legs, but
    settlement STT applies to whichever leg was ITM.

    `settle_intrinsic_short` is the counterparty's cost when the short leg is
    ITM, so we leave it at zero — it's not your cost. Kept as a parameter
    only to document that the caller may have computed it.
    """
    # Entry: short sold, long bought.
    entry = leg_entry_cost(short_entry_premium, lot, "sell") + \
            leg_entry_cost(long_entry_premium, lot, "buy")
    if closed_before_expiry:
        # Exit: short bought back, long sold back.
        exit_ = leg_exit_cost(short_exit_premium, lot, "sell") + \
                leg_exit_cost(long_exit_premium, lot, "buy")
        settle = 0.0
    else:
        # Neither leg has an explicit exit order — settled by exchange.
        # Only the long-leg holder pays settlement STT on ITM.
        exit_ = 0.0
        settle = settlement_cost(settle_intrinsic_long, lot)
    return entry + exit_ + settle
