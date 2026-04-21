"""Parity: regime_watch._compute_v3_gate routes through engine.triggers.

This test verifies post-P4 wiring: the migrated gate function calls
engine.triggers.TriggerEvaluator with the V3 spec, and the boolean result
matches a direct TriggerEvaluator call on equivalent inputs.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_regime_watch():
    path = REPO_ROOT / "scripts" / "nfo" / "regime_watch.py"
    spec = importlib.util.spec_from_file_location("_regime_watch_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_regime_watch_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _setup_tmp_root(tmp_path, monkeypatch):
    """Redirect ROOT + registry to tmp_path while copying the real v3 spec in."""
    (tmp_path / "configs" / "nfo" / "strategies").mkdir(parents=True)
    real_spec = (
        REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml"
    ).read_text()
    (tmp_path / "configs" / "nfo" / "strategies" / "v3_frozen.yaml").write_text(
        real_spec
    )
    (tmp_path / "configs" / "nfo" / ".registry.json").write_text(
        '{"strategies": {}}'
    )
    from nfo.specs import loader

    monkeypatch.setattr(
        loader,
        "_REGISTRY_PATH",
        tmp_path / "configs" / "nfo" / ".registry.json",
        raising=True,
    )
    monkeypatch.setattr("nfo.config.ROOT", tmp_path, raising=True)


def test_v3_gate_routes_to_engine_triggers(monkeypatch, tmp_path):
    """_compute_v3_gate must invoke TriggerEvaluator.evaluate_row."""
    _setup_tmp_root(tmp_path, monkeypatch)
    rw = _load_regime_watch()

    call_log = []
    from nfo.engine import triggers as trig_mod

    real_evaluate_row = trig_mod.TriggerEvaluator.evaluate_row

    def spy_evaluate_row(self, row, *, atr_value=float("nan")):
        call_log.append(
            {
                "vix": float(row.get("vix", 0)),
                "iv_rank": float(row.get("iv_rank_12mo", 0)),
            }
        )
        return real_evaluate_row(self, row, atr_value=atr_value)

    monkeypatch.setattr(
        trig_mod.TriggerEvaluator, "evaluate_row", spy_evaluate_row
    )

    passed, sev, reasoning = rw._compute_v3_gate(
        entry_date=date(2025, 3, 24),
        dte=35,
        atm_iv=15.0,
        rv_30=13.0,
        trend_score=3,
        vix=25.0,
        vix_pct_3mo=0.90,
        iv_rank_12mo=0.75,
        short_strike_iv=16.0,
    )
    assert len(call_log) >= 1, (
        "_compute_v3_gate should invoke TriggerEvaluator.evaluate_row"
    )
    assert isinstance(passed, bool)
    assert isinstance(sev, str)
    assert isinstance(reasoning, list) and len(reasoning) >= 4


def test_v3_gate_engine_matches_direct_call(monkeypatch, tmp_path):
    """regime_watch._compute_v3_gate's boolean matches a direct engine call."""
    _setup_tmp_root(tmp_path, monkeypatch)
    rw = _load_regime_watch()

    cases = [
        # All gates pass (short_strike_iv - rv_30 = 3.0 >= -2.0 spec threshold).
        dict(
            entry_date=date(2025, 3, 24),
            dte=35,
            atm_iv=15.0,
            rv_30=13.0,
            trend_score=3,
            vix=25.0,
            vix_pct_3mo=0.90,
            iv_rank_12mo=0.75,
            short_strike_iv=16.0,
        ),
        # Fail on IV-RV (iv - rv = -5.0 < -2.0).
        dict(
            entry_date=date(2025, 3, 24),
            dte=35,
            atm_iv=10.0,
            rv_30=15.0,
            trend_score=3,
            vix=25.0,
            vix_pct_3mo=0.90,
            iv_rank_12mo=0.75,
            short_strike_iv=10.0,
        ),
        # Fail: no vol signal triggers.
        dict(
            entry_date=date(2025, 3, 24),
            dte=35,
            atm_iv=15.0,
            rv_30=13.0,
            trend_score=3,
            vix=10.0,
            vix_pct_3mo=0.10,
            iv_rank_12mo=0.10,
            short_strike_iv=16.0,
        ),
    ]

    from nfo.engine.triggers import TriggerEvaluator
    from nfo.specs.loader import load_strategy

    spec, _ = load_strategy(
        tmp_path / "configs" / "nfo" / "strategies" / "v3_frozen.yaml"
    )
    ev = TriggerEvaluator(spec)

    for case in cases:
        passed, sev, _reason = rw._compute_v3_gate(**case)
        # Build the equivalent row the engine expects.
        iv_for = (
            case["short_strike_iv"]
            if case["short_strike_iv"] > 0
            else case["atm_iv"]
        )
        row = pd.Series(
            {
                "date": pd.Timestamp(case["entry_date"]),
                "vix": case["vix"],
                "vix_pct_3mo": case["vix_pct_3mo"],
                "iv_minus_rv": iv_for - case["rv_30"],
                "iv_rank_12mo": case["iv_rank_12mo"],
                "trend_score": case["trend_score"],
                "dte": case["dte"],
                "event_risk_v3": sev,  # use severity regime_watch computed
            }
        )
        direct = ev.evaluate_row(row, atr_value=float("nan"))
        assert passed == direct.fired, (
            f"mismatch on case {case}: regime_watch={passed} "
            f"engine={direct.fired}"
        )


def test_v3_gate_emits_monitor_snapshot(monkeypatch, tmp_path):
    """_compute_v3_gate must emit a MonitorSnapshot JSONL record per call."""
    _setup_tmp_root(tmp_path, monkeypatch)
    rw = _load_regime_watch()

    passed, sev, reasoning = rw._compute_v3_gate(
        entry_date=date(2025, 3, 24),
        dte=35,
        atm_iv=15.0,
        rv_30=13.0,
        trend_score=3,
        vix=25.0,
        vix_pct_3mo=0.90,
        iv_rank_12mo=0.75,
        short_strike_iv=16.0,
    )
    snap_root = tmp_path / "data" / "nfo" / "monitor_snapshots"
    assert snap_root.exists(), "monitor_snapshots directory must be created"
    files = list(snap_root.glob("*.jsonl"))
    assert files, "at least one JSONL file must be written"
    # Sanity: at least one line in one of those files.
    total_lines = 0
    for f in files:
        total_lines += len(
            [ln for ln in f.read_text().splitlines() if ln.strip()]
        )
    assert total_lines >= 1
