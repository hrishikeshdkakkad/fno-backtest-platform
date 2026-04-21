"""Parity: engine.entry.resolve_entry_date matches reference snap-forward semantics.

The legacy `scripts/nfo/v3_live_rule_backtest._first_session_on_or_after`
helper was removed in P5-A1 (script now delegates to `nfo.studies.live_replay`).
To preserve the parity intent, this test reproduces the legacy semantics
inline and asserts engine output still matches.

Legacy behaviour: given `target` date and a `spot_daily` DataFrame with a
`date` column, return the first date in `spot_daily` >= target, or None.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from nfo.engine.entry import resolve_entry_date
from nfo.specs.loader import load_strategy, reset_registry_for_tests


REPO_ROOT = Path(__file__).resolve().parents[3]
SIGNALS = REPO_ROOT / "results" / "nfo" / "historical_signals.parquet"


def _legacy_first_session_on_or_after(target: date, spot_daily: pd.DataFrame) -> date | None:
    """Reproduction of the removed legacy helper from v3_live_rule_backtest.py.

    Kept inline here so the engine parity test no longer depends on a script
    helper that P5-A1 deleted.
    """
    later = spot_daily.loc[spot_daily["date"] >= pd.Timestamp(target), "date"]
    return None if later.empty else later.iloc[0].date()


@pytest.fixture
def _iso_registry(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")


@pytest.mark.skipif(not SIGNALS.exists(), reason="requires cached historical_signals.parquet")
def test_live_rule_entry_matches_legacy(_iso_registry):
    strat_path = REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_live_rule.yaml"
    spec, _ = load_strategy(strat_path)

    df = pd.read_parquet(SIGNALS)
    df["date"] = pd.to_datetime(df["date"])
    sessions = [d.date() for d in pd.to_datetime(df["date"]).sort_values()]

    # Sample targets across the historical window
    targets = [
        date(2024, 3, 1),    # session
        date(2024, 3, 2),    # Saturday → next Mon
        date(2024, 3, 3),    # Sunday   → next Mon
        date(2024, 12, 25),  # holiday  → next session
        date(2025, 1, 1),    # holiday  → next session
        date(2025, 4, 10),   # session
        date(2026, 5, 1),    # far future (may be past cache)
    ]

    # Build a spot_daily frame the reference helper expects: columns ["date", ...]
    spot_daily = df[["date"]].copy()

    for tgt in targets:
        engine_out = resolve_entry_date(
            spec=spec, first_fire_date=tgt, sessions=sessions,
        )
        legacy_out = _legacy_first_session_on_or_after(tgt, spot_daily)
        assert engine_out == legacy_out, (
            f"target={tgt}: engine={engine_out}, legacy={legacy_out}"
        )
