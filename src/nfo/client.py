"""Thin Dhan v2 HTTP client.

We use two separate rate-limit buckets because Dhan publishes two distinct
ceilings:

  * `charts/*` and `instruments` → 20 req/sec (the general non-trading bucket)
  * `optionchain`              → 1 req / 3 sec (bucket isolated per the docs)

The `optionchain` methods aren't used by the v1 backtest (expired-history flow
relies on rollingoption + charts/historical), but keeping the bucket split
prevents us from tripping the stricter limit once live-signal mode lands.

Retries on 429/5xx use exponential backoff via tenacity, cribbed from
``src/csp/client.py`` for consistency.
"""
from __future__ import annotations

import threading
import time

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import DHAN_ACCESS_TOKEN, DHAN_BASE_URL, DHAN_CLIENT_ID


class _Bucket:
    """Minimum-interval rate limiter — blocks until enough time has passed."""

    __slots__ = ("_interval", "_lock", "_next_ok")

    def __init__(self, max_per_sec: float) -> None:
        self._interval = 1.0 / max_per_sec
        self._lock = threading.Lock()
        self._next_ok = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_ok - now
            if sleep_for > 0:
                time.sleep(sleep_for)
                now = time.monotonic()
            self._next_ok = now + self._interval


class DhanError(RuntimeError):
    """Raised on Dhan API logical errors (errorCode / errorMessage payload)."""


class DhanClient:
    """HTTP client for Dhan v2. Usable as a context manager."""

    CHARTS_BUCKET = _Bucket(max_per_sec=20.0)
    OPTIONCHAIN_BUCKET = _Bucket(max_per_sec=1.0 / 3.0)

    def __init__(self, timeout: float = 30.0) -> None:
        headers = {
            "access-token": DHAN_ACCESS_TOKEN,
            "client-id": DHAN_CLIENT_ID,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._http = httpx.Client(base_url=DHAN_BASE_URL, headers=headers, timeout=timeout)

    def __enter__(self) -> "DhanClient":
        return self

    def __exit__(self, *_exc) -> None:
        self._http.close()

    def close(self) -> None:
        self._http.close()

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    def _post(self, path: str, body: dict, bucket: _Bucket) -> dict:
        bucket.wait()
        r = self._http.post(path, json=body)
        if r.status_code == 429:
            r.raise_for_status()
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("errorCode"):
            raise DhanError(f"{data.get('errorCode')}: {data.get('errorMessage')}")
        return data

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=16),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    def _get(self, url: str, bucket: _Bucket) -> httpx.Response:
        bucket.wait()
        r = self._http.get(url)
        r.raise_for_status()
        return r

    # -- Account -------------------------------------------------------

    def fund_limit(self) -> dict:
        r = self._http.get("/fundlimit")
        r.raise_for_status()
        return r.json()

    # -- Chain -------------------------------------------------------

    def optionchain_expiry_list(self, underlying_scrip: int, underlying_seg: str) -> list[str]:
        body = {"UnderlyingScrip": underlying_scrip, "UnderlyingSeg": underlying_seg}
        data = self._post("/optionchain/expirylist", body, self.OPTIONCHAIN_BUCKET)
        return list(data.get("data", []))

    def option_chain(self, underlying_scrip: int, underlying_seg: str, expiry: str) -> dict:
        """Live option chain with Greeks. Uses the 1-per-3-sec bucket and the
        shared retry/error-handling path in `_post` (429/5xx backoff).
        """
        body = {"UnderlyingScrip": underlying_scrip, "UnderlyingSeg": underlying_seg, "Expiry": expiry}
        return self._post("/optionchain", body, self.OPTIONCHAIN_BUCKET)

    # -- Expired options (rolling ATM frame) -------------------------------

    def rolling_option(
        self,
        *,
        exchange_segment: str,
        instrument: str,
        security_id: int,
        expiry_code: int,
        expiry_flag: str,
        strike: str,
        drv_option_type: str,
        interval: int,
        from_date: str,
        to_date: str,
        required_data: list[str] | None = None,
    ) -> dict:
        body = {
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "securityId": str(security_id),
            "expiryCode": int(expiry_code),
            "expiryFlag": expiry_flag,
            "strike": strike,
            "drvOptionType": drv_option_type,
            "interval": int(interval),
            "requiredData": required_data or [
                "open", "high", "low", "close", "iv", "volume", "oi", "spot", "strike",
            ],
            "fromDate": from_date,
            "toDate": to_date,
        }
        return self._post("/charts/rollingoption", body, self.CHARTS_BUCKET)

    # -- Fixed-contract bars -----------------------------------------

    def chart_historical(
        self,
        *,
        exchange_segment: str,
        instrument: str,
        security_id: int,
        from_date: str,
        to_date: str,
        expiry_code: int | None = None,
        oi: bool = False,
    ) -> dict:
        body = {
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "securityId": str(security_id),
            "fromDate": from_date,
            "toDate": to_date,
            "oi": oi,
        }
        if expiry_code is not None:
            body["expiryCode"] = int(expiry_code)
        return self._post("/charts/historical", body, self.CHARTS_BUCKET)

    def chart_intraday(
        self,
        *,
        exchange_segment: str,
        instrument: str,
        security_id: int,
        interval: int,
        from_date: str,
        to_date: str,
        expiry_code: int | None = None,
        oi: bool = True,
    ) -> dict:
        body = {
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "securityId": str(security_id),
            "interval": int(interval),
            "fromDate": from_date,
            "toDate": to_date,
            "oi": oi,
        }
        if expiry_code is not None:
            body["expiryCode"] = int(expiry_code)
        return self._post("/charts/intraday", body, self.CHARTS_BUCKET)

    # -- Instrument master --------------------------------------------

    def fetch_instrument_master_csv(self) -> bytes:
        """Download the detailed instrument master CSV (~100MB).

        The /v2/instrument-list* URL requires no auth and returns CSV bytes.
        Callers should stream to disk, not hold in memory repeatedly.
        """
        # Dhan's instrument master is served on a static CDN, not through the
        # JSON API — we use a separate GET without JSON headers.
        url = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
        self.CHARTS_BUCKET.wait()
        with httpx.Client(timeout=180.0) as raw:
            r = raw.get(url)
            r.raise_for_status()
            return r.content
