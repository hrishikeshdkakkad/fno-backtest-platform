"""Runtime configuration for the NFO backtester — loaded from the project .env.

Timezone convention (project-wide): all dates are anchored in IST (Asia/Kolkata).
Dhan returns epoch-second timestamps which decode to IST when IST tz is applied.
We never round-trip through UTC-naive timestamps — always carry tz or decode
explicitly at the edge.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
# override=True makes project .env the single source of truth — any shell env
# var with the same name is replaced on import. Required so rotated keys in
# .env don't get silently shadowed by stale exports in ~/.zshrc.
load_dotenv(ROOT / ".env", override=True)

DHAN_CLIENT_ID = os.environ["DHAN_CLIENT_ID"]
DHAN_ACCESS_TOKEN = os.environ["DHAN_ACCESS_TOKEN"]
DHAN_BASE_URL = os.environ.get("DHAN_BASE_URL", "https://api.dhan.co/v2")

DATA_DIR = ROOT / "data" / "nfo"
RESULTS_DIR = ROOT / "results" / "nfo"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

IST = "Asia/Kolkata"
RISK_FREE_RATE = 0.065       # RBI repo as of 2026-04
