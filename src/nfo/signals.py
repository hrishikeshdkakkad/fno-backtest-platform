"""Derived regime & options-chain signals — pure math, no network calls.

Everything here is deterministic and side-effect-free. Callers pass in the
series they already have (VIX history, underlying daily bars, chain snapshot)
and receive scalars or dataclasses that the regime engine combines.

Grouped by concern:
  • IV-rank / IV-percentile   — where does today's IV sit in its history?
  • Realized-vol indicators   — ATR, ADX, RSI, EMA slope.
  • Options-chain metrics     — skew (25Δ put vs 25Δ call), strike-specific IV.
  • VIX term structure        — fast vs slow EMA of spot VIX.
  • Composite                 — weighted-sum grader over the signal dict.

All functions tolerate short / NaN-heavy inputs and fall back to NaN rather
than raising, so a partial chain or a market-holiday gap does not crash the
live TUI.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from . import bsm


# ── IV rank / percentile ────────────────────────────────────────────────────


def iv_rank(series: pd.Series | Sequence[float], lookback: int = 252) -> float:
    """(x − min) / (max − min) over last `lookback` points. Returns NaN if
    series is empty or flat."""
    s = _tail(series, lookback)
    if len(s) < 2:
        return float("nan")
    lo, hi = float(np.nanmin(s)), float(np.nanmax(s))
    if hi <= lo:
        return float("nan")
    return float((s[-1] - lo) / (hi - lo))


def iv_percentile(series: pd.Series | Sequence[float], lookback: int = 252) -> float:
    """ECDF rank of the last observation in `series` over `lookback` points."""
    s = _tail(series, lookback)
    if len(s) < 2:
        return float("nan")
    today = s[-1]
    return float(np.mean(s <= today))


# ── Calendar-structure signals ──────────────────────────────────────────────
#
# India VIX has strong day-of-week and month-of-year seasonality documented in
# peer-reviewed work (Shaikh & Padhi 2014 on 1,361 trading days; Akhtar et al
# 2017 replication). Monday VIX return averages +2.44% (p<0.001); Tuesday
# -1.29% to -1.52% (p<0.001); expiry-day -2.64% (p<0.001). Month effects: May
# positive (pre-election/budget), March & December negative (fiscal-year
# position clearing). These helpers are pure functions of the entry date —
# cheap to add, high Tier-1 ROI per docs/india-fno-nuances.md §4 + §8.


def day_of_week_score(
    entry_date: date,
    *,
    recent_expiries: Iterable[date] = (),
    post_expiry_window: int = 2,
) -> int:
    """Return -1 / 0 / +1 based on entry timing structure.

    Monday entries (-1):
        Shaikh-Padhi: Monday VIX return +2.44% (p<0.001). Selling into Monday
        AM means you're selling the lowest VIX of the week right before the
        systematic upward move reprices premiums against you.

    Thursday within `post_expiry_window` sessions after a monthly expiry (+1):
        Expiry-day VIX return -2.64% (p<0.001); T+1 -2.14%; T+2 -0.57%. IV is
        momentarily compressed relative to its forward path, so selling now
        harvests the crush.

    Otherwise: 0.

    `recent_expiries` should be a collection of monthly-expiry dates (any
    iterable — we only check "did an expiry fall in the last N sessions").
    Pass an empty iterable in contexts where the expiry calendar isn't
    available (e.g. early backtest warmup); the Thursday bonus simply won't
    fire.
    """
    wd = entry_date.weekday()       # Mon=0 … Sun=6
    if wd == 0:
        return -1
    if wd == 3:                     # Thursday
        # "Within N calendar days of the most recent expiry" — NSE schedules
        # can push monthly expiry to Tue or Thu; either way we're asking
        # "was there a crush within the last `post_expiry_window` sessions?"
        # Using calendar days as a cheap session-count proxy (weekends land
        # between Tue-expiry and Thu-entry, which is fine: both weeklies and
        # monthlies crush on expiry day regardless).
        cutoff = entry_date - timedelta(days=post_expiry_window)
        for exp in recent_expiries:
            if cutoff <= exp <= entry_date:
                return +1
    return 0


def month_of_year_size_mult(entry_date: date) -> float:
    """Return a sizing multiplier for the cycle's entry month.

    May → 0.5   (pre-election / budget residual vol; doc §4 flags positive
                 VIX drift; reduce size rather than skip entirely to keep
                 the cycle count intact for statistics).
    Mar → 1.2   (fiscal-year-end position clearing; negative VIX drift).
    Dec → 1.2   (calendar-year-end position clearing; negative VIX drift).
    else → 1.0.

    Applied to `buying_power` (capital deployment), NOT to the regime grade.
    Keeps the signal layer pure and makes the effect auditable as a sizing
    decision.
    """
    m = entry_date.month
    if m == 5:
        return 0.5
    if m in (3, 12):
        return 1.2
    return 1.0


# ── Trend / momentum ────────────────────────────────────────────────────────


def atr(daily: pd.DataFrame, window: int = 14) -> pd.Series:
    """Wilder ATR from high/low/close. Expects columns high, low, close."""
    if daily.empty or not {"high", "low", "close"}.issubset(daily.columns):
        return pd.Series(dtype=float)
    high = daily["high"].astype(float)
    low = daily["low"].astype(float)
    close = daily["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return _wilder(tr, window)


def adx(daily: pd.DataFrame, window: int = 14) -> pd.Series:
    """Wilder ADX. Returns NaN for the first `2*window` bars."""
    if daily.empty or not {"high", "low", "close"}.issubset(daily.columns):
        return pd.Series(dtype=float)
    high = daily["high"].astype(float)
    low = daily["low"].astype(float)
    close = daily["close"].astype(float)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=daily.index)
    minus_dm = pd.Series(minus_dm, index=daily.index)
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_w = _wilder(tr, window)
    plus_di = 100.0 * _wilder(plus_dm, window) / atr_w.replace(0, np.nan)
    minus_di = 100.0 * _wilder(minus_dm, window) / atr_w.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return _wilder(dx, window)


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder RSI on a single close series.

    Edge-cases:
      avg_down == 0 and avg_up > 0  →  RSI = 100 (monotone uptrend)
      avg_up == 0 and avg_down > 0  →  RSI = 0   (monotone downtrend)
      both zero                     →  RSI = 50  (no information)
    """
    if close.empty:
        return pd.Series(dtype=float)
    diff = close.astype(float).diff()
    up = diff.clip(lower=0.0)
    down = (-diff).clip(lower=0.0)
    avg_up = _wilder(up, window)
    avg_down = _wilder(down, window)
    rs = avg_up / avg_down.where(avg_down > 0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # Repair divide-by-zero cells: if no losses, RSI pegs at 100; if no
    # gains, at 0; and if both are zero, at 50.
    no_losses = (avg_down == 0) & (avg_up > 0)
    no_gains = (avg_up == 0) & (avg_down > 0)
    both_zero = (avg_up == 0) & (avg_down == 0)
    out = out.mask(no_losses, 100.0).mask(no_gains, 0.0).mask(both_zero, 50.0)
    return out


def ema_slope(close: pd.Series, span: int = 20, lookback: int = 10) -> float:
    """Slope of the last `lookback` EMA points, normalised by current price.

    Returns a dimensionless number: +ve = up-trend, −ve = down-trend.
    Normalising by price lets you compare across underlyings.
    """
    if len(close) < span + lookback:
        return float("nan")
    ema = close.astype(float).ewm(span=span, adjust=False).mean()
    tail = ema.iloc[-lookback:]
    if tail.isna().any():
        return float("nan")
    x = np.arange(lookback, dtype=float)
    slope = np.polyfit(x, tail.values, 1)[0]
    denom = float(close.iloc[-1])
    if denom <= 0:
        return float("nan")
    return float(slope / denom)


def pullback_atr_scaled(spot: float, hi: float, atr_val: float) -> float:
    """(hi − spot) / atr. Bigger = deeper pullback in ATR units."""
    if atr_val is None or not np.isfinite(atr_val) or atr_val <= 0:
        return float("nan")
    return float((hi - spot) / atr_val)


@dataclass(slots=True)
class TrendRegime:
    trending_up: bool
    score: int          # 0..3 — how many of the 3 sub-checks passed
    ema20_over_ema50: bool
    adx_strong: bool
    rsi_above_40: bool
    ema20: float
    ema50: float
    adx14: float
    rsi14: float


def trend_regime(daily: pd.DataFrame, *, adx_floor: float = 20.0, rsi_floor: float = 40.0) -> TrendRegime:
    """Three-vote trend filter on the underlying daily bars.

    Passes when:
      1. EMA20 > EMA50          (directional)
      2. ADX-14 > adx_floor     (trend strength)
      3. RSI-14 > rsi_floor     (not in correction)

    Used as a "don't-sell-puts-into-a-downtrend" gate.
    """
    if daily.empty or "close" not in daily.columns:
        return TrendRegime(False, 0, False, False, False,
                           float("nan"), float("nan"), float("nan"), float("nan"))
    close = daily["close"].astype(float)
    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    adx_series = adx(daily, 14)
    adx_val = float(adx_series.iloc[-1]) if not adx_series.empty else float("nan")
    rsi_series = rsi(close, 14)
    rsi_val = float(rsi_series.iloc[-1]) if not rsi_series.empty else float("nan")

    checks = [
        ema20 > ema50,
        np.isfinite(adx_val) and adx_val > adx_floor,
        np.isfinite(rsi_val) and rsi_val > rsi_floor,
    ]
    score = sum(1 for c in checks if c)
    return TrendRegime(
        trending_up=(score == 3),
        score=score,
        ema20_over_ema50=checks[0],
        adx_strong=checks[1],
        rsi_above_40=checks[2],
        ema20=ema20,
        ema50=ema50,
        adx14=adx_val,
        rsi14=rsi_val,
    )


# ── Chain-derived metrics ───────────────────────────────────────────────────


def strike_iv(chain: pd.DataFrame, strike: float, opt_type: str = "PUT") -> float:
    """Look up IV for a specific strike in a chain.

    Chain is expected to carry `strike` and `iv` columns (as Dhan returns).
    When the column `option_type` exists we filter by it; otherwise we trust
    the caller passed a type-pure chain. IV comes off the wire in vol-points
    (e.g. 18.5 means 18.5 %); we return it in the same unit.

    Returns NaN if the strike isn't present or IV is missing.
    """
    if chain.empty or "strike" not in chain.columns or "iv" not in chain.columns:
        return float("nan")
    df = chain
    if "option_type" in chain.columns:
        df = df[df["option_type"].str.upper() == opt_type.upper()]
    row = df[df["strike"] == float(strike)]
    if row.empty:
        return float("nan")
    iv = row["iv"].iloc[0]
    if pd.isna(iv):
        return float("nan")
    return float(iv)


@dataclass(slots=True)
class SkewSnapshot:
    put_25d_strike: float
    put_25d_iv: float
    call_25d_strike: float
    call_25d_iv: float
    skew_vol_pts: float    # put_iv − call_iv (in vol points, e.g. 18.5)


def skew_25d(
    puts: pd.DataFrame,
    calls: pd.DataFrame,
    *,
    spot: float,
    years_to_expiry: float,
    target_delta: float = 0.25,
) -> SkewSnapshot:
    """Compute 25Δ put IV − 25Δ call IV — the classic risk-reversal skew.

    Each input must have `strike`, `iv` (vol-pts), `close` columns. We
    recompute analytic delta from wire IV so we don't trust whatever delta
    Dhan pre-computed. Falls back to NaNs when the chain can't supply a
    strike near target delta.
    """
    empty = SkewSnapshot(float("nan"), float("nan"), float("nan"), float("nan"), float("nan"))
    if puts.empty or calls.empty or years_to_expiry <= 0:
        return empty

    put_strike, put_iv = _delta_closest(puts, spot, years_to_expiry, target_delta, is_put=True)
    call_strike, call_iv = _delta_closest(calls, spot, years_to_expiry, target_delta, is_put=False)
    if not np.isfinite(put_iv) or not np.isfinite(call_iv):
        return SkewSnapshot(put_strike, put_iv, call_strike, call_iv, float("nan"))
    return SkewSnapshot(put_strike, put_iv, call_strike, call_iv, float(put_iv - call_iv))


# ── VIX term structure ──────────────────────────────────────────────────────


@dataclass(slots=True)
class TermStructure:
    fast_ema: float
    slow_ema: float
    slope: float             # (slow − fast) / fast, proxy for contango steepness
    contango: bool           # True when slow_ema > fast_ema (risk calm)


def term_structure(vix_hist: Sequence[float] | pd.Series, fast: int = 5, slow: int = 22) -> TermStructure:
    """Spot-VIX proxy for term structure.

    NSE does publish VIX futures but the free feed is flakey; until we wire
    that in, a fast-vs-slow EMA of spot VIX captures most of the signal.
    Backwardation (fast > slow) = fear is being priced into short-term IV
    faster than long-term IV → common near bottoms / before bounces →
    historically a bad time to be short vega.
    """
    s = _tail(vix_hist, max(slow * 3, slow))
    empty = TermStructure(float("nan"), float("nan"), float("nan"), False)
    if len(s) < slow:
        return empty
    series = pd.Series(s)
    fast_ema = float(series.ewm(span=fast, adjust=False).mean().iloc[-1])
    slow_ema = float(series.ewm(span=slow, adjust=False).mean().iloc[-1])
    if fast_ema <= 0:
        return TermStructure(fast_ema, slow_ema, float("nan"), slow_ema > fast_ema)
    slope = float((slow_ema - fast_ema) / fast_ema)
    return TermStructure(fast_ema, slow_ema, slope, slow_ema > fast_ema)


# ── Composite grader ────────────────────────────────────────────────────────


GRADE_LADDER = ["B-", "B", "B+", "A-", "A", "A+", "A++"]


def composite_score(
    passes: Mapping[str, bool],
    weights: Mapping[str, float] | None = None,
) -> tuple[float, str]:
    """Weighted sum of boolean signal passes → (score, grade).

    Default weights are 1.0 per signal — caller can override per signal to
    lean the grade on the signals that backtest calibration proved most
    predictive. With 8 signals at weight 1.0, score maxes at 8.0; we map
    to a 7-level ladder B- … A++.
    """
    w = dict(weights or {})
    score = 0.0
    total = 0.0
    for name, passed in passes.items():
        wv = float(w.get(name, 1.0))
        total += wv
        if passed:
            score += wv
    if total <= 0:
        return 0.0, GRADE_LADDER[0]
    frac = score / total
    idx = min(len(GRADE_LADDER) - 1, int(round(frac * (len(GRADE_LADDER) - 1))))
    return float(score), GRADE_LADDER[idx]


# ── Entry-timing indicators ─────────────────────────────────────────────────
#
# These answer a different question from the 8-signal regime grade:
# "within a favourable regime, is *now* the day to open the trade?"
# Orthogonal to the grade — we return a 0-100 composite score so the live
# dashboard can render both numbers side by side. Daily timeframe only.


@dataclass(slots=True)
class BollingerSnap:
    sma: float
    upper: float
    lower: float
    bandwidth: float       # (upper − lower) / sma in percent
    z_score: float         # (spot − sma) / (nstd · σ); in [-1,+1] = inside band
    squeeze: bool          # bandwidth ≤ 20th percentile of recent lookback


def bollinger_bands(
    close: pd.Series,
    window: int = 20,
    nstd: float = 2.0,
    squeeze_lookback: int = 120,
) -> BollingerSnap:
    """Classic Bollinger (20, 2σ) plus Bollinger squeeze detection.

    Squeeze = bandwidth at/below its own 20th percentile over the last
    `squeeze_lookback` bars (John Bollinger's original rule of thumb).
    When squeeze is True, vol is compressed and expansion is loading;
    combined with a directional signal, this often marks timing inflections.
    """
    empty = BollingerSnap(float("nan"), float("nan"), float("nan"),
                          float("nan"), float("nan"), False)
    if close is None or close.empty or len(close) < window:
        return empty
    series = close.astype(float)
    sma = series.rolling(window).mean()
    std = series.rolling(window).std(ddof=0)
    upper_s = sma + nstd * std
    lower_s = sma - nstd * std
    bw = (upper_s - lower_s) / sma.replace(0, np.nan) * 100.0

    last_sma = float(sma.iloc[-1])
    last_std = float(std.iloc[-1])
    last_upper = float(upper_s.iloc[-1])
    last_lower = float(lower_s.iloc[-1])
    last_bw = float(bw.iloc[-1])
    spot = float(series.iloc[-1])
    denom = nstd * last_std
    z = float((spot - last_sma) / denom) if denom > 0 else float("nan")

    squeeze = False
    if len(bw.dropna()) >= max(squeeze_lookback // 2, 20):
        recent = bw.iloc[-squeeze_lookback:].dropna()
        if len(recent) > 0:
            squeeze = bool(last_bw <= float(np.quantile(recent, 0.20)))
    return BollingerSnap(last_sma, last_upper, last_lower, last_bw, z, squeeze)


@dataclass(slots=True)
class MACDSnap:
    macd_line: float
    signal_line: float
    histogram: float
    state: str             # rising_pos | rising_neg | falling_pos | falling_neg | unknown


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> MACDSnap:
    """MACD(12, 26, 9). Reports histogram + one-word state.

    The 4-state classification (rising/falling × positive/negative histogram)
    lets consumers weight "rising_neg" — the moment histogram flips up from
    below zero — as the highest-conviction momentum inflection. Using the
    histogram's direction rather than the signal-line crossover removes the
    well-known lag of the raw crossover trigger.
    """
    empty = MACDSnap(float("nan"), float("nan"), float("nan"), "unknown")
    if close is None or close.empty or len(close) < slow + signal:
        return empty
    series = close.astype(float)
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    sig_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - sig_line

    last_macd = float(macd_line.iloc[-1])
    last_signal = float(sig_line.iloc[-1])
    last_hist = float(hist.iloc[-1])
    prev_hist = float(hist.iloc[-2]) if len(hist) >= 2 else float("nan")

    if not np.isfinite(last_hist) or not np.isfinite(prev_hist):
        state = "unknown"
    else:
        rising = last_hist > prev_hist
        pos = last_hist > 0
        state = ("rising_pos" if rising and pos else
                 "rising_neg" if rising and not pos else
                 "falling_pos" if not rising and pos else
                 "falling_neg")
    return MACDSnap(last_macd, last_signal, last_hist, state)


@dataclass(slots=True)
class StochasticSnap:
    k: float
    d: float
    state: str             # oversold_turning_up | oversold | neutral |
                           # overbought_turning_down | overbought | unknown


def stochastic(
    daily: pd.DataFrame,
    k_window: int = 14,
    d_window: int = 3,
) -> StochasticSnap:
    """Slow Stochastic (%K smoothed by d_window, %D = SMA of %K).

    We classify not just the level but direction: `oversold_turning_up` —
    %K below 20 AND above %D AND rising — is the highest-conviction
    dip-buy timing cue for the regime we care about.
    """
    empty = StochasticSnap(float("nan"), float("nan"), "unknown")
    if (daily is None or daily.empty
            or not {"high", "low", "close"}.issubset(daily.columns)
            or len(daily) < k_window + d_window):
        return empty
    high = daily["high"].astype(float)
    low = daily["low"].astype(float)
    close = daily["close"].astype(float)
    low_n = low.rolling(k_window).min()
    high_n = high.rolling(k_window).max()
    range_n = (high_n - low_n).replace(0, np.nan)
    k_raw = 100.0 * (close - low_n) / range_n
    k_smooth = k_raw.rolling(d_window).mean()
    d_series = k_smooth.rolling(d_window).mean()

    last_k = float(k_smooth.iloc[-1])
    last_d = float(d_series.iloc[-1])
    prev_k = float(k_smooth.iloc[-2]) if len(k_smooth) >= 2 else float("nan")
    if not (np.isfinite(last_k) and np.isfinite(last_d)):
        return empty

    if last_k < 20:
        turning_up = (np.isfinite(prev_k) and last_k > prev_k and last_k > last_d)
        state = "oversold_turning_up" if turning_up else "oversold"
    elif last_k > 80:
        turning_down = (np.isfinite(prev_k) and last_k < prev_k and last_k < last_d)
        state = "overbought_turning_down" if turning_down else "overbought"
    else:
        state = "neutral"
    return StochasticSnap(last_k, last_d, state)


@dataclass(slots=True)
class EntryTimingSnap:
    score: float           # 0-100, NaN if insufficient history
    grade: str             # Strong | Good | Neutral | Weak | Avoid | Unknown
    bb_z: float
    bb_squeeze: bool
    bb_bandwidth: float
    macd_histogram: float
    macd_state: str
    stoch_k: float
    stoch_d: float
    stoch_state: str
    reasoning: list[str]


# Scoring maps — tuned to favour the dip-in-uptrend / inflection-turning-up
# configuration that matches a short-put seller's ideal entry.
_BB_POSITION_POINTS = {
    "pullback":      (-1.5, -0.5, 30),   # in-band pullback — best window
    "midline":       (-0.5,  0.5, 20),   # near SMA — acceptable
    "stretched":     ( 0.5,  1.5, 10),   # stretched up — wait
    "overextended":  ( 1.5, 99.0, 0),    # overextended — avoid
    "breakdown":     (-99.0, -1.5, 10),  # breakdown risk — small credit only
}
_MACD_POINTS = {
    "rising_neg": 30,     # histogram flipping up from below zero — inflection
    "rising_pos": 25,     # confirmed momentum up
    "falling_pos": 15,    # positive but decelerating
    "falling_neg": 5,     # falling and negative — breakdown momentum
    "unknown": 0,
}
_STOCH_POINTS = {
    "oversold_turning_up": 30,
    "neutral": 20,
    "oversold": 10,
    "overbought_turning_down": 5,
    "overbought": 0,
    "unknown": 0,
}


def entry_timing_score(daily: pd.DataFrame) -> EntryTimingSnap:
    """Composite 0-100 score combining Bollinger + MACD + Stochastic.

    Answers "within a favourable regime, is *now* a good entry day?" —
    orthogonal to the 8-signal regime grade. Returns NaN score + "Unknown"
    grade if the input has fewer than ~30 daily bars.
    """
    if daily is None or daily.empty or len(daily) < 30:
        return EntryTimingSnap(
            float("nan"), "Unknown", float("nan"), False, float("nan"),
            float("nan"), "unknown", float("nan"), float("nan"), "unknown",
            ["insufficient history (<30 bars)"],
        )

    bb = bollinger_bands(daily["close"], window=20, nstd=2.0)
    mac = macd(daily["close"], fast=12, slow=26, signal=9)
    st = stochastic(daily, k_window=14, d_window=3)

    reasoning: list[str] = []

    # Bollinger position (max 30) + squeeze bonus (max 10 → 10 or 5).
    bb_pts, bucket = 0, "unknown"
    if np.isfinite(bb.z_score):
        for name, (lo, hi, pts) in _BB_POSITION_POINTS.items():
            if lo <= bb.z_score <= hi:
                bb_pts, bucket = pts, name
                break
    reasoning.append(
        f"BB z={bb.z_score:+.2f} ({bucket}) +{bb_pts}"
        if np.isfinite(bb.z_score) else "BB unavailable +0"
    )
    squeeze_pts = 10 if bb.squeeze else 5
    reasoning.append(
        f"BB bandwidth {bb.bandwidth:.1f}% {'(SQUEEZE)' if bb.squeeze else ''} "
        f"+{squeeze_pts}"
    )

    # MACD (max 30).
    macd_pts = _MACD_POINTS.get(mac.state, 0)
    reasoning.append(f"MACD {mac.state} hist={mac.histogram:+.1f} +{macd_pts}")

    # Stochastic (max 30).
    stoch_pts = _STOCH_POINTS.get(st.state, 0)
    reasoning.append(
        f"Stoch %K={st.k:.0f} %D={st.d:.0f} ({st.state}) +{stoch_pts}"
    )

    total = float(bb_pts + squeeze_pts + macd_pts + stoch_pts)

    # Grade thresholds — stable across small score perturbations.
    if total >= 75:
        grade = "Strong"
    elif total >= 60:
        grade = "Good"
    elif total >= 40:
        grade = "Neutral"
    elif total >= 25:
        grade = "Weak"
    else:
        grade = "Avoid"

    return EntryTimingSnap(
        score=total, grade=grade,
        bb_z=bb.z_score, bb_squeeze=bb.squeeze, bb_bandwidth=bb.bandwidth,
        macd_histogram=mac.histogram, macd_state=mac.state,
        stoch_k=st.k, stoch_d=st.d, stoch_state=st.state,
        reasoning=reasoning,
    )


# ── Private helpers ─────────────────────────────────────────────────────────


def _tail(series: pd.Series | Sequence[float], n: int) -> np.ndarray:
    if isinstance(series, pd.Series):
        arr = series.to_numpy(dtype=float)
    else:
        arr = np.asarray(list(series), dtype=float)
    if arr.size == 0:
        return arr
    return arr[-n:]


def _wilder(series: pd.Series, window: int) -> pd.Series:
    """Wilder's smoothing = EMA with α = 1/window."""
    return series.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def _delta_closest(
    df: pd.DataFrame,
    spot: float,
    years_to_expiry: float,
    target: float,
    *,
    is_put: bool,
) -> tuple[float, float]:
    """Return (strike, iv) for the row whose |analytic delta| is closest to target."""
    if df.empty or "strike" not in df.columns or "iv" not in df.columns:
        return float("nan"), float("nan")
    d = df[df["iv"].notna() & (df["iv"] > 0)].copy()
    if "close" in d.columns:
        d = d[d["close"] > 0]
    if d.empty:
        return float("nan"), float("nan")
    if is_put:
        d["delta"] = d.apply(
            lambda r: bsm.put_delta(spot, float(r["strike"]), years_to_expiry, float(r["iv"]) / 100.0),
            axis=1,
        )
        d["err"] = (d["delta"].abs() - target).abs()
    else:
        # Call delta = put_delta + exp(-q*T). Zero-div NIFTY ⇒ ≈ put_delta + 1.
        d["delta"] = d.apply(
            lambda r: bsm.put_delta(spot, float(r["strike"]), years_to_expiry, float(r["iv"]) / 100.0) + 1.0,
            axis=1,
        )
        d["err"] = (d["delta"] - target).abs()
    best = d.sort_values("err").iloc[0]
    return float(best["strike"]), float(best["iv"])
