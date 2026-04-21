"""Run directory writer (master design §8.1)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from nfo.reporting.methodology_header import BEGIN_MARKER, build_header
from nfo.specs.manifest import RunManifest


@dataclass
class RunDirectory:
    path: Path
    _manifest: RunManifest | None = None

    def _bind_manifest(self, manifest: RunManifest) -> None:
        self._manifest = manifest

    def _require_manifest(self) -> RunManifest:
        if self._manifest is None:
            raise RuntimeError("write_manifest must be called before other writers")
        return self._manifest

    def _record_artifact(self, relpath: str) -> None:
        m = self._require_manifest()
        if relpath not in m.artifacts:
            m.artifacts.append(relpath)
            (self.path / "manifest.json").write_text(m.model_dump_json(indent=2))

    def write_manifest(self, manifest: RunManifest) -> None:
        self._bind_manifest(manifest)
        if "manifest.json" not in manifest.artifacts:
            manifest.artifacts.append("manifest.json")
        (self.path / "manifest.json").write_text(manifest.model_dump_json(indent=2))

    def write_metrics(self, metrics: dict[str, Any]) -> None:
        import json as _json
        (self.path / "metrics.json").write_text(_json.dumps(metrics, indent=2, sort_keys=True))
        self._record_artifact("metrics.json")

    def write_table(self, name: str, df: pd.DataFrame, *, fmt: Literal["csv", "parquet"] = "csv") -> None:
        tables_dir = self.path / "tables"
        tables_dir.mkdir(exist_ok=True)
        if fmt == "csv":
            rel = f"tables/{name}.csv"
            df.to_csv(self.path / rel, index=False)
        elif fmt == "parquet":
            rel = f"tables/{name}.parquet"
            df.to_parquet(self.path / rel, index=False)
        else:
            raise ValueError(f"unsupported fmt {fmt!r}")
        self._record_artifact(rel)

    def write_report(self, *, body_markdown: str) -> None:
        m = self._require_manifest()
        if BEGIN_MARKER in body_markdown:
            raise ValueError(
                f"body_markdown must not contain {BEGIN_MARKER!r}; "
                f"the header is rendered by RunDirectory.write_report"
            )
        content = build_header(m) + "\n" + body_markdown.lstrip()
        if not content.endswith("\n"):
            content += "\n"
        (self.path / "report.md").write_text(content)
        self._record_artifact("report.md")

    def write_log(self, name: str, text: str) -> None:
        logs_dir = self.path / "logs"
        logs_dir.mkdir(exist_ok=True)
        rel = f"logs/{name}"
        (self.path / rel).write_text(text)
        self._record_artifact(rel)


def open_run_directory(*, root: Path, run_id: str) -> RunDirectory:
    rundir = Path(root) / run_id
    (rundir / "tables").mkdir(parents=True, exist_ok=True)
    (rundir / "logs").mkdir(parents=True, exist_ok=True)
    return RunDirectory(path=rundir)
