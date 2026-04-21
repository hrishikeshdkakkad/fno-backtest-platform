"""Per-day JSONL snapshot store (master design §9.1).

Layout:  <root>/<YYYY-MM-DD>.jsonl   one snapshot per line.
Append-only; files never rewritten. Date is determined from
snapshot.timestamp.date().
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from nfo.monitor.snapshot import MonitorSnapshot


def append_snapshot(snapshot: MonitorSnapshot, *, root: Path) -> Path:
    """Append `snapshot` to <root>/<YYYY-MM-DD>.jsonl.

    Uses ``snapshot.timestamp.date()`` to determine the target file. Returns
    the path written. Creates parent dirs lazily.
    """
    root.mkdir(parents=True, exist_ok=True)
    fname = f"{snapshot.timestamp.date().isoformat()}.jsonl"
    fpath = root / fname
    with fpath.open("a", encoding="utf-8") as f:
        f.write(snapshot.model_dump_json() + "\n")
    return fpath


def load_snapshots(
    *,
    root: Path,
    start: date | None = None,
    end: date | None = None,
) -> list[MonitorSnapshot]:
    """Load snapshots from all <YYYY-MM-DD>.jsonl files under ``root`` whose
    date is in [start, end] (both inclusive; either None = open-ended)."""
    if not root.exists():
        return []
    out: list[MonitorSnapshot] = []
    for jsonl in sorted(root.glob("*.jsonl")):
        try:
            file_date = date.fromisoformat(jsonl.stem)
        except ValueError:
            continue
        if start is not None and file_date < start:
            continue
        if end is not None and file_date > end:
            continue
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            out.append(MonitorSnapshot.model_validate_json(line))
    return out
