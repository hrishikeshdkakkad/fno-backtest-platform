"""Tests for scripts/nfo/walkforward_v3.py — PR3 rolling walk-forward.

Two testable units:
  - ``generate_windows(data_start, data_end, train_months, test_months, step_months)``
    returns a list of (train_start, train_end, test_start, test_end) tuples.
  - ``apply_kill_rules(per_window)`` inspects the per-window summary and
    returns (killed: bool, reasons: list[str]) per the user's PR3 kill rules.

Trade simulation itself is delegated to nfo.engine.execution; covered there.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest


def _import_wf():
    import importlib
    return importlib.import_module("walkforward_v3")


class TestGenerateWindows:
    """Rolling 24m train / 12m test, advancing quarterly."""

    def test_first_window_starts_after_train_length(self) -> None:
        wf = _import_wf()
        windows = wf.generate_windows(
            data_start=date(2020, 8, 1),
            data_end=date(2026, 4, 10),
            train_months=24,
            test_months=12,
            step_months=3,
        )
        assert windows, "expected at least one window"
        first = windows[0]
        assert first.train_start == date(2020, 8, 1)
        assert first.test_start == date(2022, 8, 1)
        assert (first.test_end - first.test_start).days >= 360  # roughly 12 months

    def test_windows_advance_by_step_months(self) -> None:
        wf = _import_wf()
        windows = wf.generate_windows(
            data_start=date(2020, 8, 1),
            data_end=date(2026, 4, 10),
            train_months=24,
            test_months=12,
            step_months=3,
        )
        # Quarterly step: consecutive test_starts should differ by roughly 3 months.
        assert len(windows) >= 2
        for a, b in zip(windows, windows[1:]):
            delta = b.test_start - a.test_start
            assert 80 <= delta.days <= 100, f"step not ~3 months: {delta.days} days"

    def test_last_window_fits_within_data_end(self) -> None:
        wf = _import_wf()
        windows = wf.generate_windows(
            data_start=date(2020, 8, 1),
            data_end=date(2026, 4, 10),
            train_months=24,
            test_months=12,
            step_months=3,
        )
        for w in windows:
            assert w.test_end <= date(2026, 4, 10)

    def test_rejects_windows_beyond_data(self) -> None:
        wf = _import_wf()
        # Data ends 2022-08-01 — no 24m train + 12m test possible.
        windows = wf.generate_windows(
            data_start=date(2020, 8, 1),
            data_end=date(2022, 8, 1),
            train_months=24,
            test_months=12,
            step_months=3,
        )
        assert len(windows) == 0

    def test_window_fields(self) -> None:
        wf = _import_wf()
        windows = wf.generate_windows(
            data_start=date(2020, 1, 1),
            data_end=date(2024, 12, 31),
            train_months=24,
            test_months=12,
            step_months=3,
        )
        w = windows[0]
        assert hasattr(w, "train_start")
        assert hasattr(w, "train_end")
        assert hasattr(w, "test_start")
        assert hasattr(w, "test_end")
        assert w.train_end == w.test_start


class TestApplyKillRules:
    """User-specified kill rules for PR3."""

    def _row(self, **kwargs):
        base = {
            "test_start": date(2024, 1, 1),
            "test_end": date(2024, 12, 31),
            "fire_cycles": 3,
            "trades": 3,
            "wins": 2,
            "per_contract_pnl": 500.0,
            "lot_aware_pnl": 37500.0,
            "win_rate": 0.667,
            "max_drawdown": -100.0,
        }
        base.update(kwargs)
        return base

    def test_all_windows_healthy_does_not_kill(self) -> None:
        wf = _import_wf()
        rows = [self._row(), self._row(), self._row()]
        killed, reasons = wf.apply_kill_rules(rows)
        assert killed is False
        assert reasons == []

    def test_zero_fire_cycle_window_kills(self) -> None:
        wf = _import_wf()
        rows = [self._row(), self._row(fire_cycles=0, trades=0, wins=0, per_contract_pnl=0.0, lot_aware_pnl=0.0, win_rate=0.0), self._row()]
        killed, reasons = wf.apply_kill_rules(rows)
        assert killed is True
        assert any("0 fire-cycles" in r for r in reasons)

    def test_median_oos_pnl_nonpositive_kills(self) -> None:
        wf = _import_wf()
        rows = [
            self._row(per_contract_pnl=100.0, lot_aware_pnl=7500.0),
            self._row(per_contract_pnl=-400.0, lot_aware_pnl=-30000.0),
            self._row(per_contract_pnl=-200.0, lot_aware_pnl=-15000.0),
            self._row(per_contract_pnl=50.0, lot_aware_pnl=3750.0),
        ]
        killed, reasons = wf.apply_kill_rules(rows)
        assert killed is True
        assert any("median" in r.lower() for r in reasons)

    def test_repeated_thin_windows_kill(self) -> None:
        """If 2+ windows have <3 test trades, kill for production."""
        wf = _import_wf()
        rows = [
            self._row(trades=1),
            self._row(trades=2),
            self._row(trades=4),
            self._row(trades=1),
        ]
        killed, reasons = wf.apply_kill_rules(rows)
        assert killed is True
        assert any("thin" in r.lower() or "< 3" in r for r in reasons)

    def test_aggregate_dominated_by_burst_kills(self) -> None:
        """If >= 50% of total P&L comes from a single window, result is not robust."""
        wf = _import_wf()
        rows = [
            self._row(per_contract_pnl=100.0, lot_aware_pnl=7500.0),
            self._row(per_contract_pnl=100.0, lot_aware_pnl=7500.0),
            self._row(per_contract_pnl=100.0, lot_aware_pnl=7500.0),
            self._row(per_contract_pnl=2000.0, lot_aware_pnl=150000.0),  # burst
        ]
        killed, reasons = wf.apply_kill_rules(rows)
        assert killed is True
        assert any("dominated" in r.lower() or "burst" in r.lower() for r in reasons)

    def test_empty_rows_kills(self) -> None:
        """No windows at all = kill (can't evaluate anything)."""
        wf = _import_wf()
        killed, reasons = wf.apply_kill_rules([])
        assert killed is True

    def test_multiple_simultaneous_rules_all_listed(self) -> None:
        wf = _import_wf()
        rows = [
            self._row(fire_cycles=0, trades=0, wins=0, per_contract_pnl=0.0, lot_aware_pnl=0.0, win_rate=0.0),
            self._row(per_contract_pnl=-500.0, lot_aware_pnl=-37500.0),
            self._row(per_contract_pnl=-500.0, lot_aware_pnl=-37500.0),
        ]
        killed, reasons = wf.apply_kill_rules(rows)
        assert killed is True
        assert len(reasons) >= 2
