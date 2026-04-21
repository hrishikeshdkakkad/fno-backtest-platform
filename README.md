# CSP Income Backtester

A cash-secured-put backtester for a $41k portfolio targeting $500/month in
option premium, powered by Massive.com (ex-Polygon.io) historical daily bars.

## Why this exists

Running CSPs as an income strategy sounds simple — sell OTM puts, collect
premium, either keep the cash or get assigned a stock you wanted anyway.
In practice there are a dozen knobs: which underlyings, what delta, what
DTE, when (if ever) to close early, what to do on assignment. This repo
turns those knobs into numbers so the live playbook is grounded in
out-of-sample history, not vibes.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e .

cp .env.example .env            # add your Massive API key
.venv/bin/python scripts/run_grid.py   # ~60-90 min on Basic tier
.venv/bin/python scripts/build_plan.py
open results/plan.md
```

## What lives where

| Path | Purpose |
|---|---|
| `src/csp/client.py` | Rate-limited HTTP client for the Massive REST API |
| `src/csp/cache.py` | Parquet-backed cache of stock/option bars |
| `src/csp/bsm.py` | Black-Scholes pricing + implied-vol / delta |
| `src/csp/universe.py` | Monthly expirations + OCC ticker construction |
| `src/csp/strategy.py` | Analytical target-strike picker |
| `src/csp/backtest.py` | Monthly-roll CSP simulator with profit-take / stop / manage rules |
| `src/csp/portfolio.py` | Capital allocation across multiple per-contract strategies |
| `scripts/run_grid.py` | Runs the full parameter grid and writes `results/summary.csv` |
| `scripts/build_plan.py` | Reads the summary, picks the best allocation, writes `results/plan.md` |
| `scripts/quick_iwm.py` | 6-month sanity check on IWM alone |

## Tier notes (Massive.com Basic = 5 calls/min)

- Daily option bars are available back to ~2 years.
- NBBO quotes are **not** available — fills use daily close with symmetric
  slippage (default 2%).
- Historical greeks are **not** available — we compute delta/IV from the
  put's close price via Black-Scholes each day.
- Option tickers are constructed deterministically
  (`O:<UND><YYMMDD><C|P><strike*1000:08d>`); invalid strikes return empty
  bars rather than 404, which lets us skip chain-reference calls entirely
  for major ETFs with $1 strike increments.

## Strategy defaults (override in `scripts/run_grid.py`)

- Target delta 0.20–0.30
- Target DTE 35 (enter 35 calendar days before 3rd-Friday monthly expiry)
- Profit take at 50%
- Manage (close) at 21 DTE if still open
- Slippage 2% of option close, applied to both entry and exit
