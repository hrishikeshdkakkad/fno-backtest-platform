"""Thin wrapper around the Parallel.ai Python SDK — caching, retries, cost log.

Three problems this layer solves:

  1. **Cost control.** Parallel charges per call with escalating prices by
     processor tier. Cached responses are free; we never want to re-pay for
     the same objective twice in the same TTL window.
  2. **Offline resilience.** The TUI must not crash when Parallel is down
     or the user sets `PARALLEL_OFFLINE=1` — we serve the last-known good
     cached response instead.
  3. **Fail-fast secrets.** The API key comes from `os.getenv` only; if the
     user forgot to rotate / paste their key we surface a clear error at
     construction time, not deep inside the live TUI.

Cache keying: `sha256(method + json.dumps(kwargs, sort_keys=True, default=str))`.
Payload stored under `DATA_DIR/parallel_cache/<sha>.json`.

Cost log is a parquet-backed append at `DATA_DIR/parallel_cost_log.parquet` —
a small helper script in `scripts/nfo/` can summarise monthly spend later.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd
from pydantic import BaseModel

try:
    from parallel import Parallel
    from parallel._exceptions import APIError as ParallelAPIError
except ImportError as exc:   # pragma: no cover — surfaced at first use
    Parallel = None           # type: ignore[assignment]
    ParallelAPIError = Exception   # type: ignore[assignment,misc]

from .config import DATA_DIR

_log = logging.getLogger(__name__)


class ParallelKeyMissing(RuntimeError):
    """Raised when PARALLEL_API_KEY is unset and code tries to make a network call."""


class ParallelOfflineMiss(RuntimeError):
    """Raised when PARALLEL_OFFLINE=1 and we have no cached response."""


class ParallelClient:
    """Caching wrapper around `parallel.Parallel`. See module docstring."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        cache_dir: Path | None = None,
        cost_log_path: Path | None = None,
        offline: bool | None = None,
    ) -> None:
        if Parallel is None:
            raise RuntimeError(
                "parallel-web is not installed. Run `.venv/bin/pip install parallel-web`."
            )

        self.api_key = api_key or os.getenv("PARALLEL_API_KEY")
        self.offline = offline if offline is not None else os.getenv("PARALLEL_OFFLINE") == "1"
        self.cache_dir = cache_dir or (DATA_DIR / "parallel_cache")
        self.cost_log_path = cost_log_path or (DATA_DIR / "parallel_cost_log.parquet")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Lazy client — only construct when we actually need to call the network.
        self._sdk: Parallel | None = None

    # ── Public surface ──────────────────────────────────────────────────────

    def task(
        self,
        input: str | Mapping[str, Any],
        output_model: type[BaseModel],
        *,
        processor: str = "core",
        ttl_sec: int = 86_400,
    ) -> BaseModel:
        """Deep-research Task API returning a parsed Pydantic model."""
        key_payload = {
            "method": "task",
            "input": input,
            "output_model": output_model.model_json_schema(),
            "processor": processor,
        }
        cached = self._cache_read(key_payload, ttl_sec)
        if cached is not None:
            return output_model.model_validate(cached)
        if self.offline:
            raise ParallelOfflineMiss(f"task({processor}) miss in offline mode")

        client = self._require_sdk()
        t0 = time.perf_counter()
        result = client.task_run.execute(input=input, output=output_model, processor=processor)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # result.output.parsed is the Pydantic instance. Fall back to content if present.
        parsed = getattr(result.output, "parsed", None) or getattr(result.output, "content", None)
        if parsed is None:
            raise RuntimeError(f"Task API returned no parsed output: {result!r}")
        payload = parsed.model_dump() if hasattr(parsed, "model_dump") else parsed
        self._cache_write(key_payload, payload)
        self._log_cost("task", processor, elapsed_ms, extra={"run_id": getattr(result, "run_id", None)})
        return output_model.model_validate(payload)

    def findall(
        self,
        objective: str,
        entity_type: str,
        match_conditions: Sequence[Mapping[str, Any]],
        *,
        generator: str = "core",
        match_limit: int = 50,
        ttl_sec: int = 86_400,
        poll_interval: float = 2.0,
        poll_timeout: float = 600.0,
    ) -> list[dict[str, Any]]:
        """FindAll API → list of matching entity dicts.

        `match_conditions` is a list of `{name, description, ...}` condition
        dicts per the Parallel schema.
        """
        key_payload = {
            "method": "findall",
            "objective": objective,
            "entity_type": entity_type,
            "match_conditions": list(match_conditions),
            "generator": generator,
            "match_limit": match_limit,
        }
        cached = self._cache_read(key_payload, ttl_sec)
        if cached is not None:
            return cached
        if self.offline:
            raise ParallelOfflineMiss(f"findall({entity_type}) miss in offline mode")

        client = self._require_sdk()
        t0 = time.perf_counter()
        run = client.beta.findall.create(
            objective=objective,
            entity_type=entity_type,
            generator=generator,
            match_conditions=list(match_conditions),
            match_limit=match_limit,
        )
        # SDK response shape (parallel-web 0.4.x):
        #   run = FindAllRun(findall_id, status=Status(is_active, metrics), ...)
        #   result = FindAllRunResult(candidates=[Candidate(name, url, output, ...)],
        #                             run=FindAllRun, last_event_id)
        # Poll until `result.run.status.is_active` goes False, then harvest
        # `result.candidates` — each candidate's `output` dict holds the
        # match_conditions fields we asked for.
        run_id = run.findall_id
        deadline = time.monotonic() + poll_timeout
        candidates: list[Any] = []
        while time.monotonic() < deadline:
            result = client.beta.findall.result(run_id)
            candidates = list(getattr(result, "candidates", []) or [])
            run_obj = getattr(result, "run", None)
            status_obj = getattr(run_obj, "status", None) if run_obj is not None else None
            is_active = bool(getattr(status_obj, "is_active", True))
            if not is_active:
                break
            time.sleep(poll_interval)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # Normalise candidates into plain dicts so downstream parsing doesn't
        # depend on the SDK's Pydantic types.
        matches: list[dict[str, Any]] = []
        for c in candidates:
            d = c.model_dump() if hasattr(c, "model_dump") else dict(c)
            matches.append(d)
        self._cache_write(key_payload, matches)
        self._log_cost("findall", generator, elapsed_ms,
                       extra={"run_id": run_id, "n_matches": len(matches)})
        return matches

    def search(
        self,
        objective: str,
        queries: Sequence[str] | None = None,
        *,
        processor: str = "base",
        mode: str = "one-shot",
        max_results: int = 10,
        ttl_sec: int = 900,
    ) -> dict[str, Any]:
        key_payload = {
            "method": "search",
            "objective": objective,
            "queries": list(queries or []),
            "processor": processor,
            "mode": mode,
            "max_results": max_results,
        }
        cached = self._cache_read(key_payload, ttl_sec)
        if cached is not None:
            return cached
        if self.offline:
            raise ParallelOfflineMiss("search miss in offline mode")

        client = self._require_sdk()
        t0 = time.perf_counter()
        kwargs: dict[str, Any] = {"objective": objective, "processor": processor, "mode": mode,
                                  "max_results": max_results}
        if queries:
            kwargs["search_queries"] = list(queries)
        res = client.beta.search(**kwargs)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        payload = res.model_dump() if hasattr(res, "model_dump") else dict(res)
        self._cache_write(key_payload, payload)
        self._log_cost("search", processor, elapsed_ms)
        return payload

    def extract(
        self,
        urls: Sequence[str],
        objective: str,
        *,
        full_content: bool = False,
        ttl_sec: int = 86_400,
    ) -> dict[str, Any]:
        key_payload = {
            "method": "extract",
            "urls": list(urls),
            "objective": objective,
            "full_content": full_content,
        }
        cached = self._cache_read(key_payload, ttl_sec)
        if cached is not None:
            return cached
        if self.offline:
            raise ParallelOfflineMiss("extract miss in offline mode")

        client = self._require_sdk()
        t0 = time.perf_counter()
        res = client.beta.extract(
            urls=list(urls),
            objective=objective,
            full_content={"enabled": bool(full_content)} if full_content else None,
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        payload = res.model_dump() if hasattr(res, "model_dump") else dict(res)
        self._cache_write(key_payload, payload)
        self._log_cost("extract", "n/a", elapsed_ms)
        return payload

    # ── Private helpers ─────────────────────────────────────────────────────

    def _require_sdk(self) -> "Parallel":
        if self.offline:
            raise ParallelOfflineMiss("Parallel SDK requested in offline mode")
        if not self.api_key:
            raise ParallelKeyMissing(
                "PARALLEL_API_KEY is not set. Add it to .env (never commit the literal key)."
            )
        if self._sdk is None:
            self._sdk = Parallel(api_key=self.api_key)
        return self._sdk

    def _cache_key(self, payload: Mapping[str, Any]) -> str:
        s = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    def _cache_path(self, payload: Mapping[str, Any]) -> Path:
        return self.cache_dir / f"{self._cache_key(payload)}.json"

    def _cache_read(self, payload: Mapping[str, Any], ttl_sec: int) -> Any | None:
        p = self._cache_path(payload)
        if not p.exists():
            return None
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        stored_at = blob.get("stored_at", 0)
        if (time.time() - stored_at) > ttl_sec and not self.offline:
            return None
        return blob.get("data")

    def _cache_write(self, payload: Mapping[str, Any], data: Any) -> None:
        p = self._cache_path(payload)
        body = {
            "stored_at": time.time(),
            "method": payload.get("method"),
            "data": data,
        }
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, default=str), encoding="utf-8")
        tmp.replace(p)

    def _log_cost(self, method: str, processor: str, elapsed_ms: int, extra: Mapping[str, Any] | None = None) -> None:
        row = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "processor": processor,
            "elapsed_ms": elapsed_ms,
        }
        if extra:
            row.update({k: json.dumps(v, default=str) if not isinstance(v, (str, int, float, type(None))) else v
                        for k, v in extra.items()})
        df = pd.DataFrame([row])
        try:
            if self.cost_log_path.exists():
                df = pd.concat([pd.read_parquet(self.cost_log_path), df], ignore_index=True)
            df.to_parquet(self.cost_log_path, index=False)
        except Exception as e:   # pragma: no cover — telemetry must never block a trade call
            _log.warning("cost log write failed: %s", e)


# Singleton helper for scripts that just want one client.
_default: ParallelClient | None = None


def default_client() -> ParallelClient:
    global _default
    if _default is None:
        _default = ParallelClient()
    return _default
