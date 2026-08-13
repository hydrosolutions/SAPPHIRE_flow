"""Task 1c — the expectation manifest (D8, D8b, D8c, D11).

Committed data (`expectations.toml`), authored before the code that gates it.
Pydantic validates the TOML at this system boundary (CLAUDE.md: pydantic at
boundaries only); `Expectation` is the frozen domain type everything else
consumes.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

DEFAULT_EXPECTATIONS_PATH = Path(__file__).with_name("expectations.toml")

Source = Literal["vision-findings", "plan-170"]
Disposition = Literal["active", "corrected", "withdrawn_unreproducible"]
ViewName = Literal["RAW", "ON_GRID"]
GrainName = Literal[
    "source_timestamp_rows", "station_timestamp_cells", "non_null_observations"
]
AxisStatusName = Literal["AXIS_INDEPENDENT", "RAW_AXIS_DIAGNOSTIC", "RAW_PROVISIONAL"]

# D8b — mandatory keys, over and above whatever base method choices already
# apply, for every candidate-run statistic (`is_run_statistic = true`).
RUN_METHOD_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "minimum_run_duration",
        "run_predicate",
        "stuck_value_tolerance",
        "ordering_basis",
        "adjacency_rule",
        "gap_treatment",
        "missing_value_bridging",
        "season_boundary",
        "merge_distance",
    }
)


class ExpectationModel(BaseModel):
    """Boundary schema for one `[[expectation]]` TOML entry."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source: Source
    vision_ref: str | None = None
    plan_ref: str | None = None
    statistic: str
    value: float | int | str | None = None
    range: tuple[float, float] | None = None
    unit: str
    view: ViewName
    grain: GrainName
    axis_status: AxisStatusName
    population: str
    quoted_precision: int
    disposition: Disposition
    method: dict[str, object]
    successor: str | None = None
    is_run_statistic: bool = False
    original_value: float | int | str | None = None
    original_range: tuple[float, float] | None = None
    corrected_value: float | int | str | None = None
    corrected_range: tuple[float, float] | None = None
    correction_provenance: str | None = None
    method_comparison: str | None = None
    evidence: str | None = None

    @model_validator(mode="after")
    def _check_provenance(self) -> ExpectationModel:
        if self.source == "vision-findings" and not self.vision_ref:
            raise ValueError(
                f"{self.id}: source=vision-findings requires vision_ref (D8)"
            )
        if self.source == "plan-170" and not self.plan_ref:
            raise ValueError(f"{self.id}: source=plan-170 requires plan_ref (D8, D12)")
        return self

    @model_validator(mode="after")
    def _check_value_xor_range(self) -> ExpectationModel:
        if (self.value is None) == (self.range is None):
            raise ValueError(f"{self.id}: exactly one of value/range must be set (D5)")
        return self

    @model_validator(mode="after")
    def _check_raw_view_is_diagnostic(self) -> ExpectationModel:
        # D6: "a blanket ban would be unsatisfiable" — RAW may satisfy only
        # RAW_AXIS_DIAGNOSTIC expectations; every other expectation is ON_GRID.
        if self.view == "RAW" and self.axis_status != "RAW_AXIS_DIAGNOSTIC":
            raise ValueError(
                f"{self.id}: view=RAW requires axis_status=RAW_AXIS_DIAGNOSTIC (D6)"
            )
        return self

    @model_validator(mode="after")
    def _check_provisional_has_successor(self) -> ExpectationModel:
        # D11: no RAW_PROVISIONAL family is left unmapped.
        if self.axis_status == "RAW_PROVISIONAL" and not self.successor:
            raise ValueError(
                f"{self.id}: axis_status=RAW_PROVISIONAL requires a successor "
                "milestone (D11)"
            )
        return self

    @model_validator(mode="after")
    def _check_method_present(self) -> ExpectationModel:
        # D8: "rejects any entry missing a required method key" — a statistic
        # asserted with no declared method is unverifiable by construction.
        if not self.method:
            raise ValueError(f"{self.id}: method table must not be empty (D8b)")
        if self.is_run_statistic:
            missing = RUN_METHOD_REQUIRED_KEYS - set(self.method.keys())
            if missing:
                raise ValueError(
                    f"{self.id}: run statistic missing method keys "
                    f"{sorted(missing)} (D8b)"
                )
        return self

    @model_validator(mode="after")
    def _check_disposition_records(self) -> ExpectationModel:
        if self.disposition == "corrected":
            # D8c: "the new value is asserted numerically too, with the
            # original retained plus correction provenance."
            has_corrected = (
                self.corrected_value is not None or self.corrected_range is not None
            )
            has_original = (
                self.original_value is not None or self.original_range is not None
            )
            if not (has_corrected and has_original and self.correction_provenance):
                raise ValueError(
                    f"{self.id}: disposition=corrected requires original + corrected "
                    "value/range + correction_provenance (D8c)"
                )
        if self.disposition == "withdrawn_unreproducible":
            has_original = (
                self.original_value is not None or self.original_range is not None
            )
            missing = [
                name
                for name, val in (
                    ("original_value/original_range", has_original or None),
                    ("method_comparison", self.method_comparison),
                    ("evidence", self.evidence),
                    ("successor", self.successor),
                )
                if not val
            ]
            if missing:
                raise ValueError(
                    f"{self.id}: disposition=withdrawn_unreproducible requires a "
                    f"Phase-4 record, missing {missing} (D8c)"
                )
        return self


@dataclass(frozen=True, kw_only=True, slots=True)
class Expectation:
    """The domain type every other module (evaluator, statistic families) consumes."""

    id: str
    source: Source
    statistic: str
    value: float | int | str | None
    range: tuple[float, float] | None
    unit: str
    view: ViewName
    grain: GrainName
    axis_status: AxisStatusName
    population: str
    quoted_precision: int
    disposition: Disposition
    method: dict[str, object]
    successor: str | None
    asserted_value: float | int | str | None
    asserted_range: tuple[float, float] | None

    @staticmethod
    def from_model(model: ExpectationModel) -> Expectation:
        # D8c: for `corrected`, assert the NEW (corrected) value, not the
        # original one the vision stated.
        if model.disposition == "corrected":
            asserted_value = model.corrected_value
            asserted_range = model.corrected_range
        else:
            asserted_value = model.value
            asserted_range = model.range
        return Expectation(
            id=model.id,
            source=model.source,
            statistic=model.statistic,
            value=model.value,
            range=model.range,
            unit=model.unit,
            view=model.view,
            grain=model.grain,
            axis_status=model.axis_status,
            population=model.population,
            quoted_precision=model.quoted_precision,
            disposition=model.disposition,
            method=model.method,
            successor=model.successor,
            asserted_value=asserted_value,
            asserted_range=asserted_range,
        )


class DuplicateExpectationIdError(ValueError):
    pass


def load_expectations(
    path: Path = DEFAULT_EXPECTATIONS_PATH,
) -> tuple[Expectation, ...]:
    raw = tomllib.loads(path.read_text())
    entries = raw.get("expectation", [])
    models = [ExpectationModel.model_validate(entry) for entry in entries]
    seen: set[str] = set()
    for model in models:
        if model.id in seen:
            raise DuplicateExpectationIdError(f"duplicate expectation id: {model.id}")
        seen.add(model.id)
    return tuple(Expectation.from_model(model) for model in models)
