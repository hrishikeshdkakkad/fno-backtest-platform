# NFO Research Platform

Spec-driven research platform for Indian NIFTY / BANKNIFTY F&O credit-spread
strategies. It uses one shared engine for historical research, deployment
studies, and live regime monitoring so those paths cannot silently drift apart.

## What This Platform Answers

The same strategy can be evaluated in three distinct ways:

- `day_matched`: "Are trades entered on signal days generally good?"
- `cycle_matched`: "If I force one canonical trade per cycle, how does that
  trade family behave?"
- `live_rule`: "What would a literal live system have done using only the
  information available on that date?"

The strategy definition lives in YAML, the study definition lives in YAML, and
every run is written to a manifest-backed directory under `results/nfo/runs/`.

## Core Ideas

- `configs/nfo/strategies/` contains validated strategy specs. For example,
  `v3_frozen.yaml` is the canonical cycle-matched V3 spec, while
  `v3_live_rule.yaml` is the live-rule variant of the same strategy.
- `configs/nfo/studies/` contains study specs such as time split, capital
  analysis, robustness, falsification, and live replay.
- `src/nfo/engine/` owns triggers, cycle grouping, selection, entry, exit,
  execution, capital, and metrics behavior.
- `src/nfo/studies/` composes the engine into research jobs.
- `scripts/nfo/` are CLI entry points; core business logic lives in `src/nfo/`.
- `results/nfo/runs/<run_id>/` is the canonical output surface.

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install -e .

cp .env.example .env
# set:
#   DHAN_CLIENT_ID
#   DHAN_ACCESS_TOKEN
# optional:
#   PARALLEL_API_KEY

