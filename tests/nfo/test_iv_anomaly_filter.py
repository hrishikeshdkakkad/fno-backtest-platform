"""Tests for nfo.data.drop_iv_anomalies.

Policy (per 2022 sentry finding — scripts/nfo/sentry_2022.py output for
2022-06-30, 2022-08-25, 2022-09-29, 2022-10-27, 2022-11-24, 2022-12-29):
Dhan's rolling_option payload occasionally returns implausible per-strike
IV values (303%, 354%, 0.00%, etc.) that corrupt any downstream study
reading atm_iv / short_strike_iv directly.

The filter DROPS anomalies rather than clamping them. Clamping invents
data; dropping keeps the defect visible and auditable. Returns both the
filtered frame and a counts dict so callers can log per-cycle drop rates.

IV unit convention: annualized percent (empirically verified —
2024-2026 NIFTY ATM IV median ≈ 13, 2022 median ≈ 19, crisis peaks ≈ 70).

Acceptance rules (conservative, based on 2020-2026 NIFTY observations):
  - IV must be strictly > 0 (zero IV is never physical for a live option).
  - IV must be ≤ 100 (annualized %). NIFTY historical ATM IV peaks
    ~70% during COVID; 100% is a wide tolerance guard.
  - NaN IV is not treated as an anomaly — it is a separate "missing"
    condition and passed through; downstream code must already handle NaN.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nfo.data import drop_iv_anomalies


def _df(iv_values: list[float], extra_cols: dict | None = None) -> pd.DataFrame:
    """Build a minimal rolling-option-shaped frame."""
    df = pd.DataFrame({
        "t": list(range(len(iv_values))),
        "close": [10.0] * len(iv_values),
        "iv": iv_values,
        "strike": [22000.0] * len(iv_values),
        "spot": [22000.0] * len(iv_values),
    })
    if extra_cols:
        for k, v in extra_cols.items():
            df[k] = v
    return df


class TestDropIvAnomalies:
    def test_preserves_plausible_values(self) -> None:
        df = _df([10.0, 25.0, 50.0, 85.0])
        out, counts = drop_iv_anomalies(df)
        assert len(out) == 4
        assert counts == {"dropped_zero_or_negative": 0, "dropped_above_ceiling": 0, "total_dropped": 0}

    def test_drops_zero_iv(self) -> None:
        df = _df([25.0, 0.0, 30.0])
        out, counts = drop_iv_anomalies(df)
        assert len(out) == 2
        assert counts["dropped_zero_or_negative"] == 1
        assert counts["total_dropped"] == 1

    def test_drops_negative_iv(self) -> None:
        df = _df([25.0, -5.0, 30.0])
        out, counts = drop_iv_anomalies(df)
        assert len(out) == 2
        assert counts["dropped_zero_or_negative"] == 1

    def test_drops_above_ceiling_matching_sentry_findings(self) -> None:
        """Values from 2022 sentry: 303, 354, 204 are the real-world anomalies."""
        df = _df([25.0, 303.22, 30.0, 204.03])
        out, counts = drop_iv_anomalies(df)
        assert len(out) == 2
        assert counts["dropped_above_ceiling"] == 2

    def test_preserves_high_but_plausible_iv(self) -> None:
        """~70% NIFTY ATM IV during COVID is plausible — must not be dropped."""
        df = _df([30.0, 60.0, 75.0, 95.0])
        out, counts = drop_iv_anomalies(df)
        assert len(out) == 4
        assert counts["total_dropped"] == 0

    def test_nan_iv_passes_through(self) -> None:
        """NaN IV is 'missing', not 'anomalous' — downstream code already
        treats NaN specifically; we should not count it or drop it here."""
        df = _df([25.0, float("nan"), 30.0])
        out, counts = drop_iv_anomalies(df)
        assert len(out) == 3
        assert counts["total_dropped"] == 0
        assert np.isnan(out["iv"].iloc[1])

    def test_counts_are_additive(self) -> None:
        df = _df([25.0, 0.0, 303.0, -10.0, 30.0, 150.0])
        out, counts = drop_iv_anomalies(df)
        assert len(out) == 2
        assert counts["dropped_zero_or_negative"] == 2
        assert counts["dropped_above_ceiling"] == 2
        assert counts["total_dropped"] == 4

    def test_empty_frame_returns_empty(self) -> None:
        df = _df([])
        out, counts = drop_iv_anomalies(df)
        assert len(out) == 0
        assert counts["total_dropped"] == 0

    def test_preserves_row_order_of_survivors(self) -> None:
        df = _df([25.0, 303.0, 30.0, 40.0])
        out, _ = drop_iv_anomalies(df)
        # After dropping the anomaly, the surviving rows should retain their original order.
        assert out["iv"].tolist() == [25.0, 30.0, 40.0]

    def test_reports_custom_ceiling(self) -> None:
        """A caller can tighten the ceiling (e.g. to 75%) for strict studies."""
        df = _df([25.0, 80.0, 50.0])
        out, counts = drop_iv_anomalies(df, ceiling=75.0)
        assert len(out) == 2
        assert counts["dropped_above_ceiling"] == 1

    def test_missing_iv_column_raises(self) -> None:
        df = pd.DataFrame({"t": [1, 2], "close": [10.0, 11.0]})
        with pytest.raises(KeyError):
            drop_iv_anomalies(df)
