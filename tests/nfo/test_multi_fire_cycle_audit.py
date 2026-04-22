"""Tests for scripts/nfo/multi_fire_cycle_audit.py.

Pure-function helpers are unit-tested here. The end-to-end simulation
(per-day engine replay) is verified by the acceptance criterion of
reproducing the known 2022-07-28 pattern: 7 fire-days, 6 tradable
profitable, 1 untradable, 0 losing.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest


def _import():
    import importlib
    return importlib.import_module("multi_fire_cycle_audit")


class TestGroupFireDaysByCycle:
    """Multi-fire cycles = expiries with >1 fire-day."""

    def test_no_fires_returns_empty(self) -> None:
        m = _import()
        df = pd.DataFrame({"date": pd.to_datetime(["2022-06-01"]),
                           "target_expiry": ["2022-07-28"]})
        mask = pd.Series([False])
        assert m.group_fire_days_by_cycle(df, mask) == {}

    def test_single_fire_per_expiry_excluded(self) -> None:
        """Cycles with only 1 fire-day are NOT multi-fire and are excluded."""
        m = _import()
        df = pd.DataFrame({
            "date": pd.to_datetime(["2022-06-01", "2022-07-01"]),
            "target_expiry": ["2022-07-28", "2022-08-25"],
        })
        mask = pd.Series([True, True])
        out = m.group_fire_days_by_cycle(df, mask)
        assert out == {}

    def test_multi_fire_cycle_grouped(self) -> None:
        m = _import()
        df = pd.DataFrame({
            "date": pd.to_datetime(["2022-06-24", "2022-06-27", "2022-06-28", "2022-08-10"]),
            "target_expiry": ["2022-07-28", "2022-07-28", "2022-07-28", "2022-09-29"],
        })
        mask = pd.Series([True, True, True, True])
        out = m.group_fire_days_by_cycle(df, mask)
        # 2022-07-28 has 3 fire-days (multi-fire); 2022-09-29 has only 1 (excluded).
        assert "2022-07-28" in out
        assert "2022-09-29" not in out
        assert out["2022-07-28"] == [date(2022, 6, 24), date(2022, 6, 27), date(2022, 6, 28)]

    def test_fire_days_returned_in_chronological_order(self) -> None:
        m = _import()
        df = pd.DataFrame({
            "date": pd.to_datetime(["2022-07-04", "2022-06-24", "2022-06-30"]),
            "target_expiry": ["2022-07-28"] * 3,
        })
        mask = pd.Series([True, True, True])
        out = m.group_fire_days_by_cycle(df, mask)
        # First element must be the earliest fire-day (= first_fire convention).
        assert out["2022-07-28"][0] == date(2022, 6, 24)
        assert out["2022-07-28"] == sorted(out["2022-07-28"])

    def test_non_fire_rows_ignored(self) -> None:
        m = _import()
        df = pd.DataFrame({
            "date": pd.to_datetime(["2022-06-24", "2022-06-25", "2022-06-27"]),
            "target_expiry": ["2022-07-28"] * 3,
        })
        mask = pd.Series([True, False, True])  # 06-25 is not a fire
        out = m.group_fire_days_by_cycle(df, mask)
        assert out["2022-07-28"] == [date(2022, 6, 24), date(2022, 6, 27)]


class TestRankFirstFire:
    """Rank first_fire among tradable entries by pnl_contract."""

    def test_single_tradable_entry_is_only(self) -> None:
        m = _import()
        trades = [{"entry_date": date(2022, 6, 24), "is_tradable": True,
                   "pnl_contract": 500.0, "is_first_fire": True}]
        assert m.rank_first_fire(trades) == "only"

    def test_first_fire_best(self) -> None:
        m = _import()
        trades = [
            {"entry_date": date(2022, 6, 24), "is_tradable": True, "pnl_contract": 900.0, "is_first_fire": True},
            {"entry_date": date(2022, 6, 27), "is_tradable": True, "pnl_contract": 500.0, "is_first_fire": False},
            {"entry_date": date(2022, 6, 28), "is_tradable": True, "pnl_contract": 300.0, "is_first_fire": False},
        ]
        assert m.rank_first_fire(trades) == "best"

    def test_first_fire_worst(self) -> None:
        m = _import()
        trades = [
            {"entry_date": date(2022, 6, 24), "is_tradable": True, "pnl_contract": 100.0, "is_first_fire": True},
            {"entry_date": date(2022, 6, 27), "is_tradable": True, "pnl_contract": 500.0, "is_first_fire": False},
            {"entry_date": date(2022, 6, 28), "is_tradable": True, "pnl_contract": 900.0, "is_first_fire": False},
        ]
        assert m.rank_first_fire(trades) == "worst"

    def test_first_fire_middle(self) -> None:
        m = _import()
        trades = [
            {"entry_date": date(2022, 6, 24), "is_tradable": True, "pnl_contract": 500.0, "is_first_fire": True},
            {"entry_date": date(2022, 6, 27), "is_tradable": True, "pnl_contract": 900.0, "is_first_fire": False},
            {"entry_date": date(2022, 6, 28), "is_tradable": True, "pnl_contract": 100.0, "is_first_fire": False},
        ]
        assert m.rank_first_fire(trades) == "middle"

    def test_untradable_first_fire_returns_untradable(self) -> None:
        """If first_fire itself was untradable, rank is 'first_fire_untradable'."""
        m = _import()
        trades = [
            {"entry_date": date(2022, 6, 24), "is_tradable": False, "pnl_contract": None, "is_first_fire": True},
            {"entry_date": date(2022, 6, 27), "is_tradable": True, "pnl_contract": 500.0, "is_first_fire": False},
        ]
        assert m.rank_first_fire(trades) == "first_fire_untradable"

    def test_no_tradable_entries_returns_none(self) -> None:
        m = _import()
        trades = [
            {"entry_date": date(2022, 6, 24), "is_tradable": False, "pnl_contract": None, "is_first_fire": True},
            {"entry_date": date(2022, 6, 27), "is_tradable": False, "pnl_contract": None, "is_first_fire": False},
        ]
        assert m.rank_first_fire(trades) is None


class TestUntradableHandling:
    """Untradable entries must be preserved as is_tradable=False, not silently
    dropped and not counted as losses."""

    def test_cycle_summary_preserves_untradable_count(self) -> None:
        m = _import()
        trades = [
            {"entry_date": date(2022, 6, 24), "is_tradable": True, "pnl_contract": 500.0, "is_first_fire": True, "lot_aware_pnl": 12500.0},
            {"entry_date": date(2022, 6, 27), "is_tradable": False, "pnl_contract": None, "is_first_fire": False, "lot_aware_pnl": None},
            {"entry_date": date(2022, 6, 28), "is_tradable": True, "pnl_contract": 300.0, "is_first_fire": False, "lot_aware_pnl": 7500.0},
        ]
        summary = m.cycle_summary(trades, expiry=date(2022, 7, 28))
        assert summary["fire_days"] == 3
        assert summary["tradable"] == 2
        assert summary["profitable"] == 2
        assert summary["losing"] == 0
        assert summary["untradable"] == 1

    def test_cycle_summary_does_not_count_untradable_as_losing(self) -> None:
        """A None pnl_contract must not flip into 'losing' via <0 coercion."""
        m = _import()
        trades = [
            {"entry_date": date(2022, 6, 24), "is_tradable": False, "pnl_contract": None, "is_first_fire": True, "lot_aware_pnl": None},
            {"entry_date": date(2022, 6, 27), "is_tradable": True, "pnl_contract": 500.0, "is_first_fire": False, "lot_aware_pnl": 12500.0},
        ]
        summary = m.cycle_summary(trades, expiry=date(2022, 7, 28))
        assert summary["losing"] == 0

    def test_cycle_summary_best_worst_only_from_tradable(self) -> None:
        m = _import()
        trades = [
            {"entry_date": date(2022, 6, 24), "is_tradable": True, "pnl_contract": 900.0, "is_first_fire": True, "lot_aware_pnl": 22500.0},
            {"entry_date": date(2022, 6, 27), "is_tradable": False, "pnl_contract": None, "is_first_fire": False, "lot_aware_pnl": None},
            {"entry_date": date(2022, 6, 28), "is_tradable": True, "pnl_contract": 100.0, "is_first_fire": False, "lot_aware_pnl": 2500.0},
        ]
        summary = m.cycle_summary(trades, expiry=date(2022, 7, 28))
        assert summary["best_pnl"] == 900.0
        assert summary["worst_pnl"] == 100.0


class TestLotSizeDating:
    """lot_aware_pnl must use the lot size effective on the entry date."""

    def test_pre_2024_reform_uses_lot_25(self) -> None:
        m = _import()
        out = m.lot_aware_pnl(pnl_per_share=10.0, entry_date=date(2022, 6, 24))
        # NIFTY lot was 25 before 2024-11-20.
        assert out == 10.0 * 25

    def test_post_2024_reform_pre_2025_uses_lot_75(self) -> None:
        m = _import()
        out = m.lot_aware_pnl(pnl_per_share=10.0, entry_date=date(2025, 6, 1))
        assert out == 10.0 * 75

    def test_post_2025_dec_uses_lot_65(self) -> None:
        m = _import()
        out = m.lot_aware_pnl(pnl_per_share=10.0, entry_date=date(2026, 1, 15))
        assert out == 10.0 * 65

    def test_none_pnl_passes_through_as_none(self) -> None:
        m = _import()
        assert m.lot_aware_pnl(pnl_per_share=None, entry_date=date(2022, 6, 24)) is None


class TestOosWindowTagging:
    """OOS tagging must match walkforward_v3.generate_windows semantics exactly."""

    def test_cycle_inside_a_test_window_is_oos(self) -> None:
        m = _import()
        import walkforward_v3 as wf
        windows = wf.generate_windows(
            data_start=date(2020, 8, 1),
            data_end=date(2026, 4, 10),
        )
        # 2024-02-22 is inside at least one walk-forward test window (2024-02 onwards).
        assert m.is_cycle_oos(date(2024, 2, 22), windows) is True

    def test_cycle_before_first_test_window_is_not_oos(self) -> None:
        m = _import()
        import walkforward_v3 as wf
        windows = wf.generate_windows(
            data_start=date(2020, 8, 1),
            data_end=date(2026, 4, 10),
        )
        # 2021-01-06 is in training/warmup only; first test window starts 2022-08-01.
        assert m.is_cycle_oos(date(2021, 1, 6), windows) is False

    def test_consistency_with_walkforward_test_window_bounds(self) -> None:
        """The test-window tagging must use the EXACT same convention as
        walkforward_v3: [test_start, test_end) half-open, same generator,
        same defaults (24m train, 12m test, 3m step)."""
        m = _import()
        import walkforward_v3 as wf
        windows = wf.generate_windows(
            data_start=date(2020, 8, 1),
            data_end=date(2026, 4, 10),
        )
        first_test_start = windows[0].test_start
        # One day before the first test window start cannot be OOS.
        from datetime import timedelta
        assert m.is_cycle_oos(first_test_start - timedelta(days=1), windows) is False
        # The first test window start itself IS OOS.
        assert m.is_cycle_oos(first_test_start, windows) is True


class TestRowLevelOosTagging:
    """Row-level parquet must carry the cycle-level OOS flag + window id so
    downstream slicing doesn't need to re-join against the summary."""

    def _fake_sim_factory(self):
        """Reusable stub that returns a simulated-trade-like object."""
        from types import SimpleNamespace

        class _FakeSim:
            def __init__(self, entry, expiry):
                self.spread_trade = SimpleNamespace(
                    entry_date=entry, expiry_date=expiry, exit_date=expiry,
                    outcome="expired_worthless",
                    short_strike=22000.0, long_strike=21900.0,
                    net_credit=10.0, pnl_per_share=5.0,
                    pnl_contract=125.0, gross_pnl_contract=125.0, txn_cost_contract=0.0,
                )
                self.cycle_id = "c"
                self.trade_id = "t"

        def fake_simulate(client, strategy_spec, under, entry_date, expiry_date, spot_daily):
            return _FakeSim(entry_date, expiry_date)
        return fake_simulate

    def test_rows_carry_cycle_in_oos_flag(self) -> None:
        m = _import()
        import walkforward_v3 as wf
        windows = wf.generate_windows(
            data_start=date(2020, 8, 1),
            data_end=date(2026, 4, 10),
        )
        # 2024-05-30 cycle (first_fire=2024-05-03): inside walk-forward OOS.
        trades = m.simulate_all_fire_days(
            fire_days=[date(2024, 5, 3), date(2024, 5, 6)],
            expiry=date(2024, 5, 30),
            simulate_fn=self._fake_sim_factory(),
            client=None, strategy_spec=None, under=None, spot_daily=None,
            windows=windows,
        )
        assert all(t["cycle_in_oos"] is True for t in trades)
        # earliest_cycle_test_window_start must be a valid ISO date string.
        assert all(t["earliest_cycle_test_window_start"] is not None for t in trades)

    def test_pre_oos_rows_carry_false_flag_and_none_window(self) -> None:
        m = _import()
        import walkforward_v3 as wf
        windows = wf.generate_windows(
            data_start=date(2020, 8, 1),
            data_end=date(2026, 4, 10),
        )
        # 2022-07-28 cycle (first_fire=2022-06-24): inside training zone,
        # NOT in any walk-forward test window.
        trades = m.simulate_all_fire_days(
            fire_days=[date(2022, 6, 24), date(2022, 6, 27)],
            expiry=date(2022, 7, 28),
            simulate_fn=self._fake_sim_factory(),
            client=None, strategy_spec=None, under=None, spot_daily=None,
            windows=windows,
        )
        assert all(t["cycle_in_oos"] is False for t in trades)
        assert all(t["earliest_cycle_test_window_start"] is None for t in trades)

    def test_all_rows_in_cycle_share_same_oos_flag(self) -> None:
        """Cycle-level OOS is anchored on first_fire — every row in the same
        cycle must report the same flag, even if a later fire-day would be
        classified differently on its own."""
        m = _import()
        import walkforward_v3 as wf
        windows = wf.generate_windows(
            data_start=date(2020, 8, 1),
            data_end=date(2026, 4, 10),
        )
        trades = m.simulate_all_fire_days(
            fire_days=[date(2024, 5, 3), date(2024, 5, 6), date(2024, 5, 8)],
            expiry=date(2024, 5, 30),
            simulate_fn=self._fake_sim_factory(),
            client=None, strategy_spec=None, under=None, spot_daily=None,
            windows=windows,
        )
        oos_flags = {t["cycle_in_oos"] for t in trades}
        window_ids = {t["earliest_cycle_test_window_start"] for t in trades}
        assert len(oos_flags) == 1, "all rows in a cycle must share cycle_in_oos"
        assert len(window_ids) == 1, "all rows in a cycle must share the window id"

    def test_untradable_rows_also_carry_oos_tags(self) -> None:
        """An untradable fire-day (simulate_fn returns None) must still get
        the cycle-level OOS tag, not just the tradable rows."""
        m = _import()
        import walkforward_v3 as wf
        windows = wf.generate_windows(
            data_start=date(2020, 8, 1),
            data_end=date(2026, 4, 10),
        )

        def none_sim(client, strategy_spec, under, entry_date, expiry_date, spot_daily):
            return None

        trades = m.simulate_all_fire_days(
            fire_days=[date(2024, 5, 3), date(2024, 5, 6)],
            expiry=date(2024, 5, 30),
            simulate_fn=none_sim,
            client=None, strategy_spec=None, under=None, spot_daily=None,
            windows=windows,
        )
        assert len(trades) == 2
        assert all(t["is_tradable"] is False for t in trades)
        assert all(t["cycle_in_oos"] is True for t in trades)

    def test_default_no_windows_yields_false_flag_and_none_window(self) -> None:
        """When called without a windows list, downstream slicing still gets
        well-defined defaults: cycle_in_oos=False, window_id=None."""
        m = _import()
        trades = m.simulate_all_fire_days(
            fire_days=[date(2024, 5, 3)],
            expiry=date(2024, 5, 30),
            simulate_fn=self._fake_sim_factory(),
            client=None, strategy_spec=None, under=None, spot_daily=None,
        )
        assert trades[0]["cycle_in_oos"] is False
        assert trades[0]["earliest_cycle_test_window_start"] is None


