"""Tests for canonical JSON + spec hashing (master design §4.2)."""
from __future__ import annotations

from pydantic import BaseModel

from nfo.specs.hashing import canonical_json, spec_hash, short_hash


class _Toy(BaseModel):
    b: int
    a: str


def test_canonical_json_sorts_keys():
    m = _Toy(a="x", b=1)
    out = canonical_json(m)
    assert out == b'{"a":"x","b":1}'


def test_canonical_json_no_whitespace():
    m = _Toy(a="x", b=1)
    out = canonical_json(m)
    assert b" " not in out
    assert b"\n" not in out


def test_spec_hash_is_hex_sha256():
    m = _Toy(a="x", b=1)
    h = spec_hash(m)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_spec_hash_stable_across_field_order():
    m1 = _Toy(a="x", b=1)
    m2 = _Toy.model_validate({"b": 1, "a": "x"})
    assert spec_hash(m1) == spec_hash(m2)


def test_spec_hash_changes_on_value_change():
    assert spec_hash(_Toy(a="x", b=1)) != spec_hash(_Toy(a="x", b=2))


def test_short_hash_is_6_chars():
    h = short_hash(_Toy(a="x", b=1))
    assert len(h) == 6
    assert h == spec_hash(_Toy(a="x", b=1))[:6]
