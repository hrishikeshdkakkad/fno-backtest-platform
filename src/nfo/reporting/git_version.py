"""Read `git` state for RunManifest.code_version (master design §2.3)."""
from __future__ import annotations

import subprocess
from pathlib import Path


def current_code_version(*, repo_root: Path) -> str:
    if not (repo_root / ".git").exists():
        return "unversioned"
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return "unversioned"
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return f"{sha}-dirty" if dirty else sha