class TestEarliestContainingWindow:
    """Pure helper used by simulate_all_fire_days."""

    def test_returns_none_for_pre_oos_date(self) -> None:
        m = _import()
        import walkforward_v3 as wf
        windows = wf.generate_windows(
            data_start=date(2020, 8, 1),
            data_end=date(2026, 4, 10),
        )
        assert m.earliest_containing_window(date(2021, 1, 6), windows) is None

    def test_returns_earliest_when_multiple_windows_contain_date(self) -> None:
        """Quarterly sliding windows overlap — a date inside OOS lands in up
        to 4 windows. The helper must return the one with the smallest
        test_start."""
        m = _import()
        import walkforward_v3 as wf
        windows = wf.generate_windows(
            data_start=date(2020, 8, 1),
            data_end=date(2026, 4, 10),
        )
        d = date(2024, 8, 1)
        hits = [w for w in windows if w.test_start <= d < w.test_end]
        assert len(hits) > 1, "expected multiple overlapping windows for this date"
        result = m.earliest_containing_window(d, windows)
        assert result.test_start == min(h.test_start for h in hits)


class TestNoLookaheadLeakage:
    """Each entry's trade result must be computable without data after entry_date.

    We express this as a structural property: the audit's per-fire-day
    simulation must pass its own entry_date to the engine, and the engine
    must not receive fire-days that occur later than the candidate entry
    as part of the same cycle's 'context.'
    """

    def test_simulated_entries_are_independent_per_fire_day(self, monkeypatch) -> None:
        """Stub run_cycle_from_dhan and prove the audit calls it once per
        fire-day with the correct entry_date, and never reuses or peeks at
        a future fire-day for a prior-day simulation."""
        m = _import()
        captured_entries: list[date] = []

        class _FakeTrade:
            def __init__(self, entry, pnl):
                from types import SimpleNamespace
                self.spread_trade = SimpleNamespace(
                    entry_date=entry, expiry_date=date(2022, 7, 28),
                    exit_date=date(2022, 7, 28),
                    outcome="expired_worthless",
                    short_strike=15800.0, long_strike=15700.0,
                    net_credit=10.0, pnl_per_share=pnl, pnl_contract=pnl * 25.0,
                    gross_pnl_contract=pnl * 25.0, txn_cost_contract=0.0,
                )
                self.cycle_id = "c"
                self.trade_id = "t"

        def fake_simulate(client, strategy_spec, under, entry_date, expiry_date, spot_daily):
            captured_entries.append(entry_date)
            return _FakeTrade(entry_date, 10.0)

        trades = m.simulate_all_fire_days(
            fire_days=[date(2022, 6, 24), date(2022, 6, 27), date(2022, 6, 28)],
            expiry=date(2022, 7, 28),
            simulate_fn=fake_simulate,
            client=None, strategy_spec=None, under=None, spot_daily=None,
        )
        assert captured_entries == [date(2022, 6, 24), date(2022, 6, 27), date(2022, 6, 28)]
        # Each result is tagged with its own entry_date; no cross-contamination.
        assert [t["entry_date"] for t in trades] == captured_entries
