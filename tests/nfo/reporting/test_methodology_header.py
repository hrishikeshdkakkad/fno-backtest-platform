"""Methodology header rendering (master design §8.4)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from nfo.reporting.methodology_header import build_header
from nfo.specs.manifest import RunManifest


def _manifest() -> RunManifest:
    return RunManifest(
        run_id="20260421T143000-capital_analysis-7a3f9b",
        created_at=datetime(2026, 4, 21, 14, 30, 0, tzinfo=timezone.utc),
        code_version="a1b2c3d",
        study_spec_hash="a" * 64,
        strategy_spec_hash="7a3f9b2e1c9d8a6f" + "0" * 48,
        strategy_id="v3",
        strategy_version="3.0.0",
        study_type="capital_analysis",
        selection_mode="cycle_matched",
        dataset_hashes={"ds_features_2024": "4c2d" + "0" * 60},
        window_start=date(2024, 2, 1),
        window_end=date(2026, 4, 18),
        artifacts=["manifest.json", "metrics.json", "report.md"],
        status="ok",
        duration_seconds=12.4,
    )


def test_header_starts_and_ends_with_markers():
    out = build_header(_manifest())
    assert out.startswith("<!-- methodology:begin -->")
    assert out.rstrip().endswith("<!-- methodology:end -->")


def test_header_contains_required_fields():
    out = build_header(_manifest())
    for fragment in [
        "20260421T143000-capital_analysis-7a3f9b",
        "capital_analysis",
        "`v3`",
        "`3.0.0`",
        "cycle_matched",
        "2024-02-01",
        "2026-04-18",
        "a1b2c3d",
    ]:
        assert fragment in out, f"missing {fragment!r}"


def test_header_shows_dirty_code_version():
    m = _manifest()
    m = m.model_copy(update={"code_version": "a1b2c3d-dirty"})
    out = build_header(m)
    assert "a1b2c3d-dirty" in out
    assert "dirty" in out.lower()


def test_header_no_placeholders():
    out = build_header(_manifest())
    for bad in ["TBD", "TODO", "FIXME", "XXX"]:
        assert bad not in out
