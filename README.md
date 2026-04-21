# NFO Research Platform

Spec-driven research platform for Indian NIFTY / BANKNIFTY F&O credit-spread
strategies. Backed by Dhan v2 for historical and live data.

## What this is

A single system that answers three distinct research questions for the same
strategy spec:

- **day_matched** — "Are trades entered on signal days generally good?"
- **cycle_matched** — "If I force one canonical trade per cycle, how does that
  trade family behave?"
- **live_rule** — "What would a literal live system have done using only the
  information available on that date?"

Every study runs from a validated `StrategySpec` (YAML) and writes a
manifest-backed run directory under `results/nfo/runs/<run_id>/`. Live regime
monitoring consumes the same trigger engine as historical replay, so live and
research cannot silently disagree about the same strategy.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e .

cp .env.example .env            # add DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, PARALLEL_API_KEY
.venv/bin/python -m pytest tests/nfo/ -q
```

## Layout

| Path | Purpose |
|---|---|
| `src/nfo/specs/` | Pydantic models: StrategySpec, StudySpec, RunManifest, DatasetManifest |
| `src/nfo/engine/` | Triggers, cycles, selection, entry, exits, execution, capital, metrics |
| `src/nfo/studies/` | Variant comparison, time split, capital analysis, robustness, falsification, live replay |
| `src/nfo/monitor/` | Live regime snapshots, state machine, research parity |
| `src/nfo/reporting/` | Run directory writer, methodology header, top-level index |
| `src/nfo/datasets/` | Stage pipeline: raw → normalized → features → trade_universe → study_inputs |
| `configs/nfo/strategies/` | Strategy YAMLs (e.g. v3_frozen.yaml) |
| `configs/nfo/studies/` | Study YAMLs |
| `scripts/nfo/` | Thin CLI wrappers (business logic lives in `src/nfo/`) |
| `results/nfo/runs/` | Canonical run outputs |
| `results/nfo/index.md` | Generated index of all runs |
| `data/nfo/` | Cached raw data, dataset parquets, monitor snapshots |
| `legacy/` | Archived CSP backtester (predecessor project) |
| `docs/superpowers/specs/` | Master platform design |
| `docs/superpowers/plans/` | Phase-level implementation plans |

## Status

Phase 1 (Foundation & Contracts) — in progress. See
`docs/superpowers/plans/2026-04-21-nfo-platform-phase1-plan.md`.

## Prior CSP work

The original cash-secured-put backtester for US equities (Massive.com data) is
archived under `legacy/csp/` with its own quick-start. It is not part of the
NFO platform.
