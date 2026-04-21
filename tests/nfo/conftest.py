"""Make script modules under `scripts/nfo/` importable from tests.

`regime_watch.py` and `tune_thresholds.py` live outside `src/`, so pytest
can't import them by name unless we extend sys.path. This conftest runs
once per test session, before any `test_*` module collects.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "nfo"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
