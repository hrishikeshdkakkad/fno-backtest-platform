"""CLI: regenerate results/nfo/index.md + latest.json."""
from __future__ import annotations

import argparse
from pathlib import Path

from nfo.config import RESULTS_DIR, ROOT
from nfo.reporting.hash_sources import filesystem_hash_sources
from nfo.reporting.index import generate_index
from nfo.reporting.master_summary import generate_master_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m nfo.reporting",
        description="Regenerate NFO platform run index (results/nfo/index.md + latest.json).",
    )
    parser.add_argument("--runs-root", type=Path, default=RESULTS_DIR / "runs")
    parser.add_argument("--out-root", type=Path, default=RESULTS_DIR)
    parser.add_argument("--strategies-root", type=Path, default=ROOT / "configs" / "nfo" / "strategies")
    parser.add_argument("--datasets-root", type=Path, default=ROOT / "data" / "nfo" / "datasets")
    args = parser.parse_args(argv)

    sources = filesystem_hash_sources(
        strategies_root=args.strategies_root,
        datasets_root=args.datasets_root,
    )
    args.out_root.mkdir(parents=True, exist_ok=True)
    res = generate_index(runs_root=args.runs_root, out_root=args.out_root, sources=sources)
    print(f"Indexed {res.total_runs} runs ({res.stale_runs} stale).")
    print(f"  by_study={res.by_study}")
    print(f"Wrote {args.out_root / 'index.md'}")
    print(f"Wrote {args.out_root / 'latest.json'}")
    ms = generate_master_summary(
        runs_root=args.runs_root,
        out_path=args.out_root / "master_summary.md",
    )
    print(f"Wrote {ms.out_path} (latest per study: {ms.latest_per_study})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
