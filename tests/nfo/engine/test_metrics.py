"""Unit tests for nfo.engine.metrics.summary_stats.

Hand-computed synthetic DataFrames. No cached-file dependencies.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from nfo.engine.metrics import SummaryStats, summary_stats


def _frame(pnls: list[float], outcomes: list[str] | None = None) -> pd.DataFrame:
    d = {"pnl_contract": pnls}
    if outcomes is not None:
        d["outcome"] = outcomes
    return pd.DataFrame(d)


class TestEmptyTrades:
    def test_empty_dataframe_returns_all_zeros(self) -> None:
        s = summary_stats(_frame([]))
        assert isinstance(s, SummaryStats)
        assert s.n == 0
        assert s.win_rate == 0
        assert s.avg_pnl_contract == 0
        assert s.total_pnl_contract == 0
        assert s.worst_cycle_pnl == 0
        assert s.best_cycle_pnl == 0
        assert s.std_pnl_contract == 0
        assert s.sharpe == 0
        assert s.sortino == 0
        assert s.max_loss_rate == 0


class TestSingleTrade:
    def test_single_winning_trade(self) -> None:
        s = summary_stats(_frame([100.0]))
        assert s.n == 1
        assert s.win_rate == 1.0
        assert s.avg_pnl_contract == 100.0
        assert s.total_pnl_contract == 100.0
        assert s.worst_cycle_pnl == 100.0
        assert s.best_cycle_pnl == 100.0
        assert math.isnan(s.std_pnl_contract)
        # std NaN → sharpe defaults to 0.0 (see impl guard).
        assert s.sharpe == 0.0
        assert s.sortino == 0.0

    def test_single_losing_trade(self) -> None:
        s = summary_stats(_frame([-50.0]))
        assert s.n == 1
        assert s.win_rate == 0.0
        assert s.avg_pnl_contract == -50.0
        assert s.total_pnl_contract == -50.0
        assert s.worst_cycle_pnl == -50.0
        assert s.best_cycle_pnl == -50.0
        assert math.isnan(s.std_pnl_contract)
        assert s.sharpe == 0.0


class TestMixedWinLoss:
    def test_win_rate_and_moments(self) -> None:
        # 3 wins, 1 loss
        pnls = [100.0, 200.0, -50.0, 150.0]
        s = summary_stats(_frame(pnls))
        assert s.n == 4
        assert s.win_rate == pytest.approx(0.75)
        expected_mean = (100 + 200 - 50 + 150) / 4.0
        assert s.avg_pnl_contract == pytest.approx(expected_mean)
        assert s.total_pnl_contract == pytest.approx(400.0)
        assert s.worst_cycle_pnl == pytest.approx(-50.0)
        assert s.best_cycle_pnl == pytest.approx(200.0)
        # ddof=1 stdev matches pandas.
        expected_std = float(pd.Series(pnls).std(ddof=1))
        assert s.std_pnl_contract == pytest.approx(expected_std)
        # sharpe non-zero (mean > 0, std > 0).
        assert s.sharpe != 0.0
        assert s.sharpe == pytest.approx((expected_mean / expected_std) * math.sqrt(12.0))


class TestAllMaxLoss:
    def test_all_max_loss_negative_sharpe(self) -> None:
        pnls = [-100.0, -95.0, -105.0, -90.0]
        outcomes = ["max_loss"] * 4
        s = summary_stats(_frame(pnls, outcomes))
        assert s.max_loss_rate == pytest.approx(1.0)
        assert s.win_rate == 0.0
        # negative mean, positive std → negative sharpe.
        assert s.sharpe < 0.0
        # all-negative → downside std > 0 → sortino < 0.
        assert s.sortino < 0.0


class TestPeriodsPerYear:
    def test_default_is_twelve(self) -> None:
        pnls = [10.0, -5.0, 15.0, -3.0, 20.0]
        s_default = summary_stats(_frame(pnls))
        s_explicit = summary_stats(_frame(pnls), periods_per_year=12.0)
        assert s_default.sharpe == pytest.approx(s_explicit.sharpe)
        assert s_default.sortino == pytest.approx(s_explicit.sortino)

    def test_periods_per_year_scaling(self) -> None:
        pnls = [10.0, -5.0, 15.0, -3.0, 20.0]
        s_12 = summary_stats(_frame(pnls), periods_per_year=12.0)
        s_1 = summary_stats(_frame(pnls), periods_per_year=1.0)
        # sqrt(12) scaling between the two.
        assert s_12.sharpe == pytest.approx(s_1.sharpe * math.sqrt(12.0))
        if s_1.sortino != 0.0:
            assert s_12.sortino == pytest.approx(s_1.sortino * math.sqrt(12.0))


class TestReExportIdentity:
    def test_calibrate_reexports_same_object(self) -> None:
        from nfo.calibrate import SummaryStats as CalSS, summary_stats as cal_ss
        from nfo.engine.metrics import SummaryStats as EngSS, summary_stats as eng_ss

        assert CalSS is EngSS
        assert cal_ss is eng_ss
