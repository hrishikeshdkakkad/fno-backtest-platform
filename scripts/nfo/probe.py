"""Phase-1 wire probe.

Validates every Dhan v2 endpoint we'll depend on, one by one, with
human-readable output. Gate for all further NFO work.

Checks:
  1. Auth — /fundlimit
  2. Expiry list — NIFTY + BANKNIFTY
  3. Instrument master — CSV size, NFO-options subset row count, column resolution
  4. Rolling option — NIFTY Mar-2025 ATM PE, confirm IV/OHLC/timestamp shape
  5. securityId round-trip — pick a known strike, resolve via master, pull
     /charts/historical for that contract, confirm rows return
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone, timedelta

import pandas as pd

from nfo import universe
from nfo.client import DhanClient
from nfo.instruments import load_or_refresh_master, resolve_option_security_id


IST = timezone(timedelta(hours=5, minutes=30))


def _hr(title: str) -> None:
    print(f"\n== {title} ==", flush=True)


def _ist(epoch_s: int) -> str:
    return datetime.fromtimestamp(int(epoch_s), IST).strftime("%Y-%m-%d %H:%M:%S %Z")


def main() -> int:
    with DhanClient() as client:
        # 1. Auth
        _hr("1. Fund limit (auth smoke-test)")
        fl = client.fund_limit()
        print(f"  client id: {fl.get('dhanClientId')}  balance: ₹{fl.get('availabelBalance')}")

        # 2. Expiry list
        _hr("2. Expiry list")
        for name in ("NIFTY", "BANKNIFTY"):
            u = universe.get(name)
            expiries = client.optionchain_expiry_list(u.security_id, u.underlying_seg)
            print(f"  {name}: {len(expiries)} expiries; first 4 = {expiries[:4]}")

        # 3. Instrument master
        _hr("3. Instrument master (~100MB download on cold cache)")
        master = load_or_refresh_master(client)
        print(f"  NFO options rows: {len(master):,}")
        by_inst = master["instrument_type"].value_counts().to_dict()
        print(f"  by instrument_type: {by_inst}")
        by_flag = master["expiry_flag"].value_counts().to_dict()
        print(f"  by expiry_flag: {by_flag}")
        nifty_rows = master[master["underlying_symbol"] == "NIFTY"]
        nifty_expiries = sorted({d for d in nifty_rows["expiry_date"] if d is not None and not pd.isna(d)})
        print(f"  NIFTY contracts: {len(nifty_rows):,} across {len(nifty_expiries)} expiries")
        print(f"  NIFTY expiry range: {nifty_expiries[:2]} ... {nifty_expiries[-2:]}")

        # 4. Rolling option (expired NIFTY Mar-2025 ATM PE)
        _hr("4. rolling_option — NIFTY Mar-2025 ATM PE, 60-min")
        n = universe.get("NIFTY")
        resp = client.rolling_option(
            exchange_segment=n.exchange_segment,
            instrument=n.instrument,
            security_id=n.security_id,
            expiry_code=1,
            expiry_flag="MONTH",
            strike="ATM",
            drv_option_type="PUT",
            interval=60,
            from_date="2025-03-01",
            to_date="2025-03-28",
        )
        pe = (resp.get("data") or {}).get("pe") or {}
        if not pe:
            print("  ✗ empty response"); return 2
        n_candles = len(pe.get("close", []))
        first_ts = pe["timestamp"][0]
        last_ts = pe["timestamp"][-1]
        print(f"  candles: {n_candles}  IV range: [{min(pe['iv']):.2f}, {max(pe['iv']):.2f}]")
        print(f"  first bar: {_ist(first_ts)}  strike={pe['strike'][0]}  spot={pe['spot'][0]}  close={pe['close'][0]}")
        print(f"  last  bar: {_ist(last_ts)}  strike={pe['strike'][-1]}  spot={pe['spot'][-1]}  close={pe['close'][-1]}")

        # 5. Fixed-strike reconstruction — the BACKTEST's actual data path
        #    Instrument master only holds live/future contracts, so we cannot
        #    resolve historical expired securityIds from it. Instead we sweep
        #    rollingoption across ATM offsets for a wide range and filter by
        #    absolute strike. This IS the backtest's data path.
        _hr("5. Fixed-strike reconstruction (NIFTY Mar-2025 22500 PE)")
        TARGET_STRIKE = 22500.0
        series: dict[int, float] = {}  # timestamp → close
        for offset in range(-3, 8):
            strike_arg = "ATM" if offset == 0 else f"ATM{offset:+d}"
            r = client.rolling_option(
                exchange_segment=n.exchange_segment,
                instrument=n.instrument,
                security_id=n.security_id,
                expiry_code=1,
                expiry_flag="MONTH",
                strike=strike_arg,
                drv_option_type="PUT",
                interval=60,
                from_date="2025-03-01",
                to_date="2025-03-28",
            )
            leg = (r.get("data") or {}).get("pe") or {}
            for i, k in enumerate(leg.get("strike", [])):
                if k == TARGET_STRIKE:
                    series[leg["timestamp"][i]] = leg["close"][i]
        by_day: dict[str, float] = {}
        for ts, close_px in sorted(series.items()):
            day = datetime.fromtimestamp(int(ts), IST).strftime("%Y-%m-%d")
            by_day[day] = close_px
        print(f"  reconstructed {len(by_day)} trading-day closes for 22500 PE")
        for day, close_px in list(by_day.items())[:3]:
            print(f"    {day}: close=₹{close_px}")
        if list(by_day.items())[3:]:
            print(f"    ...")
            for day, close_px in list(by_day.items())[-3:]:
                print(f"    {day}: close=₹{close_px}")
        if len(by_day) < 5:
            print("  ✗ expected ≥ 5 trading days for a month — offset range too narrow or spot drifted > range")
            return 5

        _hr("PROBE PASSED — Phase 2 can begin")
        return 0


if __name__ == "__main__":
    sys.exit(main())
