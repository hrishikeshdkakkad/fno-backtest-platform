"""Engine: trigger evaluation (master design §12 acceptance item 3).

Single source of truth for whether a strategy fires on a given day. Replaces
`scripts/nfo/redesign_variants._row_passes` + `get_firing_dates` for any spec
whose trigger_rule.feature_thresholds declares the V3-family gates. Legacy
variants (V0-V2, V4-V6) remain in redesign_variants until P2b.

Semantics (matches V3 legacy at V3's thresholds):
- s3_iv_rv  : iv_minus_rv >= threshold "iv_minus_rv_min_vp"
- s6_trend  : trend_score  >= threshold "trend_score_min"
- s8_events : event_risk_v3 != "high" OR event occurs outside window_days
- Optional vol signals (any pass satisfies the specific-pass "vol_ok" branch):
  s1_vix_abs  : vix > threshold "vix_abs_min"
  s2_vix_pct  : vix_pct_3mo >= threshold "vix_pct_3mo_min"
  s5_iv_rank  : iv_rank_12mo >= threshold "iv_rank_min"
- When trigger_rule.specific_pass_gates lists s3/s6/s8, require all core AND >=1 vol.
- score_gates.min_score (if set) is applied as a final floor.

Event-resolution flexibility (master design §7.3):
- By default, _event_pass reads the parquet's `event_risk_v3` (or `event_risk`)
  column and treats "high" as fail. The feature dataset is the single source of
  truth for event risk.
- For specs whose feature dataset has NOT yet computed the event-risk column
  under the spec's semantics (e.g. V3 against the P1 cached parquet, which
  baked in V0 semantics), callers may pass `event_resolver=` to the evaluator.
  The resolver is a callable `(entry_date, dte) -> str` returning a severity
  string ("high" or otherwise) that replaces the column lookup. This keeps the
  engine as the sole gate-logic authority while allowing data-layer flexibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

import numpy as np
import pandas as pd

from nfo.specs.strategy import StrategySpec


@dataclass
class FireRow:
    fired: bool
    detail: dict[str, Any]


CORE_GATES = ("s3_iv_rv", "s6_trend", "s8_events")
VOL_GATES = ("s1_vix_abs", "s2_vix_pct", "s5_iv_rank")


EventResolver = Callable[[date, int], str]


class TriggerEvaluator:
    """Evaluate trigger_rule against a features frame."""

    def __init__(
        self,
        spec: StrategySpec,
        *,
        event_resolver: EventResolver | None = None,
    ) -> None:
        self.spec = spec
        self.thresholds = spec.trigger_rule.feature_thresholds
        self.specific = set(spec.trigger_rule.specific_pass_gates)
        self.min_score = int(spec.trigger_rule.score_gates.get("min_score", 0))
        self.window_days = spec.trigger_rule.event_window_days
        self.event_resolver = event_resolver

    def evaluate_row(self, row: pd.Series, *, atr_value: float = float("nan")) -> FireRow:
        t = self.thresholds
        vix = float(row.get("vix", np.nan))
        vpct = float(row.get("vix_pct_3mo", np.nan))
        iv_rv = float(row.get("iv_minus_rv", np.nan))
        ivr = float(row.get("iv_rank_12mo", np.nan))
        trend_raw = row.get("trend_score", 0)
        trend = float(trend_raw) if trend_raw is not None and pd.notna(trend_raw) else 0.0
        event_risk = row.get("event_risk_v3", row.get("event_risk", "none"))

        s1 = bool(np.isfinite(vix) and vix > t.get("vix_abs_min", float("inf")))
        s2 = bool(np.isfinite(vpct) and vpct >= t.get("vix_pct_3mo_min", float("inf")))
        s3 = bool(np.isfinite(iv_rv) and iv_rv >= t.get("iv_minus_rv_min_vp", float("inf")))
        s5 = bool(np.isfinite(ivr) and ivr >= t.get("iv_rank_min", float("inf")))
        s6 = bool(trend >= t.get("trend_score_min", float("inf")))
        s8 = self._event_pass(row=row, event_risk=event_risk)

        passes = {"s1": s1, "s2": s2, "s3": s3,
                  "s5": s5, "s6": s6, "s8": s8}
        score = sum(1 for v in passes.values() if v)

        if self.specific:
            core_ok = all(passes[g[:2]] for g in CORE_GATES if g in self.specific)
            vol_ok = any(passes[g[:2]] for g in VOL_GATES)
            if not (core_ok and vol_ok):
                return FireRow(False, {"score": score, **passes})

        return FireRow(score >= self.min_score, {"score": score, **passes})

    def fire_dates(
        self, features_df: pd.DataFrame, atr_series: pd.Series
    ) -> list[tuple[date, dict]]:
        atr_by_date: dict[date, float] = {}
        for d, v in atr_series.items():
            atr_by_date[pd.Timestamp(d).date()] = float(v) if np.isfinite(v) else float("nan")
        fires: list[tuple[date, dict]] = []
        for _, row in features_df.iterrows():
            entry = row["date"]
            if isinstance(entry, pd.Timestamp):
                entry = entry.date()
            atr_val = atr_by_date.get(entry, float("nan"))
            result = self.evaluate_row(row, atr_value=atr_val)
            if result.fired:
                fires.append((entry, result.detail))
        return fires

    def _event_pass(self, *, row: pd.Series, event_risk: Any) -> bool:
        """Return True if the event gate passes (no blocking event)."""
        if self.event_resolver is not None:
            entry = row.get("date")
            if isinstance(entry, pd.Timestamp):
                entry = entry.date()
            dte_raw = row.get("dte")
            try:
                dte = int(dte_raw) if dte_raw is not None and np.isfinite(float(dte_raw)) else 35
            except (TypeError, ValueError):
                dte = 35
            severity = self.event_resolver(entry, dte)
            if isinstance(severity, bool):
                return not severity
            if not isinstance(severity, str):
                return True
            return severity.lower() != "high"

        # Default: read event_risk column value as a string category.
        if isinstance(event_risk, bool):
            return not event_risk
        if event_risk is None:
            return True
        if not isinstance(event_risk, str):
            return True
        return event_risk.lower() != "high"
