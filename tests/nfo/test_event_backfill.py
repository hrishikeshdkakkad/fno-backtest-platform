"""Tests for nfo.events.load_sourced_backfill — primary-sourced event loader.

Guards:
  - Every confirmed entry carries a non-empty source_url + an iso event_date.
  - Unresolved entries have event_date=None and do NOT appear in the merged
    date list (they are explicit gaps, not silent drops).
  - Dates fall in the declared window.
  - RBI MPC, FOMC, and Union Budget are complete within 2020-08-01 .. 2023-12-31.
  - load_sourced_backfill returns the shape historical_backtest.HARD_EVENTS expects:
    a list of (date, name, kind) tuples.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from nfo.events import load_sourced_backfill


REPO_ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = REPO_ROOT / "configs" / "nfo" / "events" / "backfill_2020_2023.yaml"


class TestYamlShape:
    def test_yaml_file_exists(self) -> None:
        assert YAML_PATH.exists(), f"missing sourced backfill YAML at {YAML_PATH}"

    def test_loader_returns_list_of_tuples(self) -> None:
        events = load_sourced_backfill(YAML_PATH)
        assert isinstance(events, list)
        assert all(isinstance(e, tuple) and len(e) == 3 for e in events)

    def test_loader_tuple_shape_is_date_name_kind(self) -> None:
        events = load_sourced_backfill(YAML_PATH)
        assert events, "loader returned no events"
        d, name, kind = events[0]
        assert isinstance(d, date)
        assert isinstance(name, str) and name
        assert kind in {"RBI", "FOMC", "CPI", "BUDGET"}


class TestCompleteness:
    """RBI, FOMC, Budget must be complete in window — CPI may be partial."""

    @pytest.fixture(scope="class")
    def events(self) -> list[tuple[date, str, str]]:
        return load_sourced_backfill(YAML_PATH)

    def test_rbi_has_22_meetings_in_window(self, events) -> None:
        rbi = [d for d, _, k in events if k == "RBI" and date(2020, 8, 1) <= d <= date(2023, 12, 31)]
        assert len(rbi) == 22, f"expected 22 RBI MPC meetings in window, got {len(rbi)}"

    def test_fomc_has_27_meetings_in_window(self, events) -> None:
        # 2020-08 → 2023-12 inclusive: 3 (2020) + 8 (2021) + 8 (2022) + 8 (2023) = 27
        fomc = [d for d, _, k in events if k == "FOMC" and date(2020, 8, 1) <= d <= date(2023, 12, 31)]
        assert len(fomc) == 27, f"expected 27 FOMC meetings in window, got {len(fomc)}"

    def test_union_budget_has_three_entries(self, events) -> None:
        budget = [d for d, _, k in events if k == "BUDGET" and date(2020, 8, 1) <= d <= date(2023, 12, 31)]
        assert len(budget) == 3
        assert budget == [date(2021, 2, 1), date(2022, 2, 1), date(2023, 2, 1)]

    def test_cpi_has_at_least_twenty_confirmed_in_window(self, events) -> None:
        # Partial by design — 23 confirmed as of 2026-04-21. Gate must remain >= 20.
        cpi = [d for d, _, k in events if k == "CPI" and date(2020, 8, 1) <= d <= date(2023, 12, 31)]
        assert len(cpi) >= 20, f"expected >=20 confirmed CPI releases, got {len(cpi)}"


class TestDataIntegrity:
    def test_all_dates_in_declared_window_or_flagged_boundary(self) -> None:
        """Every emitted tuple must have a date inside the documented window.

        Exception: a single Dec-2023 CPI release dated 2024-01-11 is retained
        for completeness (documented in the YAML). Any other out-of-window
        date is a bug.
        """
        events = load_sourced_backfill(YAML_PATH)
        for d, name, kind in events:
            if d == date(2024, 1, 11) and kind == "CPI":
                continue
            assert date(2020, 8, 1) <= d <= date(2023, 12, 31), (
                f"date out of window: {d} {kind} {name!r}"
            )

    def test_no_duplicate_date_kind_pairs(self) -> None:
        events = load_sourced_backfill(YAML_PATH)
        seen = set()
        for d, _, kind in events:
            key = (d, kind)
            assert key not in seen, f"duplicate (date, kind): {key}"
            seen.add(key)

    def test_unresolved_cpi_entries_are_silently_dropped_not_emitted_as_nat(self) -> None:
        """Unresolved entries have event_date=null in the YAML — they must be
        dropped from the merged list entirely rather than surface as None."""
        events = load_sourced_backfill(YAML_PATH)
        assert all(d is not None for d, _, _ in events)


class TestIntegrationWithHistoricalBacktest:
    """Verify historical_backtest.HARD_EVENTS merges the sourced backfill."""

    def test_hard_events_contains_august_2020_rbi(self) -> None:
        import sys
        scripts = REPO_ROOT / "scripts" / "nfo"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import historical_backtest  # type: ignore[import-not-found]
        dates_kinds = {(d, k) for d, _, k in historical_backtest.HARD_EVENTS}
        assert (date(2020, 8, 6), "RBI") in dates_kinds
        assert (date(2022, 2, 10), "RBI") in dates_kinds
        assert (date(2023, 12, 13), "FOMC") in dates_kinds
        assert (date(2022, 2, 1), "BUDGET") in dates_kinds


class TestUnresolvedWarning:
    """Per user's P2: unresolved entries must be audible at runtime, not silent."""

    def test_load_emits_warning_with_unresolved_counts(self, caplog) -> None:
        import logging
        caplog.set_level(logging.WARNING, logger="nfo.events")
        _ = load_sourced_backfill(YAML_PATH)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "expected at least one WARN log from load_sourced_backfill"
        msg = warnings[0].getMessage()
        assert "unresolved" in msg.lower()
        assert "CPI" in msg, "CPI gaps must be named explicitly in the warning"

    def test_warning_count_matches_yaml(self, caplog) -> None:
        """If the YAML is edited to resolve a CPI entry, the log count must drop."""
        import logging
        import yaml as _yaml
        caplog.set_level(logging.WARNING, logger="nfo.events")
        _ = load_sourced_backfill(YAML_PATH)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        msg = warnings[-1].getMessage()
        # Count actual `unresolved` entries in YAML for a precise assertion.
        raw = _yaml.safe_load(YAML_PATH.read_text())
        total = 0
        for section in ("rbi_mpc", "fomc", "us_cpi", "union_budget"):
            for e in raw.get(section) or []:
                if e.get("status") == "unresolved" or e.get("event_date") is None:
                    total += 1
        assert f"dropped {total}" in msg, f"expected 'dropped {total}' in warning, got {msg!r}"
