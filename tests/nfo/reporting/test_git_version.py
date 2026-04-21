"""Tests for the code_version git helper (master design §2.3)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from nfo.reporting.git_version import current_code_version


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _init_repo(root: Path) -> None:
    _run(["git", "init", "-q"], root)
    _run(["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit",
          "--allow-empty", "-m", "init", "-q"], root)


def test_current_code_version_is_short_sha(tmp_path):
    _init_repo(tmp_path)
    ver = current_code_version(repo_root=tmp_path)
    assert len(ver) >= 7
    assert not ver.endswith("-dirty")


def test_current_code_version_marks_dirty(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "file.txt").write_text("hi")
    _run(["git", "add", "file.txt"], tmp_path)
    ver = current_code_version(repo_root=tmp_path)
    assert ver.endswith("-dirty")


def test_current_code_version_fallback_when_not_git(tmp_path):
    ver = current_code_version(repo_root=tmp_path)
    assert ver == "unversioned"
