"""Parity: engine.execution.run_cycle_from_dhan matches legacy backtest._run_cycle.

Runs a handful of known cycles from ``results/nfo/spread_trades.csv`` through
``run_cycle_from_dhan`` and compares ``SimulatedTrade.spread_trade.pnl_contract``
against the legacy CSV value within 1e-6 relative tolerance.

Relies on cached Dhan data under ``data/nfo/``. If a cycle's cache is missing
(or the DHAN creds are absent), the test is skipped rather than failing, so
the suite stays green on fresh checkouts.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from nfo import cache as _cache
from nfo.specs.loader import load_strategy, reset_registry_for_tests
from nfo.specs.strategy import ExitSpec, SelectionSpec, StrategySpec
from nfo.universe import get as get_under


REPO_ROOT = Path(__file__).resolve().parents[3]
CSV = REPO_ROOT / "results" / "nfo" / "spread_trades.csv"
INDEX_CACHE = REPO_ROOT / "data" / "nfo" / "index"


# ---------------------------------------------------------------------------
# Preconditions (cached data + creds)
# ---------------------------------------------------------------------------

_HAS_CREDS = bool(os.environ.get("DHAN_CLIENT_ID") and os.environ.get("DHAN_ACCESS_TOKEN"))
_HAS_DOTENV = (REPO_ROOT / ".env").exists()
_HAS_NIFTY_INDEX = any(
    p.name.startswith("NIFTY_") and p.suffix == ".parquet"
    for p in INDEX_CACHE.glob("*.parquet")
)


def _nifty_index_range() -> tuple[str, str] | None:
    """Find a cached NIFTY underlying-daily range that spans 2024-02..2026."""
    candidates = sorted(INDEX_CACHE.glob("NIFTY_*.parquet"))
    for p in candidates:
        # name format: NIFTY_<from>_<to>.parquet
        parts = p.stem.split("_")
        if len(parts) < 3:
            continue
        from_s, to_s = parts[1], parts[2]
        try:
            from_d = date.fromisoformat(from_s)
            to_d = date.fromisoformat(to_s)
        except ValueError:
            continue
        if from_d <= date(2024, 2, 15) and to_d >= date(2025, 12, 31):
            return from_s, to_s
    return None


_RANGE = _nifty_index_range()


def _variant_for(row: pd.Series) -> ExitSpec:
    pt = float(row["param_pt"])
    manage = row["param_manage"]
    manage_int: int | None
    try:
        manage_int = int(float(manage)) if pd.notna(manage) and str(manage).strip() else None
    except (ValueError, TypeError):
        manage_int = None
    if pt >= 1.0 and manage_int is None:
        return ExitSpec(variant="hte", profit_take_fraction=1.0, manage_at_dte=None)
    if pt == 0.25:
        name = "pt25"
    elif pt == 0.75:
        name = "pt75"
    else:
        name = "pt50"
    return ExitSpec(variant=name, profit_take_fraction=pt, manage_at_dte=manage_int)


def _strategy_for(row: pd.Series) -> StrategySpec:
    """Build a StrategySpec matching the CSV row's params (delta/width/pt/manage)."""
    exit_spec = _variant_for(row)

    # V3-frozen is the baseline; we override exit/universe to match the row.
    strat_path = REPO_ROOT / "configs" / "nfo" / "strategies" / "v3_frozen.yaml"
    base, _ = load_strategy(strat_path)
    universe = base.universe.model_copy(update={
        "delta_target": float(row["param_delta"]),
        "width_value": float(row["param_width"]),
    })
    # Ensure selection_rule.preferred_exit_variant matches exit_spec.variant so
    # the model validators are satisfied; use cycle_matched (no live-rule extra
    # constraints triggered here).
    selection = base.selection_rule.model_copy(update={
        "preferred_exit_variant": exit_spec.variant,
        "mode": "cycle_matched",
    })
    spec = base.model_copy(update={
        "universe": universe,
        "selection_rule": selection,
        "exit_rule": exit_spec,
    })
    # Re-validate by constructing a fresh StrategySpec (model_copy skips validators).
    return StrategySpec.model_validate(spec.model_dump())


# ---------------------------------------------------------------------------
# Parity harness
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not CSV.exists() or not _HAS_NIFTY_INDEX or _RANGE is None,
    reason="requires cached Dhan data (results/nfo/spread_trades.csv + data/nfo/index/NIFTY_*.parquet)",
)


