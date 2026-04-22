"""Tests for scripts/nfo/sentry_2022.py — the 2022 NIFTY V3 sentry.

The sentry script is a thin wrapper around historical_backtest.run_backtest
for a 2022-only window, plus a V3-gate fire-counting helper. The scaffolding
(fetching, writing) is integration-only; the one unit-testable piece is
`v3_fire_mask(frame, events=...)`, which applies the canonical V3 trigger
definition (score>=4 AND s3_iv_rv AND s6_trend AND V3_event_ok AND any-vol)
over a signals DataFrame.

CLAUDE.md convention: scripts are importable via sys.path extension in
tests/nfo/conftest.py, so we can `import sentry_2022 as s`.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest


# Defer import so the test module collects even if the script is missing
# at first run — the RED-before-GREEN TDD cycle requires test collection
# to succeed so we can see the missing-attribute failure.
def _import_sentry():
    import importlib
    return importlib.import_module("sentry_2022")


class TestV3FireMask:
    """Exercises v3_fire_mask over hand-built signals frames."""

    def _row(self, **overrides) -> dict:
        """Sensible defaults for a non-firing day. Flip fields to fire.

        The date is 2022-06-15 — chosen so the default (no events passed to
        v3_fire_mask) yields no {RBI,FOMC,BUDGET} within 10 days, so the
        V3 event gate passes by default.
        """
        base = {
            "date": pd.Timestamp("2022-06-15"),
            "s1_vix_abs": False,
            "s2_vix_pct": False,
            "s3_iv_rv": False,
            "s4_pullback": False,
            "s5_iv_rank": False,
            "s6_trend": False,
            "s7_skew": None,  # V3 doesn't require s7
            "iv_minus_rv": 0.0,
        }
        base.update(overrides)
        return base

    def test_all_signals_off_is_no_fire(self) -> None:
        s = _import_sentry()
        df = pd.DataFrame([self._row()])
        assert not s.v3_fire_mask(df, events=[]).iloc[0]

    def test_v3_canonical_fire(self) -> None:
        """Frozen V3 spec: score>=4 AND s3 AND s6 AND event_ok AND any-vol AND iv-rv OK."""
        s = _import_sentry()
        row = self._row(
            s1_vix_abs=True,   # vol signal #1
            s3_iv_rv=True,
            s5_iv_rank=True,   # vol signal #2 (bumps score to 4)
            s6_trend=True,
            iv_minus_rv=1.0,
        )
        df = pd.DataFrame([row])
        assert s.v3_fire_mask(df, events=[]).iloc[0]

    def test_missing_vol_signal_blocks_fire(self) -> None:
        s = _import_sentry()
        row = self._row(
            s3_iv_rv=True,
            s6_trend=True,
            iv_minus_rv=1.0,
            # no s1/s2/s5 → no vol signal
        )
        df = pd.DataFrame([row])
        assert not s.v3_fire_mask(df, events=[]).iloc[0]

    def test_high_event_within_10_days_blocks_fire(self) -> None:
        """An RBI MPC 7 days after entry blocks V3 (10-day window)."""
        s = _import_sentry()
        row = self._row(
            s1_vix_abs=True,
            s3_iv_rv=True,
            s5_iv_rank=True,
            s6_trend=True,
            iv_minus_rv=1.0,
        )
        df = pd.DataFrame([row])
        events = [(date(2022, 6, 22), "RBI MPC", "RBI")]  # 7 days after 2022-06-15
        assert not s.v3_fire_mask(df, events=events).iloc[0]

    def test_high_event_outside_10_days_does_not_block(self) -> None:
        """An RBI MPC 15 days after entry is outside V3's 10-day window → OK."""
        s = _import_sentry()
        row = self._row(
            s1_vix_abs=True,
            s3_iv_rv=True,
            s5_iv_rank=True,
            s6_trend=True,
            iv_minus_rv=1.0,
        )
        df = pd.DataFrame([row])
        events = [(date(2022, 6, 30), "RBI MPC", "RBI")]  # 15 days after 2022-06-15
        assert s.v3_fire_mask(df, events=events).iloc[0]

    def test_cpi_within_10_days_does_not_block(self) -> None:
        """V3 demotes CPI to medium — it does NOT block firing even if in window."""
        s = _import_sentry()
        row = self._row(
            s1_vix_abs=True,
            s3_iv_rv=True,
            s5_iv_rank=True,
            s6_trend=True,
            iv_minus_rv=1.0,
        )
        df = pd.DataFrame([row])
        events = [(date(2022, 6, 20), "US CPI", "CPI")]  # 5 days after entry, but CPI
        assert s.v3_fire_mask(df, events=events).iloc[0]

    def test_iv_rv_too_low_blocks_fire(self) -> None:
        s = _import_sentry()
        row = self._row(
            s1_vix_abs=True,
            s3_iv_rv=True,
            s6_trend=True,
            iv_minus_rv=-5.0,   # below the -2.0 threshold
        )
        df = pd.DataFrame([row])
        assert not s.v3_fire_mask(df, events=[]).iloc[0]

    def test_nan_iv_minus_rv_with_none_s3_blocks(self) -> None:
        """Per historical_backtest.py, s3_iv_rv is None when iv_minus_rv is
        NaN (insufficient RV history). v3_fire_mask must treat None s3 as
        non-firing — early-window days with missing RV cannot fire V3."""
        s = _import_sentry()
        row = self._row(
            s1_vix_abs=True,
            s3_iv_rv=None,     # set to None because RV30 not yet computable
            s5_iv_rank=True,
            s6_trend=True,
            iv_minus_rv=float("nan"),
        )
        df = pd.DataFrame([row])
        assert not s.v3_fire_mask(df, events=[]).iloc[0]

    def test_trend_off_blocks_even_with_all_other_gates(self) -> None:
        s = _import_sentry()
        row = self._row(
            s1_vix_abs=True,
            s2_vix_pct=True,
            s3_iv_rv=True,
            s5_iv_rank=True,
            s6_trend=False,    # trend off
            iv_minus_rv=1.0,
        )
        df = pd.DataFrame([row])
        assert not s.v3_fire_mask(df, events=[]).iloc[0]

    def test_mask_returns_bool_series_same_length(self) -> None:
        s = _import_sentry()
        df = pd.DataFrame([
            self._row(),
            self._row(s1_vix_abs=True, s3_iv_rv=True, s5_iv_rank=True,
                      s6_trend=True, iv_minus_rv=1.0),
        ])
        mask = s.v3_fire_mask(df, events=[])
        assert len(mask) == 2
        assert mask.dtype == bool
        assert mask.tolist() == [False, True]

    def test_empty_frame_returns_empty_mask(self) -> None:
        s = _import_sentry()
        df = pd.DataFrame(columns=["s1_vix_abs", "s3_iv_rv", "s6_trend", "iv_minus_rv"])
        mask = s.v3_fire_mask(df, events=[])
        assert len(mask) == 0


