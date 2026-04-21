"""Package identity test — asserts pyproject declares nfo-platform."""
from __future__ import annotations

import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pyproject_name_is_nfo_platform():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert data["project"]["name"] == "nfo-platform"


def test_pyproject_description_mentions_nfo():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    desc = data["project"]["description"].lower()
    assert "nfo" in desc or "nifty" in desc
