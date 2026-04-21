"""Parity: post-P5-C2 _legacy_main regenerates time_split_report.md consistently.

This is a regression test: run the refactored `_legacy_main`, parse the
regenerated `results/nfo/time_split_report.md`, and compare the key numeric
columns (fires, fires/yr, trades, win%, sharpe, max-loss%) to the committed
file. The report is human-readable Markdown; we match the per-variant tables
row-by-row within 1e-6 relative tolerance on the numerics and byte-exact on
the integer columns.
"""
from __future__ import annotations

import importlib.util
import math
import re
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_PATH = REPO_ROOT / "results" / "nfo" / "time_split_report.md"


@pytest.fixture(autouse=True)
def _restore_real_registry(monkeypatch):
    from nfo.specs import loader
    monkeypatch.setattr(
        loader, "_REGISTRY_PATH",
        REPO_ROOT / "configs" / "nfo" / ".registry.json",
        raising=True,
    )


def _load_script():
    path = REPO_ROOT / "scripts" / "nfo" / "time_split_validate.py"
    spec = importlib.util.spec_from_file_location("_tsv_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_tsv_test"] = mod
    spec.loader.exec_module(mod)
    return mod


_VARIANT_HEADER_RE = re.compile(r"^## (V\d) — ")
_ROW_RE = re.compile(
    r"^\| (Full|Train|Test) \| (\d+) \| ([\d.]+) \| (\d+) \| "
    r"(\d+)% \| ([+-][\d.]+|nan) \| ([\d.]+)% \|"
)


def _parse_report(text: str) -> dict[str, dict[str, dict[str, float]]]:
    """Parse the per-variant/per-split table. Returns
    {variant: {split: {fires, fires_yr, trades, win_pct, sharpe, max_loss_pct}}}."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    current_variant: str | None = None
    for line in text.splitlines():
        m_h = _VARIANT_HEADER_RE.match(line)
        if m_h:
            current_variant = m_h.group(1)
            out[current_variant] = {}
            continue
        m_r = _ROW_RE.match(line)
        if m_r and current_variant:
            split = m_r.group(1)
            sharpe_str = m_r.group(6)
            try:
                sharpe = float(sharpe_str)
            except ValueError:
                sharpe = float("nan")
            out[current_variant][split] = {
                "fires": float(m_r.group(2)),
                "fires_yr": float(m_r.group(3)),
                "trades": float(m_r.group(4)),
                "win_pct": float(m_r.group(5)),
                "sharpe": sharpe,
                "max_loss_pct": float(m_r.group(7)),
            }
    return out


@pytest.mark.skipif(
    not REPORT_PATH.exists(),
    reason="requires committed legacy time_split_report.md",
)
def test_legacy_main_regenerates_report_consistently():
    committed_text = REPORT_PATH.read_text(encoding="utf-8")
    committed = _parse_report(committed_text)
    assert committed, "failed to parse committed report"

    mod = _load_script()
    result = mod._legacy_main([])
    assert isinstance(result, dict)
    assert "metrics" in result

    regen_text = REPORT_PATH.read_text(encoding="utf-8")
    regen = _parse_report(regen_text)
    assert regen, "failed to parse regenerated report"

    # Same set of variants.
    assert set(regen.keys()) == set(committed.keys()), (
        f"variant set drift: regen={set(regen)} committed={set(committed)}"
    )

    for variant, splits in committed.items():
        assert set(regen[variant].keys()) == set(splits.keys()), (
            f"{variant}: split set drift"
        )
        for split, metrics in splits.items():
            r_metrics = regen[variant][split]
            # Integer columns: byte-exact.
            for k in ("fires", "trades"):
                assert r_metrics[k] == metrics[k], (
                    f"{variant}/{split} {k} drift: regen={r_metrics[k]} "
                    f"committed={metrics[k]}"
                )
            # Numeric: within 1e-6 relative / abs tolerance. Note the legacy
            # report rounds fires/yr to 2dp and sharpe to 2dp and win_pct
            # to 0dp and max_loss_pct to 1dp before writing, so we compare
            # the rounded values and allow a small epsilon.
            for k in ("fires_yr", "win_pct", "max_loss_pct"):
                a = r_metrics[k]
                b = metrics[k]
                if math.isnan(a) and math.isnan(b):
                    continue
                assert math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6), (
                    f"{variant}/{split} {k} drift: regen={a} committed={b}"
                )
            # Sharpe rounded to 2dp by legacy formatter; compare at printed
            # precision.
            s_regen = r_metrics["sharpe"]
            s_cmt = metrics["sharpe"]
            if math.isnan(s_regen) and math.isnan(s_cmt):
                continue
            assert math.isclose(s_regen, s_cmt, rel_tol=1e-6, abs_tol=1e-6), (
                f"{variant}/{split} sharpe drift: regen={s_regen} committed={s_cmt}"
            )
