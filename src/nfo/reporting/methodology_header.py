"""Methodology header block (master design §8.4)."""
from __future__ import annotations

from nfo.specs.manifest import RunManifest


BEGIN_MARKER = "<!-- methodology:begin -->"
END_MARKER = "<!-- methodology:end -->"


def build_header(manifest: RunManifest) -> str:
    lines: list[str] = [BEGIN_MARKER, "## Methodology"]
    lines.append(f"- **Run ID:** `{manifest.run_id}`")
    lines.append(f"- **Study type:** {manifest.study_type}")
    lines.append(
        f"- **Strategy:** `{manifest.strategy_id}` version `{manifest.strategy_version}` "
        f"(hash `{manifest.strategy_spec_hash[:16]}`)"
    )
    lines.append(f"- **Selection mode:** {manifest.selection_mode}")
    lines.append(f"- **Date window:** {manifest.window_start.isoformat()} -> {manifest.window_end.isoformat()}")
    if manifest.dataset_hashes:
        lines.append("- **Datasets:**")
        for dsid in sorted(manifest.dataset_hashes):
            h = manifest.dataset_hashes[dsid]
            lines.append(f"  - `{dsid}` (sha256 `{h[:12]}`)")
    clean = "clean" if not manifest.code_version.endswith("-dirty") else "dirty"
    lines.append(f"- **Code version:** `{manifest.code_version}` ({clean})")
    lines.append(f"- **Created:** {manifest.created_at.isoformat()}")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"
