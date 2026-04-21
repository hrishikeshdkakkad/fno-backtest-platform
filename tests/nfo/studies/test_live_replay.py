"""Parity test: engine run_live_replay matches legacy v3_live_rule_backtest output.

Loads cached signals + spot, runs `studies.live_replay.run_live_replay` for
the v3_live_rule spec (hte variant), and compares against the committed
`results/nfo/v3_live_trades_hte.csv`.

The test is skipped (not errored) if:
  - the cached signals parquet is missing
  - the cached NIFTY index parquet spanning 2023-12-15 → 2026-04-18 is missing
  - DHAN creds are absent AND the .env file doesn't exist
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pandas as pd
import pytest

from nfo.specs.loader import load_strategy, reset_registry_for_tests


REPO_ROOT = Path(__file__).resolve().parents[3]
SIGNALS = REPO_ROOT / "results" / "nfo" / "historical_signals.parquet"
LIVE_HTE_CSV = REPO_ROOT / "results" / "nfo" / "v3_live_trades_hte.csv"
INDEX_CACHE = REPO_ROOT / "data" / "nfo" / "index"
STRAT_PATH = REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_live_rule.yaml"

# Cache range the legacy `_legacy_main` uses.
CACHE_FROM = "2023-12-15"
CACHE_TO = "2026-04-18"


_HAS_CREDS = bool(os.environ.get("DHAN_CLIENT_ID") and os.environ.get("DHAN_ACCESS_TOKEN"))
_HAS_DOTENV = (REPO_ROOT / ".env").exists()
_HAS_NIFTY_CACHE = (INDEX_CACHE / f"NIFTY_{CACHE_FROM}_{CACHE_TO}.parquet").exists()


@pytest.fixture(autouse=True)
def _restore_real_registry(monkeypatch, tmp_path):
    """Force loader to read the real committed registry so v3_live_rule loads."""
    from nfo.specs import loader
    monkeypatch.setattr(
        loader, "_REGISTRY_PATH",
        REPO_ROOT / "configs" / "nfo" / ".registry.json",
        raising=True,
    )


def _import_redesign_variants():
    path = REPO_ROOT / "scripts" / "nfo" / "redesign_variants.py"
    spec = importlib.util.spec_from_file_location("_legacy_rv_live_replay", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_legacy_rv_live_replay"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(
    not (SIGNALS.exists() and LIVE_HTE_CSV.exists()
         and _HAS_NIFTY_CACHE and (_HAS_CREDS or _HAS_DOTENV)
         and STRAT_PATH.exists()),
    reason=(
        "requires cached signals parquet, legacy v3_live_trades_hte.csv, "
        "NIFTY underlying-daily cache, and DHAN creds (env or .env)"
    ),
)
def test_live_replay_parity_vs_legacy_hte():
    """Engine `run_live_replay` matches the legacy `v3_live_trades_hte.csv` output.

    Compares on the identity-bearing columns:
      - entry_date, expiry_date (byte-exact)
      - outcome (byte-exact)
      - pnl_contract (within 1e-6 relative tolerance)
    """
    from nfo.client import DhanClient
    from nfo.data import load_underlying_daily
    from nfo.studies.live_replay import LiveReplayResult, run_live_replay
    from nfo.universe import get as get_under

    rv = _import_redesign_variants()

    spec, _ = load_strategy(STRAT_PATH)
    assert spec.selection_rule.mode == "live_rule"
    assert spec.exit_rule.variant == "hte"

    df = pd.read_parquet(SIGNALS)
    df["date"] = pd.to_datetime(df["date"])
    atr = rv.load_nifty_atr(df["date"])

    def _event_resolver(entry, dte):
        return "high" if not rv._event_pass(
            entry, dte, severity_high_kinds={"RBI", "FOMC", "BUDGET"},
            window_days=10,
        ) else "none"

    under = get_under("NIFTY")
    client = DhanClient()
    try:
        spot_daily = load_underlying_daily(
            client, under, from_date=CACHE_FROM, to_date=CACHE_TO,
        )
        if spot_daily.empty:
            pytest.skip("cached underlying-daily frame is empty")

        result: LiveReplayResult = run_live_replay(
            spec=spec, features_df=df, atr_series=atr,
            spot_daily=spot_daily, client=client, under=under,
            event_resolver=_event_resolver,
        )
    finally:
        client.close()

    legacy = pd.read_csv(LIVE_HTE_CSV)
    legacy = legacy[legacy["variant"] == "hte"].copy()
    legacy = legacy.sort_values(["entry_date", "expiry_date"]).reset_index(drop=True)

    engine_df = result.selected_trades.copy()
    # If the engine produced no trades (e.g. cache gap), skip rather than fail.
    if engine_df.empty:
        pytest.skip("engine run_live_replay returned no trades — cache coverage insufficient")

    # Normalise engine date columns to ISO-string to match the legacy CSV.
    for col in ("entry_date", "expiry_date", "exit_date"):
        if col in engine_df.columns:
            engine_df[col] = engine_df[col].astype(str)
    engine_df = engine_df.sort_values(["entry_date", "expiry_date"]).reset_index(drop=True)

    # Trade count must match exactly (same cycles should resolve).
    assert len(engine_df) == len(legacy), (
        f"trade count mismatch: engine={len(engine_df)} legacy={len(legacy)}\n"
        f"engine entry_dates: {engine_df['entry_date'].tolist()}\n"
        f"legacy entry_dates: {legacy['entry_date'].tolist()}"
    )

    # Byte-exact entry_date + expiry_date + outcome.
    pd.testing.assert_series_equal(
        engine_df["entry_date"].reset_index(drop=True),
        legacy["entry_date"].reset_index(drop=True),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        engine_df["expiry_date"].reset_index(drop=True),
        legacy["expiry_date"].reset_index(drop=True),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        engine_df["outcome"].reset_index(drop=True),
        legacy["outcome"].reset_index(drop=True),
        check_names=False,
    )

    # pnl_contract within 1e-6 relative tolerance.
    for i in range(len(engine_df)):
        e = float(engine_df["pnl_contract"].iloc[i])
        L = float(legacy["pnl_contract"].iloc[i])
        denom = max(abs(L), 1.0)
        assert abs(e - L) / denom < 1e-6, (
            f"pnl_contract drift at row {i}: engine={e} legacy={L} "
            f"(entry={engine_df['entry_date'].iloc[i]})"
        )
