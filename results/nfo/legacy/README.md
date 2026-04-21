# Legacy narrative reports (pre-platform)

These files predate the NFO research platform and are retained for historical
reference only. They are not regenerated; they do not reflect current engine
semantics; they may disagree with the platform's manifest-backed runs.

## Deprecated

| File | Reason |
|---|---|
| `results/nfo/tier1_report.md` | Pre-V3 signal-grade narrative; use `runs/<run_id>/report.md` from `variant_comparison` or `capital_analysis` instead. |
| `results/nfo/backtest_rerun_plain.md` | Pre-platform plain-language rerun; superseded by `master_summary.md`. |
| `results/nfo/v3_capital_report.md` | Unsuffixed variant; use `v3_capital_report_pt50.md` or `v3_capital_report_hte.md` (or the canonical `runs/<run_id>/` under `capital_analysis`). |
| `results/nfo/v3_capital_trades.csv` | Unsuffixed variant; use variant-suffixed trades or the canonical run tables. |
| `results/nfo/v3_master_analysis.md` | Pre-platform master analysis; superseded by `master_summary.md`. |

## Canonical replacements

- Per-run reports: `results/nfo/runs/<run_id>/report.md`
- Cross-study summary: `results/nfo/master_summary.md`
- Latest-run pointers: `results/nfo/latest.json`
- Full run index: `results/nfo/index.md`

The platform does not delete the listed files automatically. They remain in
place until a future phase archives them under `results/nfo/legacy/archive/`.
