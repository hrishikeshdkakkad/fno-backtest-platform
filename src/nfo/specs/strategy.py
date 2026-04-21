"""StrategySpec and nested models (master design §4.1)."""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

Underlying = Literal["NIFTY", "BANKNIFTY", "FINNIFTY"]


class UniverseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    underlyings: list[Underlying]
    delta_target: Annotated[float, Field(gt=0, lt=1)]
    delta_tolerance: Annotated[float, Field(ge=0, lt=0.5)]
    width_rule: Literal["fixed", "formula", "risk_budget"]
    width_value: float | None = None
    dte_target: Annotated[int, Field(ge=1, le=60)]
    dte_tolerance: Annotated[int, Field(ge=0, le=14)]
    allowed_contract_families: list[Literal["PE", "CE"]] = Field(default_factory=lambda: ["PE"])

    @model_validator(mode="after")
    def _fixed_width_requires_value(self) -> "UniverseSpec":
        if self.width_rule == "fixed" and self.width_value is None:
            raise ValueError("width_rule='fixed' requires width_value to be set")
        return self


class TriggerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score_gates: dict[str, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)
    specific_pass_gates: list[str] = Field(default_factory=list)
    event_window_days: Annotated[int, Field(ge=0, le=30)] = 10
    feature_thresholds: dict[str, float] = Field(default_factory=dict)
    missing_data_policy: Literal["skip_day", "treat_as_fail", "treat_as_pass"] = "skip_day"


class SelectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["day_matched", "cycle_matched", "live_rule"]
    one_trade_per_cycle: bool = True
    preferred_exit_variant: Literal["pt25", "pt50", "pt75", "hte", "dte2"]
    canonical_trade_chooser: Literal["first_fire", "best_delta_match", "earliest_entry"] = "first_fire"
    width_handling: Literal["strict_fixed", "allow_alternate"] = "strict_fixed"
    tie_breaker_order: list[str] = Field(
        default_factory=lambda: ["delta_err_asc", "width_exact", "entry_date_asc"]
    )


class EntrySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    earliest_entry_relative_to_first_fire: Annotated[int, Field(ge=0)] = 0
    session_snap_rule: Literal["forward_only", "forward_or_backward", "no_snap"] = "forward_only"
    entry_timestamp_convention: Literal["session_close", "session_open", "mid_session"] = "session_close"
    allow_pre_fire_entry: bool = False


class ExitSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant: Literal["pt25", "pt50", "pt75", "hte", "dte2"]
    profit_take_fraction: float | None = None
    manage_at_dte: Annotated[int, Field(ge=0, le=60)] | None = None
    expiry_settlement: Literal["cash_settled_to_spot", "held_to_expiry_intrinsic"] = "cash_settled_to_spot"

    @model_validator(mode="after")
    def _variant_constraints(self) -> "ExitSpec":
        if self.variant == "hte":
            if self.manage_at_dte is not None:
                raise ValueError("exit_rule.variant='hte' requires manage_at_dte=None")
            if self.profit_take_fraction not in (None, 1.0):
                raise ValueError("exit_rule.variant='hte' requires profit_take_fraction in (None, 1.0)")
        return self


class CapitalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixed_capital_inr: Annotated[float, Field(gt=0)]
    deployment_fraction: Annotated[float, Field(gt=0, le=1.0)] = 1.0
    compounding: bool = False
    lot_rounding_mode: Literal["floor", "round"] = "floor"


class SlippageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["flat_rupees_per_lot", "percent_of_premium"] = "flat_rupees_per_lot"
    flat_rupees_per_lot: float = 0.0
    percent_of_premium: float = 0.0


_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    strategy_version: str
    description: str
    universe: UniverseSpec
    feature_set: list[str]
    trigger_rule: TriggerSpec
    selection_rule: SelectionSpec
    entry_rule: EntrySpec
    exit_rule: ExitSpec
    capital_rule: CapitalSpec
    slippage_rule: SlippageSpec
    report_defaults: dict[str, Any] = Field(default_factory=dict)

    @field_validator("strategy_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(f"strategy_version must match ^\\d+\\.\\d+\\.\\d+$, got {v!r}")
        return v

    @model_validator(mode="after")
    def _live_rule_consistency(self) -> "StrategySpec":
        if self.selection_rule.mode == "live_rule":
            if self.entry_rule.allow_pre_fire_entry:
                raise ValueError("selection mode 'live_rule' forbids entry_rule.allow_pre_fire_entry=True")
            if self.entry_rule.earliest_entry_relative_to_first_fire != 0:
                raise ValueError(
                    "selection mode 'live_rule' requires entry_rule.earliest_entry_relative_to_first_fire == 0"
                )
        return self
