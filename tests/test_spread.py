"""Unit tests for credit spread module (no API calls)."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from csp.spread import (
    SpreadCandidate,
    SpreadConfig,
    pick_put_spread_for_cycle,
    spread_payoff_per_share,
)


class TestPayoffMath:
    def test_expired_worthless_above_short(self):
        pnl, outcome = spread_payoff_per_share(250, 240, 2.5, 260)
        assert outcome == "expired_worthless"
        assert pnl == pytest.approx(2.5)

    def test_expired_worthless_at_short_strike(self):
        # S == short_strike → short just OTM → keep full credit
        pnl, outcome = spread_payoff_per_share(250, 240, 2.5, 250)
        assert outcome == "expired_worthless"
        assert pnl == pytest.approx(2.5)

    def test_partial_loss_between_strikes(self):
        # S=245, short=250, long=240: short intrinsic=5, long=0, credit=2.5 → pnl = 2.5 - 5 = -2.5
        pnl, outcome = spread_payoff_per_share(250, 240, 2.5, 245)
        assert outcome == "partial_loss"
        assert pnl == pytest.approx(-2.5)

    def test_partial_loss_at_long_strike(self):
        # S==long_strike → still partial_loss (short intrinsic = width, long=0)
        pnl, outcome = spread_payoff_per_share(250, 240, 2.5, 240)
        assert outcome == "partial_loss"
        assert pnl == pytest.approx(-7.5)

    def test_max_loss_below_long(self):
        # S=235: both ITM, intrinsic=width=10, credit=2.5 → pnl=-7.5
        pnl, outcome = spread_payoff_per_share(250, 240, 2.5, 235)
        assert outcome == "max_loss"
        assert pnl == pytest.approx(-7.5)

    def test_zero_credit_is_still_defined(self):
        # pathological but should not raise
        pnl, outcome = spread_payoff_per_share(250, 240, 0.0, 260)
        assert outcome == "expired_worthless"
        assert pnl == pytest.approx(0.0)


class TestSpreadConfig:
    def test_defaults(self):
        cfg = SpreadConfig(underlying="IWM")
        assert cfg.target_delta == 0.20
        assert cfg.target_dte == 35
        assert cfg.spread_width == 10.0
        assert cfg.strike_increment == 1.0

    def test_inheritance_preserves_strategy_fields(self):
        cfg = SpreadConfig(underlying="QQQ", target_delta=0.3, spread_width=5.0, profit_take=0.5)
        assert cfg.target_delta == 0.3
        assert cfg.spread_width == 5.0
        assert cfg.profit_take == 0.5
        # unchanged inherited defaults
        assert cfg.risk_free_rate == 0.045


class TestSpreadCandidate:
    def test_asdict_roundtrip(self):
        cfg = SpreadConfig(underlying="SPY", target_delta=0.25, spread_width=10.0)
        c = SpreadCandidate(
            cfg=cfg,
            entry_date=pd.Timestamp("2024-05-17"),
            expiry_date=date(2024, 6, 21),
            spot_at_entry=500.0,
            short_strike=480.0,
            short_ticker="O:SPY240621P00480000",
            short_premium=3.0,
            short_iv=0.15,
            short_delta=-0.25,
            long_strike=470.0,
            long_ticker="O:SPY240621P00470000",
            long_premium=1.5,
            net_credit=1.5,
            max_loss=8.5,
            buying_power=850.0,
        )
        d = asdict(c)
        assert d["short_strike"] == 480.0
        assert d["long_strike"] == 470.0
        assert d["net_credit"] == 1.5
        assert d["buying_power"] == 850.0


class TestPickerShortCircuit:
    def test_returns_none_when_short_leg_missing(self):
        cfg = SpreadConfig(underlying="IWM", target_delta=0.25, spread_width=10.0)
        stock = pd.DataFrame()
        with patch("csp.spread.pick_put_for_cycle", return_value=None) as mock:
            result = pick_put_spread_for_cycle(
                MagicMock(), cfg, stock, pd.Timestamp("2024-05-17"), date(2024, 6, 21)
            )
        assert result is None
        mock.assert_called_once()

    def test_returns_none_when_long_leg_missing(self):
        from csp.strategy import TradeCandidate
        cfg = SpreadConfig(underlying="IWM", target_delta=0.25, spread_width=10.0)
        stock = pd.DataFrame()
        fake_short = TradeCandidate(
            cfg=cfg,
            entry_date=pd.Timestamp("2024-05-17"),
            expiry_date=date(2024, 6, 21),
            spot_at_entry=200.0,
            strike=195.0,
            option_ticker="O:IWM240621P00195000",
            entry_premium=2.0,
            entry_iv=0.18,
            entry_delta=-0.25,
            estimated_strike=195.0,
        )
        with patch("csp.spread.pick_put_for_cycle", return_value=fake_short), \
             patch("csp.spread.load_option_bars", return_value=pd.DataFrame()):
            result = pick_put_spread_for_cycle(
                MagicMock(), cfg, stock, pd.Timestamp("2024-05-17"), date(2024, 6, 21)
            )
        assert result is None

    def test_returns_none_when_net_credit_non_positive(self):
        from csp.strategy import TradeCandidate
        cfg = SpreadConfig(underlying="IWM", target_delta=0.25, spread_width=10.0)
        stock = pd.DataFrame()
        fake_short = TradeCandidate(
            cfg=cfg,
            entry_date=pd.Timestamp("2024-05-17"),
            expiry_date=date(2024, 6, 21),
            spot_at_entry=200.0,
            strike=195.0,
            option_ticker="O:IWM240621P00195000",
            entry_premium=1.0,   # short premium lower than long — inverted
            entry_iv=0.18,
            entry_delta=-0.25,
            estimated_strike=195.0,
        )
        # long leg has higher close than short → negative credit
        long_df = pd.DataFrame({
            "date": [pd.Timestamp("2024-05-17")],
            "c": [1.5],
            "t": [1715904000000],
            "o": [1.5], "h": [1.5], "l": [1.5], "v": [1], "n": [1], "vw": [1.5],
        })
        with patch("csp.spread.pick_put_for_cycle", return_value=fake_short), \
             patch("csp.spread.load_option_bars", return_value=long_df):
            result = pick_put_spread_for_cycle(
                MagicMock(), cfg, stock, pd.Timestamp("2024-05-17"), date(2024, 6, 21)
            )
        assert result is None

    def test_returns_candidate_on_happy_path(self):
        from csp.strategy import TradeCandidate
        cfg = SpreadConfig(underlying="IWM", target_delta=0.25, spread_width=10.0)
        stock = pd.DataFrame()
        fake_short = TradeCandidate(
            cfg=cfg,
            entry_date=pd.Timestamp("2024-05-17"),
            expiry_date=date(2024, 6, 21),
            spot_at_entry=200.0,
            strike=195.0,
            option_ticker="O:IWM240621P00195000",
            entry_premium=2.5,
            entry_iv=0.18,
            entry_delta=-0.25,
            estimated_strike=195.0,
        )
        long_df = pd.DataFrame({
            "date": [pd.Timestamp("2024-05-17")],
            "c": [0.75],
            "t": [1715904000000],
            "o": [0.75], "h": [0.75], "l": [0.75], "v": [1], "n": [1], "vw": [0.75],
        })
        with patch("csp.spread.pick_put_for_cycle", return_value=fake_short), \
             patch("csp.spread.load_option_bars", return_value=long_df):
            result = pick_put_spread_for_cycle(
                MagicMock(), cfg, stock, pd.Timestamp("2024-05-17"), date(2024, 6, 21)
            )
        assert result is not None
        assert result.short_strike == 195.0
        assert result.long_strike == 185.0
        assert result.net_credit == pytest.approx(1.75)
        assert result.max_loss == pytest.approx(8.25)
        assert result.buying_power == pytest.approx(825.0)
