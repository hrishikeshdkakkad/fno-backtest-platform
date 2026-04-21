"""ParallelClient wrapper — cache, offline, key-missing paths.

Network calls are stubbed at the SDK boundary (`Parallel` class) so these
tests run offline and deterministic.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from nfo import parallel_client as pc


class _Out(BaseModel):
    foo: str


@pytest.fixture
def tmp_cache(tmp_path: Path) -> Path:
    return tmp_path / "cache"


def _make_client(tmp_cache: Path, *, offline: bool = False, api_key: str | None = "test-key") -> pc.ParallelClient:
    return pc.ParallelClient(
        api_key=api_key,
        cache_dir=tmp_cache,
        cost_log_path=tmp_cache.parent / "cost.parquet",
        offline=offline,
    )


def test_key_missing_raises_on_network(tmp_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing API key should raise ParallelKeyMissing when we actually try to call."""
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    client = _make_client(tmp_cache, api_key=None)
    with pytest.raises(pc.ParallelKeyMissing):
        client._require_sdk()


def test_offline_mode_refuses_with_no_cache(tmp_cache: Path) -> None:
    client = _make_client(tmp_cache, offline=True)
    with pytest.raises(pc.ParallelOfflineMiss):
        client.task("hello", output_model=_Out)


def test_cache_hit_skips_network(tmp_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_cache)

    # Pre-populate the cache with a valid response for this exact call.
    key_payload = {
        "method": "task",
        "input": "hello",
        "output_model": _Out.model_json_schema(),
        "processor": "core",
    }
    client._cache_write(key_payload, {"foo": "cached"})

    # If the SDK is touched, the test fails loudly.
    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("SDK was called — cache should have served this")
    monkeypatch.setattr(client, "_require_sdk", boom)

    got = client.task("hello", output_model=_Out)
    assert got.foo == "cached"


def test_cache_miss_calls_sdk_and_stores(tmp_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_cache)

    sdk_mock = MagicMock()
    # execute(...) returns an object with output.parsed
    parsed_instance = _Out(foo="fresh")
    sdk_mock.task_run.execute.return_value = MagicMock(
        output=MagicMock(parsed=parsed_instance),
        run_id="run-123",
    )
    monkeypatch.setattr(client, "_require_sdk", lambda: sdk_mock)

    got = client.task("hello", output_model=_Out)
    assert got.foo == "fresh"
    sdk_mock.task_run.execute.assert_called_once()

    # A second call with the same args should NOT hit the SDK again.
    sdk_mock.task_run.execute.reset_mock()
    got2 = client.task("hello", output_model=_Out)
    assert got2.foo == "fresh"
    sdk_mock.task_run.execute.assert_not_called()


def test_cache_ttl_honoured(tmp_cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(tmp_cache)
    key_payload = {"method": "search", "objective": "x", "queries": [], "processor": "base",
                   "mode": "one-shot", "max_results": 10}

    # Write a blob with stored_at far in the past to simulate expiration.
    client._cache_write(key_payload, {"old": True})
    cache_file = client._cache_path(key_payload)
    raw = json.loads(cache_file.read_text())
    raw["stored_at"] = 0   # epoch 1970
    cache_file.write_text(json.dumps(raw))

    sdk_mock = MagicMock()
    sdk_mock.beta.search.return_value = MagicMock(model_dump=lambda: {"fresh": True})
    monkeypatch.setattr(client, "_require_sdk", lambda: sdk_mock)

    got = client.search("x", ttl_sec=60)   # blob is decades old → miss
    assert got == {"fresh": True}
    sdk_mock.beta.search.assert_called_once()


def test_cache_key_sorts_stably(tmp_cache: Path) -> None:
    """Same payload, different dict order → same cache hash."""
    client = _make_client(tmp_cache)
    h1 = client._cache_key({"a": 1, "b": [1, 2], "c": "x"})
    h2 = client._cache_key({"c": "x", "b": [1, 2], "a": 1})
    assert h1 == h2