class TestCountFireCycles:
    """The decision unit is fire-CYCLES (distinct target_expiry), not fire-days.

    Multiple fire-days within the same monthly expiry collapse to one canonical
    trade under V3's cycle_matched / live_rule selection modes, so this is the
    right number to compare against the calibration's filtered_trades.
    """

    def test_empty_frame_returns_zero(self) -> None:
        s = _import_sentry()
        df = pd.DataFrame(columns=["date", "target_expiry"])
        mask = pd.Series([], dtype=bool)
        assert s.count_fire_cycles(df, mask) == 0

    def test_no_fires_returns_zero(self) -> None:
        s = _import_sentry()
        df = pd.DataFrame({"date": ["2022-06-01"], "target_expiry": ["2022-07-28"]})
        assert s.count_fire_cycles(df, pd.Series([False])) == 0

    def test_single_fire_counts_as_one(self) -> None:
        s = _import_sentry()
        df = pd.DataFrame({"date": ["2022-06-01"], "target_expiry": ["2022-07-28"]})
        assert s.count_fire_cycles(df, pd.Series([True])) == 1

    def test_multiple_fires_same_expiry_collapse_to_one_cycle(self) -> None:
        """The user's P1 finding: 7 fire-days on 2022-07-28 expiry = 1 cycle."""
        s = _import_sentry()
        df = pd.DataFrame({
            "date": ["2022-06-27", "2022-06-28", "2022-06-29", "2022-06-30",
                     "2022-07-01", "2022-07-04"],
            "target_expiry": ["2022-07-28"] * 6,
        })
        mask = pd.Series([True] * 6)
        assert s.count_fire_cycles(df, mask) == 1

    def test_fires_across_distinct_expiries_count_separately(self) -> None:
        s = _import_sentry()
        df = pd.DataFrame({
            "date": ["2022-01-05", "2022-02-28", "2022-05-05", "2022-10-03"],
            "target_expiry": ["2022-02-24", "2022-03-31", "2022-06-30", "2022-11-24"],
        })
        mask = pd.Series([True] * 4)
        assert s.count_fire_cycles(df, mask) == 4

    def test_mixed_fire_and_non_fire_counts_only_fires(self) -> None:
        s = _import_sentry()
        df = pd.DataFrame({
            "date": ["2022-01-05", "2022-02-01", "2022-02-28", "2022-03-01"],
            "target_expiry": ["2022-02-24", "2022-02-24", "2022-03-31", "2022-03-31"],
        })
        mask = pd.Series([True, False, True, False])
        # Two fire-days, each on a different expiry → 2 cycles.
        assert s.count_fire_cycles(df, mask) == 2

    def test_missing_target_expiry_column_returns_zero(self) -> None:
        s = _import_sentry()
        df = pd.DataFrame({"date": ["2022-06-01"]})
        assert s.count_fire_cycles(df, pd.Series([True])) == 0
