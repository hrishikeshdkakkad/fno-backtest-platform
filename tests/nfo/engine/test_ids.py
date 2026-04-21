"""Tests for canonical id helpers (master design §5)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from nfo.engine.cycles import (
    build_run_id,
    cycle_id,
    feature_day_id,
    fire_id,
    selection_id,
    trade_id,
)


def test_feature_day_id_shape():
    assert feature_day_id("NIFTY", date(2025, 3, 24)) == "NIFTY:2025-03-24"


def test_cycle_id_shape():
    cid = cycle_id("NIFTY", date(2025, 4, 24), "3.0.0")
    assert cid == "NIFTY:2025-04-24:3.0.0"


def test_fire_id_shape():
    cid = cycle_id("NIFTY", date(2025, 4, 24), "3.0.0")
    fid = fire_id(cid, date(2025, 3, 24))
    assert fid == "NIFTY:2025-04-24:3.0.0:2025-03-24"


def test_selection_id_shape():
    cid = cycle_id("NIFTY", date(2025, 4, 24), "3.0.0")
    sid = selection_id(cid, "live_rule", "hte")
    assert sid == "NIFTY:2025-04-24:3.0.0:live_rule:hte"


def test_trade_id_is_hex_16_and_deterministic():
    kw = dict(
        underlying="NIFTY",
        expiry_date=date(2025, 4, 24),
        short_strike=22500,
        long_strike=22400,
        width=100.0,
        delta_target=0.30,
        exit_variant="hte",
        entry_date=date(2025, 3, 24),
    )
    a = trade_id(**kw)
    b = trade_id(**kw)
    assert a == b
    assert len(a) == 16
    assert all(c in "0123456789abcdef" for c in a)


def test_trade_id_differs_on_strike():
    kw = dict(
        underlying="NIFTY",
        expiry_date=date(2025, 4, 24),
        short_strike=22500,
        long_strike=22400,
        width=100.0,
        delta_target=0.30,
        exit_variant="hte",
        entry_date=date(2025, 3, 24),
    )
    a = trade_id(**kw)
    b = trade_id(**{**kw, "short_strike": 22400})
    assert a != b


def test_build_run_id_shape():
    ts = datetime(2026, 4, 21, 14, 30, 0, tzinfo=timezone.utc)
    rid = build_run_id(created_at=ts, study_id="capital_analysis", strategy_hash_short="7a3f9b")
    assert rid == "20260421T143000-capital_analysis-7a3f9b"
