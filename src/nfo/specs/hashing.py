"""Canonical JSON serialization + spec hashing (master design §4.2).

Contract:
  canonical_json(model) -> bytes — sorted keys, no whitespace, JSON mode.
  spec_hash(model) -> str — hex-encoded SHA-256 of canonical_json.
  short_hash(model) -> str — first 6 chars of spec_hash.
"""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel


def canonical_json(model: BaseModel) -> bytes:
    payload = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def spec_hash(model: BaseModel) -> str:
    return hashlib.sha256(canonical_json(model)).hexdigest()


def short_hash(model: BaseModel, length: int = 6) -> str:
    return spec_hash(model)[:length]
