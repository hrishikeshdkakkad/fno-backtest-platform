# Legacy archive

Code archived from the predecessor CSP backtester (US-equity cash-secured puts
on Massive.com data). Retained for reference; not installed or maintained.

## Contents

- `csp/` — Python package (`src/csp/` in the pre-refactor tree).
- `scripts_csp/` — CSP-era CLI scripts (`scripts/*.py` except `scripts/nfo/*`).
- `tests/test_spread.py` — legacy spread-backtest test.

## Running legacy code

The archived code is not part of the installable `nfo-platform` package. To run
it, add `legacy/` to `PYTHONPATH`:

```bash
PYTHONPATH=legacy .venv/bin/python -m csp.backtest  # example
```

The NFO research platform does not depend on anything here.