.venv/bin/python -m pytest tests/nfo/ -q
```

Runtime configuration is loaded from `.env` via `src/nfo/config.py`.

## Typical Workflow

### 1. Refresh market and enrichment caches

```bash
.venv/bin/python scripts/nfo/refresh_vix_cache.py
.venv/bin/python scripts/nfo/refresh_events.py
```

Use `refresh_events.py --dry-run` if you want cache-only behavior without new
Parallel calls.

### 2. Build historical research inputs

```bash
.venv/bin/python scripts/nfo/historical_backtest.py
.venv/bin/python scripts/nfo/v3_fill_gaps.py
.venv/bin/python scripts/nfo/p6_seed_datasets.py
```

These commands produce the key inputs that downstream studies consume:

- `results/nfo/historical_signals.parquet`
- `results/nfo/spread_trades.csv`
- `results/nfo/spread_trades_v3_gaps.csv`
- `data/nfo/datasets/features/...`
- `data/nfo/datasets/trade_universe/...`

### 3. Run the research study that matches your question

```bash
.venv/bin/python scripts/nfo/redesign_variants.py
.venv/bin/python scripts/nfo/time_split_validate.py
.venv/bin/python scripts/nfo/v3_capital_analysis.py
.venv/bin/python scripts/nfo/v3_robustness.py
.venv/bin/python scripts/nfo/v3_falsification.py
.venv/bin/python scripts/nfo/v3_live_rule_backtest.py
```

Use each script for a different question:

- `redesign_variants.py`: compare candidate filters and pick a winner.
- `time_split_validate.py`: check whether V3 generalizes out of sample.
- `v3_capital_analysis.py`: translate matched trades into capital deployment
  and equity curves.
- `v3_robustness.py`: stress slippage, leave-one-out sensitivity, and
  bootstrap behavior.
- `v3_falsification.py`: run realism tests such as tail-loss injection,
  allocation sweep, walk-forward, and perturbation summaries.
- `v3_live_rule_backtest.py`: replay the strategy under literal live-entry
  rules instead of cycle-matched lookback selection.

### 4. Run the live monitor

```bash
.venv/bin/python scripts/nfo/regime_watch.py
.venv/bin/python scripts/nfo/regime_watch.py --history
.venv/bin/python scripts/nfo/regime_watch.py --loop 30
.venv/bin/python scripts/nfo/regime_watch.py --tui
```

`regime_watch.py` is the operational surface for current-market monitoring.

### 5. Rebuild reporting views

```bash
.venv/bin/python -m nfo.reporting
```

This regenerates:

- `results/nfo/index.md`
- `results/nfo/latest.json`
- `results/nfo/master_summary.md`

## How To Read Results

Every canonical run directory under `results/nfo/runs/<run_id>/` is expected to
contain:

- `manifest.json`: strategy version, study type, selection mode, code version,
  dataset hashes, window, warnings, and artifact list.
- `metrics.json`: machine-readable headline metrics for the run.
- `report.md`: human-readable report with a methodology header.
- `tables/`: mirrored CSV or parquet artifacts when the study emits them.

The run manifest schema is defined in `src/nfo/specs/manifest.py`.

Top-level files under `results/nfo/` are still useful, but they are not the
authoritative provenance surface. The run directories are the source of truth.

## Provenance Rules

If you want reproducible public results, follow these rules:

- Treat `results/nfo/runs/` as canonical. Use `index.md`, `latest.json`, and
  `master_summary.md` as helper views only.
- Prefer changing strategy or study YAMLs over piling on ad hoc CLI overrides.
  Specs are what make runs inspectable and stale-checkable.
- Use the default study settings when you want the canonical public run. If you
  pass custom CLI overrides for exploratory analysis, treat that output as
  exploratory unless you also promote those parameters into a study spec.
- When a study emits multiple variants under the same study type, inspect the
  actual run directories instead of relying only on `latest.json`, which groups
  by `study_type`.

## Strategy Specs In This Repo

- `configs/nfo/strategies/v3_frozen.yaml`: canonical V3, `cycle_matched`,
  preferred exit variant `hte`, fixed 0.30 delta / 100-point width / 35 DTE.
- `configs/nfo/strategies/v3_live_rule.yaml`: V3 under `live_rule`, with entry
  forced on or after first fire.
- `configs/nfo/strategies/v3_live_rule_pt50.yaml`: live-rule PT50 variant.

Study YAMLs under `configs/nfo/studies/` define the canonical parameters for
variant comparison, time split, capital analysis, robustness, falsification,
and live replay.

## Public-Facing Mental Model

The clean way to use the platform is:

1. Refresh caches and rebuild datasets.
2. Choose a strategy spec.
3. Choose the study that matches the question you care about.
4. Run the CLI wrapper.
5. Read the run directory first, then the top-level summaries.

That gives you a stable path from idea to evidence:

- filter design -> `redesign_variants.py`
- generalization -> `time_split_validate.py`
- position sizing -> `v3_capital_analysis.py`
- fragility -> `v3_robustness.py`
- realism checks -> `v3_falsification.py`
- literal live behavior -> `v3_live_rule_backtest.py`
- today's regime state -> `regime_watch.py`

## Repository Layout

| Path | Purpose |
|---|---|
| `src/nfo/specs/` | Pydantic models: strategy, study, run manifest, dataset manifest |
| `src/nfo/engine/` | Triggers, cycles, selection, entry, exits, execution, capital, metrics |
| `src/nfo/studies/` | Variant comparison, time split, capital analysis, robustness, falsification, live replay |
| `src/nfo/monitor/` | Live regime snapshots, state machine, research parity |
| `src/nfo/reporting/` | Run directory writer, methodology header, index, summary generation |
| `src/nfo/datasets/` | Dataset ingestion and staleness tracking |
| `configs/nfo/strategies/` | Strategy YAMLs |
| `configs/nfo/studies/` | Study YAMLs |
| `scripts/nfo/` | Thin CLI wrappers |
| `results/nfo/runs/` | Canonical run outputs |
| `data/nfo/` | Cached raw data, dataset parquets, monitor snapshots |
| `docs/superpowers/specs/` | Master design spec |

## Additional References

- `docs/superpowers/specs/2026-04-21-nfo-research-platform-design.md`
- `docs/v3-spec-frozen.md`
- `docs/india-fno-nuances.md`

## Scope

The original US-equities CSP backtester is archived under `legacy/csp/`. It is
not part of this NFO platform.
