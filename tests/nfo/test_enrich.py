"""Macro-brief & flow enrichment — both paths mock the Parallel client."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nfo import enrich


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(enrich, "BRIEF_PATH", tmp_path / "brief.json")
    monkeypatch.setattr(enrich, "FII_DII_PATH", tmp_path / "fii_dii.parquet")


def test_macro_brief_returns_parsed_and_persists() -> None:
    mock_client = MagicMock()
    mock_client.task.return_value = enrich.MacroBrief(
        summary="RBI steady; FII selling moderated; earnings beats in banks.",
        rate_outlook="RBI on hold through Q2.",
        flow_regime="FIIs net sellers last 5 sessions.",
        earnings_tone="Mixed; financials beat, IT guide soft.",
        citations=["https://www.rbi.org.in/x"],
    )

    out = enrich.macro_brief(client=mock_client, snap={"grade": "A", "spot": 24500})
    assert "steady" in out.summary
    mock_client.task.assert_called_once()

    # Latest brief is now persisted — second access via latest_brief() should hit disk.
    loaded = enrich.latest_brief()
    assert loaded is not None
    assert loaded.summary == out.summary


def test_fii_dii_flow_writes_parquet_and_sorts() -> None:
    mock_client = MagicMock()
    mock_client.task.return_value = enrich.FlowBundle(
        rows=[
            enrich.FlowRow(date=date(2026, 4, 19), fii_cash=-1200.0, dii_cash=1500.0,
                           source_url="nseindia.com"),
            enrich.FlowRow(date=date(2026, 4, 18), fii_cash=-800.0, dii_cash=900.0,
                           source_url="nseindia.com"),
        ],
        lookback_days=30,
    )

    df = enrich.fii_dii_flow(client=mock_client, lookback_days=30)
    assert len(df) == 2
    assert enrich.FII_DII_PATH.exists()
    # Sorted ascending by date.
    assert df["date"].is_monotonic_increasing


def test_news_snapshot_passes_through() -> None:
    mock_client = MagicMock()
    mock_client.search.return_value = {"results": [{"url": "x", "snippet": "headline"}]}
    got = enrich.news_snapshot(client=mock_client)
    assert got["results"][0]["url"] == "x"
    mock_client.search.assert_called_once()
