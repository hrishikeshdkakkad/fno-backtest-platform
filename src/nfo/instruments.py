"""Dhan instrument master — fetch, cache, resolve securityIds.

The detailed CSV at images.dhan.co is ~100MB. We cache the filtered NFO-options
subset as parquet. Schema varies by CSV version, so column resolution is done
by flexible alias lookup — if Dhan renames a column tomorrow, failure is a
clear `KeyError`, not silent data corruption.

Usage:
    master = load_or_refresh_master(client)
    sid = resolve_option_security_id(
        master, underlying="NIFTY", expiry=date(2024, 6, 27),
        strike=23000, option_type="PE",
    )
"""
from __future__ import annotations

import io
from datetime import date, datetime

import pandas as pd

from .client import DhanClient
from .config import DATA_DIR

MASTER_PARQUET = DATA_DIR / "instruments_nfo_options.parquet"
MASTER_RAW_CSV = DATA_DIR / "api-scrip-master-detailed.csv"
REFRESH_HOURS = 24


# Canonical columns → known aliases in Dhan's CSV over the years.
_COL_ALIASES: dict[str, tuple[str, ...]] = {
    "security_id":        ("SECURITY_ID", "SEM_SMST_SECURITY_ID"),
    "exchange":           ("EXCH_ID", "SEM_EXM_EXCH_ID"),
    "segment":            ("SEGMENT", "SEM_SEGMENT"),
    "instrument_type":    ("INSTRUMENT_TYPE", "SEM_INSTRUMENT_NAME"),
    "underlying_symbol":  ("UNDERLYING_SYMBOL", "SM_SYMBOL_NAME"),
    "trading_symbol":     ("SYMBOL_NAME", "SEM_TRADING_SYMBOL"),
    "display_name":       ("DISPLAY_NAME", "SEM_CUSTOM_SYMBOL"),
    "expiry_date":        ("SM_EXPIRY_DATE", "SEM_EXPIRY_DATE", "EXPIRY_DATE"),
    "strike_price":       ("STRIKE_PRICE", "SEM_STRIKE_PRICE"),
    "option_type":        ("OPTION_TYPE", "SEM_OPTION_TYPE"),
    "expiry_flag":        ("EXPIRY_FLAG",),
    "lot_size":           ("LOT_SIZE", "SEM_LOT_UNITS"),
}


def _pick_column(df: pd.DataFrame, canonical: str) -> str:
    for alias in _COL_ALIASES[canonical]:
        if alias in df.columns:
            return alias
    raise KeyError(
        f"Dhan instrument CSV missing all known aliases for {canonical!r}; "
        f"looked for {_COL_ALIASES[canonical]!r}; columns present: {list(df.columns)[:40]}..."
    )


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Rename Dhan columns to canonical names; coerce dtypes."""
    ren = {_pick_column(df, c): c for c in _COL_ALIASES}
    out = df.rename(columns=ren)[list(_COL_ALIASES)].copy()
    out["expiry_date"] = pd.to_datetime(out["expiry_date"], errors="coerce").dt.date
    out["strike_price"] = pd.to_numeric(out["strike_price"], errors="coerce")
    out["security_id"] = pd.to_numeric(out["security_id"], errors="coerce").astype("Int64")
    out["lot_size"] = pd.to_numeric(out["lot_size"], errors="coerce").astype("Int64")
    for c in ("option_type", "instrument_type", "underlying_symbol",
              "trading_symbol", "display_name", "expiry_flag", "exchange", "segment"):
        out[c] = out[c].astype(str).str.upper().str.strip()
    return out


def _filter_nfo_options(df: pd.DataFrame) -> pd.DataFrame:
    # Index options (OPTIDX) and stock options (OPTSTK) on NFO.
    inst = df["instrument_type"]
    mask = inst.isin({"OPTIDX", "OPTSTK"})
    return df.loc[mask].reset_index(drop=True)


def _should_refresh() -> bool:
    if not MASTER_PARQUET.exists():
        return True
    age_h = (datetime.now().timestamp() - MASTER_PARQUET.stat().st_mtime) / 3600
    return age_h > REFRESH_HOURS


def refresh_master(client: DhanClient) -> pd.DataFrame:
    """Fetch the CSV fresh, filter to NFO options, cache parquet, return it."""
    raw_bytes = client.fetch_instrument_master_csv()
    MASTER_RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
    MASTER_RAW_CSV.write_bytes(raw_bytes)
    raw = pd.read_csv(io.BytesIO(raw_bytes), low_memory=False)
    normalised = _normalise(raw)
    filtered = _filter_nfo_options(normalised)
    filtered.to_parquet(MASTER_PARQUET, index=False)
    return filtered


def load_or_refresh_master(client: DhanClient) -> pd.DataFrame:
    if _should_refresh():
        return refresh_master(client)
    return pd.read_parquet(MASTER_PARQUET)


def resolve_option_security_id(
    master: pd.DataFrame,
    underlying: str,
    expiry: date,
    strike: float,
    option_type: str,
) -> int:
    """Return the Dhan securityId for a specific option contract.

    Matches on UNDERLYING_SYMBOL + expiry_date + strike + option_type.
    Raises KeyError if no match.
    """
    und = underlying.upper()
    opt = option_type.upper()
    cand = master[
        (master["underlying_symbol"] == und)
        & (master["expiry_date"] == expiry)
        & (master["option_type"] == opt)
        & (master["strike_price"] == float(strike))
    ]
    if cand.empty:
        raise KeyError(
            f"No option securityId for {und} {expiry.isoformat()} {strike} {opt} — "
            f"master may be stale, contract never listed, or expired beyond master's retention window."
        )
    if len(cand) > 1:
        cand = cand.sort_values("security_id").head(1)
    return int(cand["security_id"].iloc[0])
