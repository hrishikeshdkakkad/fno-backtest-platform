"""Unit tests for engine.exits.decide_exit (master design §6).

Spec-driven behaviour tests for the exit-timing engine. Parity with legacy
`backtest._manage_exit` + expiry-settlement branch lives in
test_exits_parity.py.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from nfo.engine.exits import ExitDecision, decide_exit
from nfo.specs.strategy import ExitSpec


def _merged(rows: list[dict]) -> pd.DataFrame:
    """Build a merged-legs DataFrame with the columns decide_exit expects."""
    if not rows:
        return pd.DataFrame(columns=["date", "short_close", "long_close", "net_close", "dte"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def test_profit_take_fires_on_first_hit():
    """pt50, credit=10, threshold=5. Merged has net_close [6, 4, 3] — exit at row 1 (4)."""
    spec = ExitSpec(variant="pt50", profit_take_fraction=0.5, manage_at_dte=21)
    merged = _merged([
        {"date": "2025-03-24", "short_close": 8.0, "long_close": 2.0, "net_close": 6.0, "dte": 30},
        {"date": "2025-03-25", "short_close": 5.0, "long_close": 1.0, "net_close": 4.0, "dte": 29},
        {"date": "2025-03-26", "short_close": 4.0, "long_close": 1.0, "net_close": 3.0, "dte": 28},
    ])
    out = decide_exit(
        merged,
        exit_spec=spec,
        net_credit=10.0,
        short_strike=100.0,
        long_strike=90.0,
        spot_at_expiry=105.0,
        expiry_date=date(2025, 4, 24),
    )
    assert isinstance(out, ExitDecision)
    assert out.outcome == "profit_take"
    assert out.net_close_at_exit == pytest.approx(4.0)
    assert out.exit_date == date(2025, 3, 25)
    assert out.dte_exit == 29
    assert out.pnl_per_share == pytest.approx(6.0)
    assert out.closed_before_expiry is True
    assert out.short_exit_premium == pytest.approx(5.0)
    assert out.long_exit_premium == pytest.approx(1.0)


def test_profit_take_skipped_for_hte():
    """pt=1.0 (HTE) ⇒ branch 1 skipped; manage_at_dte=None ⇒ branch 2 skipped;
    settle at expiry (expired worthless here since spot > short_strike)."""
    spec = ExitSpec(variant="hte", profit_take_fraction=1.0, manage_at_dte=None)
    merged = _merged([
        {"date": "2025-03-24", "short_close": 8.0, "long_close": 2.0, "net_close": 6.0, "dte": 30},
        {"date": "2025-03-25", "short_close": 5.0, "long_close": 1.0, "net_close": 4.0, "dte": 29},
    ])
    out = decide_exit(
        merged,
        exit_spec=spec,
        net_credit=10.0,
        short_strike=100.0,
        long_strike=90.0,
        spot_at_expiry=105.0,
        expiry_date=date(2025, 4, 24),
    )
    assert out.outcome == "expired_worthless"
    assert out.closed_before_expiry is False
    assert out.exit_date == date(2025, 4, 24)
    assert out.dte_exit == 0
    assert out.pnl_per_share == pytest.approx(10.0)  # keep full credit
    assert out.net_close_at_exit == pytest.approx(0.0)  # credit - max_profit
    assert out.short_exit_premium == 0.0
    assert out.long_exit_premium == 0.0


def test_managed_at_dte_fires():
    """pt=1.0 (skip branch 1), manage_at_dte=21; merged has dte [30, 25, 20, 15] —
    exit at row 2 (dte=20)."""
    spec = ExitSpec(variant="dte2", profit_take_fraction=1.0, manage_at_dte=21)
    merged = _merged([
        {"date": "2025-03-20", "short_close": 8.0, "long_close": 2.0, "net_close": 6.0, "dte": 30},
        {"date": "2025-03-25", "short_close": 7.0, "long_close": 1.5, "net_close": 5.5, "dte": 25},
        {"date": "2025-03-30", "short_close": 6.0, "long_close": 1.0, "net_close": 5.0, "dte": 20},
        {"date": "2025-04-04", "short_close": 5.0, "long_close": 0.5, "net_close": 4.5, "dte": 15},
    ])
    out = decide_exit(
        merged,
        exit_spec=spec,
        net_credit=10.0,
        short_strike=100.0,
        long_strike=90.0,
        spot_at_expiry=105.0,
        expiry_date=date(2025, 4, 24),
    )
    assert out.outcome == "managed"
    assert out.exit_date == date(2025, 3, 30)
    assert out.dte_exit == 20
    assert out.net_close_at_exit == pytest.approx(5.0)
    assert out.pnl_per_share == pytest.approx(5.0)
    assert out.closed_before_expiry is True
    assert out.short_exit_premium == pytest.approx(6.0)
    assert out.long_exit_premium == pytest.approx(1.0)


def test_empty_merged_settles_at_expiry():
    """Empty merged frame ⇒ settle at expiry. Spot well below long strike ⇒ max_loss."""
    spec = ExitSpec(variant="pt50", profit_take_fraction=0.5, manage_at_dte=21)
    merged = _merged([])
    out = decide_exit(
        merged,
        exit_spec=spec,
        net_credit=10.0,
        short_strike=100.0,
        long_strike=90.0,
        spot_at_expiry=80.0,  # < long_strike ⇒ max_loss
        expiry_date=date(2025, 4, 24),
    )
    # width = 10, max_loss = net_credit - width = 10 - 10 = 0 per share
    # pnl_per_share = net_credit - width = 0
    assert out.outcome == "max_loss"
    assert out.closed_before_expiry is False
    assert out.exit_date == date(2025, 4, 24)
    assert out.dte_exit == 0
    assert out.pnl_per_share == pytest.approx(0.0)  # 10 - 10
    assert out.net_close_at_exit == pytest.approx(10.0)  # credit - pnl = 10 - 0
    assert out.short_exit_premium == 0.0
    assert out.long_exit_premium == 0.0


def test_settle_at_expiry_expired_worthless():
    """Merged frame exists but no rows meet pt/manage triggers; spot > short_strike →
    expired_worthless and net_close_at_exit = credit - max_profit = 0."""
    # pt=0.5 with threshold=5: no rows below 5 (all net_close ≥ 6)
    # manage_at_dte=None: branch 2 skipped
    spec = ExitSpec(variant="pt50", profit_take_fraction=0.5, manage_at_dte=None)
    merged = _merged([
        {"date": "2025-03-24", "short_close": 8.0, "long_close": 2.0, "net_close": 6.0, "dte": 30},
        {"date": "2025-04-01", "short_close": 8.5, "long_close": 2.5, "net_close": 6.0, "dte": 23},
    ])
    out = decide_exit(
        merged,
        exit_spec=spec,
        net_credit=10.0,
        short_strike=100.0,
        long_strike=90.0,
        spot_at_expiry=110.0,  # > short_strike ⇒ expired_worthless
        expiry_date=date(2025, 4, 24),
    )
    assert out.outcome == "expired_worthless"
    assert out.closed_before_expiry is False
    assert out.pnl_per_share == pytest.approx(10.0)
    assert out.net_close_at_exit == pytest.approx(0.0)
    assert out.exit_date == date(2025, 4, 24)
    assert out.dte_exit == 0
    assert out.short_exit_premium == 0.0
    assert out.long_exit_premium == 0.0


def test_profit_take_fraction_none_treated_as_hte_when_manage_none():
    """profit_take_fraction=None + manage_at_dte=None ⇒ HTE behaviour."""
    spec = ExitSpec(variant="hte", profit_take_fraction=None, manage_at_dte=None)
    merged = _merged([
        {"date": "2025-03-24", "short_close": 0.1, "long_close": 0.05, "net_close": 0.05, "dte": 30},
    ])
    out = decide_exit(
        merged,
        exit_spec=spec,
        net_credit=10.0,
        short_strike=100.0,
        long_strike=90.0,
        spot_at_expiry=105.0,
        expiry_date=date(2025, 4, 24),
    )
    # Branch 1 must be skipped (pt None treated as 1.0) even though net_close ≤ 0.05
    # would trip a threshold if we accidentally set pt to 0. Branch 2 skipped (manage=None).
    # Settles at expiry: spot=105 > short=100 ⇒ expired_worthless.
    assert out.outcome == "expired_worthless"
    assert out.closed_before_expiry is False


def test_profit_take_only_no_manage_settles_when_no_hit():
    """profit_take active but not hit, manage=None ⇒ settles at expiry partial_loss."""
    spec = ExitSpec(variant="pt25", profit_take_fraction=0.25, manage_at_dte=None)
    merged = _merged([
        {"date": "2025-03-24", "short_close": 8.0, "long_close": 2.0, "net_close": 6.0, "dte": 30},
    ])
    # threshold = (1 - 0.25) * 10 = 7.5  → row with net_close=6 would TRIGGER!
    # Need to ensure we test the settle path, so use a higher net_close.
    merged = _merged([
        {"date": "2025-03-24", "short_close": 10.0, "long_close": 0.5, "net_close": 9.5, "dte": 30},
    ])
    out = decide_exit(
        merged,
        exit_spec=spec,
        net_credit=10.0,
        short_strike=100.0,
        long_strike=90.0,
        spot_at_expiry=95.0,  # between strikes ⇒ partial_loss
        expiry_date=date(2025, 4, 24),
    )
    # intrinsic = 100 - 95 = 5; pnl = 10 - 5 = 5
    assert out.outcome == "partial_loss"
    assert out.closed_before_expiry is False
    assert out.pnl_per_share == pytest.approx(5.0)
    assert out.net_close_at_exit == pytest.approx(5.0)  # credit - pnl = 10 - 5
