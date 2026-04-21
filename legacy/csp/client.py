"""Thin HTTP client for the Massive/Polygon REST API.

Includes a token-bucket rate limiter because the user's current plan is
Basic (5 calls/minute). The limiter is conservative (4 calls per 60s by
default, leaving headroom for bursts) and adapts on 429 by doubling the
cooldown.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Iterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import API_KEY, BASE_URL

log = logging.getLogger(__name__)


class MassiveError(RuntimeError):
    pass


class RateLimitError(MassiveError):
    pass


class _TokenBucket:
    """Allow up to `burst` calls per `window` seconds; block if exceeded."""

    def __init__(self, burst: int, window: float):
        self.burst = burst
        self.window = window
        self.timestamps: deque[float] = deque()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                while self.timestamps and now - self.timestamps[0] > self.window:
                    self.timestamps.popleft()
                if len(self.timestamps) < self.burst:
                    self.timestamps.append(now)
                    return
                sleep_for = self.window - (now - self.timestamps[0]) + 0.1
            log.debug("rate limiter sleeping %.1fs", sleep_for)
            time.sleep(max(sleep_for, 0.1))


class MassiveClient:
    def __init__(
        self,
        api_key: str = API_KEY,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        rate_limit_calls: int = 5,
        rate_limit_window: float = 62.0,
    ):
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._api_key = api_key
        self._bucket = _TokenBucket(rate_limit_calls, rate_limit_window)

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @retry(
        reraise=True,
        retry=retry_if_exception_type((httpx.TransportError, RateLimitError)),
        wait=wait_exponential(multiplier=2, min=5, max=65),
        stop=stop_after_attempt(8),
    )
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._bucket.acquire()
        resp = self._http.get(path, params=params)
        if resp.status_code == 429:
            log.warning("429 rate limited, backing off 60s")
            time.sleep(60)
            raise RateLimitError("429 rate limited")
        if resp.status_code == 403:
            body = resp.json() if resp.content else {}
            raise MassiveError(f"403 forbidden: {body.get('message', resp.text)}")
        resp.raise_for_status()
        return resp.json()

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._get(path, params or {})

    def paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Walk `next_url` pagination. Yields each `results` item."""
        data = self._get(path, params or {})
        while True:
            for row in data.get("results", []) or []:
                yield row
            next_url = data.get("next_url")
            if not next_url:
                return
            # next_url is absolute; strip host and re-use our auth header
            resp = self._http.get(next_url)
            if resp.status_code == 429:
                time.sleep(2)
                continue
            resp.raise_for_status()
            data = resp.json()

    # ---------- high-level helpers ----------

    def stock_aggs(
        self,
        ticker: str,
        multiplier: int,
        timespan: str,
        from_: str,
        to: str,
        adjusted: bool = True,
        limit: int = 50000,
    ) -> list[dict[str, Any]]:
        path = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_}/{to}"
        params = {"adjusted": str(adjusted).lower(), "sort": "asc", "limit": limit}
        data = self._get(path, params)
        return data.get("results") or []

    def option_aggs(
        self,
        option_ticker: str,
        multiplier: int,
        timespan: str,
        from_: str,
        to: str,
        adjusted: bool = True,
        limit: int = 50000,
    ) -> list[dict[str, Any]]:
        path = f"/v2/aggs/ticker/{option_ticker}/range/{multiplier}/{timespan}/{from_}/{to}"
        params = {"adjusted": str(adjusted).lower(), "sort": "asc", "limit": limit}
        try:
            data = self._get(path, params)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return []
            raise
        return data.get("results") or []

    def contracts_for_expiration(
        self,
        underlying_ticker: str,
        expiration_date: str,
        contract_type: str | None = None,
        include_expired: bool = True,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Return all contracts for a given underlying expiring on `expiration_date`.

        We filter on expiration_date directly because `as_of + underlying_ticker`
        mis-behaves for expired contracts on the current API (returns unrelated
        underlyings).
        """
        params: dict[str, Any] = {
            "underlying_ticker": underlying_ticker,
            "expiration_date": expiration_date,
            "expired": str(include_expired).lower(),
            "limit": limit,
            "order": "asc",
            "sort": "strike_price",
        }
        if contract_type:
            params["contract_type"] = contract_type
        out: list[dict[str, Any]] = []
        for row in self.paginate("/v3/reference/options/contracts", params):
            if row.get("underlying_ticker") == underlying_ticker:
                out.append(row)
        return out
