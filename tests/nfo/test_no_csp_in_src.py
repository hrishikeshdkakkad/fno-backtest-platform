"""Assert that CSP code has been archived out of the installable package."""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_no_src_csp_directory():
    assert not (REPO_ROOT / "src" / "csp").exists(), \
        "src/csp/ must be archived to legacy/csp/"


def test_legacy_csp_exists():
    assert (REPO_ROOT / "legacy" / "csp" / "__init__.py").exists(), \
        "legacy/csp/ must contain the archived package"


def test_legacy_has_readme():
    assert (REPO_ROOT / "legacy" / "README.md").exists()