# Known cycles to sample (V3-HTE expired_worthless + pt50 profit_take + V3-HTE max_loss).
# Each tuple: (entry_date, expiry_date, param_delta, param_width, param_pt, param_manage).
# The (width, pt, manage) triple must match a row in the CSV to look up the legacy pnl.
_SAMPLE_CYCLES = [
    (date(2024, 2, 22), date(2024, 3, 28), 0.3, 100.0, 1.0, ""),   # expired_worthless
    (date(2024, 3, 21), date(2024, 4, 25), 0.3, 100.0, 0.5, "21"), # profit_take (pt50)
    (date(2024, 10, 24), date(2024, 11, 28), 0.3, 100.0, 1.0, ""), # max_loss (V3 HTE)
]


def _rolling_cache_exists(entry_d: date, expiry_d: date) -> bool:
    """Cheap heuristic: rolling parquets exist for the cycle's entry date."""
    rolling_dir = REPO_ROOT / "data" / "nfo" / "rolling"
    pattern = f"NIFTY_MONTH1_PUT_-1_{entry_d.isoformat()}_{expiry_d.isoformat()}.parquet"
    return (rolling_dir / pattern).exists()


@pytest.fixture
def _iso_registry(tmp_path):
    reset_registry_for_tests(tmp_path / "registry.json")


@pytest.mark.parametrize("entry_d, expiry_d, delta, width, pt, manage", _SAMPLE_CYCLES)
def test_run_cycle_from_dhan_parity(
    _iso_registry, entry_d, expiry_d, delta, width, pt, manage,
):
    # Skip if the cached rolling parquets aren't present for this cycle.
    if not _rolling_cache_exists(entry_d, expiry_d):
        pytest.skip(
            f"rolling cache missing for NIFTY entry={entry_d} expiry={expiry_d}"
        )

    # Lazy imports: the engine module touches DhanClient → .env → DHAN_CLIENT_ID.
    # Only import once we know we have preconditions.
    if not _HAS_CREDS and not _HAS_DOTENV:
        pytest.skip("no DHAN creds and no .env — run_cycle_from_dhan requires DhanClient()")
    from nfo.client import DhanClient
    from nfo.data import load_underlying_daily
    from nfo.engine.execution import run_cycle_from_dhan

    # Find the legacy CSV row.
    df = pd.read_csv(CSV)
    mask = (
        (df["underlying"] == "NIFTY")
        & (df["entry_date"] == entry_d.isoformat())
        & (df["expiry_date"] == expiry_d.isoformat())
        & (df["param_delta"] == delta)
        & (df["param_width"] == width)
        & (df["param_pt"] == pt)
    )
    if manage == "":
        mask = mask & (df["param_manage"].isna() | (df["param_manage"].astype(str) == ""))
    else:
        mask = mask & (df["param_manage"].astype(str).isin([manage, f"{manage}.0"]))
    rows = df[mask]
    if rows.empty:
        pytest.skip(
            f"no legacy CSV row for {entry_d} → {expiry_d} (d={delta},w={width},pt={pt},m={manage})"
        )
    legacy_row = rows.iloc[0]

    # Build StrategySpec from the row params.
    spec = _strategy_for(legacy_row)
    under = get_under("NIFTY")

    from_s, to_s = _RANGE
    client = DhanClient()
    try:
        # Cached NIFTY daily bars — must use the exact cache-key range, since
        # load_underlying_daily keys the cache by (underlying, from_date,
        # to_date). Using a non-cached range would trigger a live API call.
        spot_daily = load_underlying_daily(
            client, under, from_date=from_s, to_date=to_s,
        )
        if spot_daily.empty:
            pytest.skip("cached underlying-daily frame is empty")

        out = run_cycle_from_dhan(
            client=client, under=under, strategy_spec=spec,
            entry_date=entry_d, expiry_date=expiry_d, spot_daily=spot_daily,
        )
    except Exception as exc:  # noqa: BLE001
        if _is_network_error(exc):
            pytest.skip(f"cached data insufficient (network hit): {exc}")
        raise
    finally:
        client.close()

    assert out is not None, (
        f"run_cycle_from_dhan returned None for {entry_d} → {expiry_d}"
    )

    engine_pnl = out.spread_trade.pnl_contract
    legacy_pnl = float(legacy_row["pnl_contract"])
    assert engine_pnl == pytest.approx(legacy_pnl, rel=1e-6), (
        f"pnl_contract mismatch for {entry_d} → {expiry_d}: "
        f"engine={engine_pnl}, legacy={legacy_pnl}"
    )

    # Also confirm outcome label parity (stronger than just the dollar).
    assert out.spread_trade.outcome == legacy_row["outcome"]


def _is_network_error(exc: BaseException) -> bool:
    """True if `exc` looks like an HTTP / connectivity failure (not a logic bug)."""
    import httpx
    if isinstance(exc, (httpx.TransportError, httpx.HTTPStatusError)):
        return True
    # tenacity re-raises the last attempt's exception, which is usually an httpx one.
    from nfo.client import DhanError
    if isinstance(exc, DhanError):
        return True
    return False
