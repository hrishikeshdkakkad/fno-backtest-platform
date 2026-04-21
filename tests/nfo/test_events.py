"""Event calendar — pure-lookup paths + one refresh path with a mocked Parallel client."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from nfo import events as ev


@pytest.fixture(autouse=True)
def _isolate_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the events parquet to a tmp path so tests don't collide."""
    target = tmp_path / "events.parquet"
    monkeypatch.setattr(ev, "EVENTS_PATH", target)
    return target


def _write_events(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df.to_parquet(path, index=False)


def test_upcoming_events_filters_by_window(_isolate_events: Path) -> None:
    today = date(2026, 4, 20)
    _write_events(_isolate_events, [
        {"date": today - timedelta(days=1), "name": "past", "kind": "OTHER",
         "severity": "low", "source_url": "", "notes": None},
        {"date": today + timedelta(days=5), "name": "RBI-MPC", "kind": "RBI",
         "severity": "high", "source_url": "rbi.org.in", "notes": None},
        {"date": today + timedelta(days=40), "name": "out-of-window", "kind": "FOMC",
         "severity": "high", "source_url": "", "notes": None},
    ])
    out = ev.upcoming_events(today, dte=30)
    assert len(out) == 1
    assert out[0].kind == "RBI"


def test_event_risk_flag_high_on_macro_event() -> None:
    flag = ev.event_risk_flag([
        ev.EventRecord(date=date(2026, 4, 25), name="FOMC", kind="FOMC", severity="high"),
    ])
    assert flag.severity == "high"


def test_event_risk_flag_medium_on_earnings_cluster() -> None:
    flag = ev.event_risk_flag([
        ev.EventRecord(date=date(2026, 4, 25), name="RELIANCE", kind="EARNINGS", severity="medium"),
    ])
    assert flag.severity == "medium"


def test_event_risk_flag_low_on_empty() -> None:
    flag = ev.event_risk_flag([])
    assert flag.severity == "low"
    assert flag.any() is False


def test_refresh_macro_events_calls_client_and_marks_high(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_bundle = ev.EventBundle(
        events=[
            ev.EventRecord(date=date(2026, 4, 30), name="RBI MPC Decision",
                           kind="RBI", severity="medium", source_url="rbi.org.in"),
        ],
        horizon_days=90,
    )
    mock_client = MagicMock()
    mock_client.task.return_value = fake_bundle

    got = ev.refresh_macro_events(horizon_days=90, client=mock_client, today=date(2026, 4, 20))
    assert len(got) == 1
    assert got[0].severity == "high"    # forced regardless of what the model returns
    assert got[0].kind == "RBI"
    mock_client.task.assert_called_once()


def test_refresh_all_writes_parquet(monkeypatch: pytest.MonkeyPatch,
                                    _isolate_events: Path) -> None:
    mock_client = MagicMock()
    mock_client.task.return_value = ev.EventBundle(
        events=[ev.EventRecord(date=date(2026, 4, 30), name="FOMC", kind="FOMC", severity="high")],
    )
    mock_client.findall.return_value = [
        {"fields": {"company_name": "RELIANCE", "announcement_date": "2026-04-28",
                    "source_url": "nseindia.com"}},
        {"fields": {"company_name": "TCS", "announcement_date": "not-a-date",
                    "source_url": "x"}},   # malformed — should be dropped
    ]

    df = ev.refresh_all(client=mock_client, today=date(2026, 4, 20))
    assert _isolate_events.exists()
    assert len(df) == 2         # FOMC + RELIANCE; TCS dropped for bad date
    kinds = set(df["kind"])
    assert kinds == {"FOMC", "EARNINGS"}
