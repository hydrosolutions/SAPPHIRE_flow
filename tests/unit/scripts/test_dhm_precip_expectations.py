"""Task 1c — the expectation manifest (D8, D8b, D8c, D11).

The three KEY schema-rejection criteria named in the plan (task 1c
verification): missing `method` key, a non-diagnostic `RAW` declaration
(D6), and a `RAW_PROVISIONAL` entry missing `successor` (D11)."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from scripts.dhm_precip.expectations import (
    DEFAULT_EXPECTATIONS_PATH,
    ExpectationModel,
    load_expectations,
)

if TYPE_CHECKING:
    from pathlib import Path

_BASE_ENTRY: dict[str, object] = {
    "id": "t_1",
    "source": "vision-findings",
    "vision_ref": "docs/design/dhm-precipitation-vision.md:1",
    "statistic": "x",
    "value": 1,
    "unit": "count",
    "view": "ON_GRID",
    "grain": "source_timestamp_rows",
    "axis_status": "RAW_PROVISIONAL",
    "population": "p",
    "quoted_precision": 0,
    "disposition": "active",
    "method": {"denominator": "d"},
    "successor": "M-A2",
}


def _entry(**overrides: object) -> dict[str, object]:
    merged = dict(_BASE_ENTRY)
    merged.update(overrides)
    return merged


class TestSchemaRejectsMissingMethod:
    def test_empty_method_table_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="method table must not be empty"):
            ExpectationModel.model_validate(_entry(method={}))

    def test_run_statistic_missing_run_method_keys_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="run statistic missing method keys"):
            ExpectationModel.model_validate(
                _entry(is_run_statistic=True, method={"denominator": "d"})
            )

    def test_run_statistic_with_the_full_key_set_is_accepted(self) -> None:
        full_method = {
            "denominator": "d",
            "minimum_run_duration": "12h",
            "run_predicate": "zero",
            "stuck_value_tolerance": "1e-9",
            "ordering_basis": "timestamp",
            "adjacency_rule": "consecutive",
            "gap_treatment": "break",
            "missing_value_bridging": "none",
            "season_boundary": "jjas",
            "merge_distance": "0",
        }
        model = ExpectationModel.model_validate(
            _entry(is_run_statistic=True, method=full_method)
        )
        assert model.is_run_statistic is True


class TestSchemaRejectsNonDiagnosticRaw:
    def test_raw_view_without_diagnostic_axis_status_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="RAW_AXIS_DIAGNOSTIC"):
            ExpectationModel.model_validate(
                _entry(view="RAW", axis_status="RAW_PROVISIONAL", successor="M-A2")
            )

    def test_raw_view_with_diagnostic_axis_status_is_accepted(self) -> None:
        model = ExpectationModel.model_validate(
            _entry(view="RAW", axis_status="RAW_AXIS_DIAGNOSTIC", successor=None)
        )
        assert model.axis_status == "RAW_AXIS_DIAGNOSTIC"


class TestSchemaRequiresSuccessorForProvisional:
    def test_provisional_without_successor_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="successor milestone"):
            ExpectationModel.model_validate(
                _entry(axis_status="RAW_PROVISIONAL", successor=None)
            )

    def test_axis_independent_needs_no_successor(self) -> None:
        # AXIS_INDEPENDENT results don't depend on the view (D7) but the
        # schema restricts a `view=RAW` declaration to RAW_AXIS_DIAGNOSTIC
        # entries only (D6), so AXIS_INDEPENDENT entries declare ON_GRID.
        model = ExpectationModel.model_validate(
            _entry(view="ON_GRID", axis_status="AXIS_INDEPENDENT", successor=None)
        )
        assert model.successor is None


class TestValueXorRange:
    def test_both_value_and_range_set_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exactly one of value/range"):
            ExpectationModel.model_validate(_entry(value=1, range=(1.0, 2.0)))

    def test_neither_value_nor_range_set_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exactly one of value/range"):
            ExpectationModel.model_validate(_entry(value=None, range=None))


class TestDispositionCompleteness:
    def test_corrected_without_correction_fields_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="disposition=corrected"):
            ExpectationModel.model_validate(_entry(disposition="corrected"))

    def test_corrected_with_full_record_is_accepted(self) -> None:
        model = ExpectationModel.model_validate(
            _entry(
                disposition="corrected",
                original_value=1,
                corrected_value=2,
                correction_provenance="found a units bug",
            )
        )
        assert model.corrected_value == 2

    def test_withdrawn_without_phase4_record_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="withdrawn_unreproducible"):
            ExpectationModel.model_validate(
                _entry(disposition="withdrawn_unreproducible")
            )

    def test_withdrawn_with_full_record_is_accepted(self) -> None:
        model = ExpectationModel.model_validate(
            _entry(
                disposition="withdrawn_unreproducible",
                original_value=1,
                method_comparison="our run predicate differs",
                evidence="see report row 12",
                successor="M-A3",
            )
        )
        assert model.disposition == "withdrawn_unreproducible"


class TestProvenanceRequired:
    def test_vision_findings_without_vision_ref_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="vision_ref"):
            ExpectationModel.model_validate(_entry(vision_ref=None))

    def test_plan_158_without_plan_ref_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="plan_ref"):
            ExpectationModel.model_validate(_entry(source="plan-170", vision_ref=None))


_DUPLICATE_ID_TOML = """
[[expectation]]
id = "dup"
source = "vision-findings"
vision_ref = "docs/design/dhm-precipitation-vision.md:1"
statistic = "x"
value = 1
unit = "count"
view = "ON_GRID"
grain = "source_timestamp_rows"
axis_status = "RAW_PROVISIONAL"
population = "p"
quoted_precision = 0
disposition = "active"
successor = "M-A2"
method = { denominator = "d" }

[[expectation]]
id = "dup"
source = "vision-findings"
vision_ref = "docs/design/dhm-precipitation-vision.md:2"
statistic = "y"
value = 2
unit = "count"
view = "ON_GRID"
grain = "source_timestamp_rows"
axis_status = "RAW_PROVISIONAL"
population = "p"
quoted_precision = 0
disposition = "active"
successor = "M-A2"
method = { denominator = "d" }
"""


class TestLoadExpectationsRejectsDuplicateIds:
    def test_duplicate_id_raises(self, tmp_path: Path) -> None:
        from scripts.dhm_precip.expectations import DuplicateExpectationIdError

        path = tmp_path / "dup.toml"
        path.write_text(_DUPLICATE_ID_TOML)
        with pytest.raises(DuplicateExpectationIdError):
            load_expectations(path)


class TestRealManifestLoads:
    def test_the_committed_manifest_parses_and_validates(self) -> None:
        expectations = load_expectations(DEFAULT_EXPECTATIONS_PATH)
        assert len(expectations) > 0
        ids = [e.id for e in expectations]
        assert len(ids) == len(set(ids))

    def test_every_raw_provisional_entry_declares_a_successor(self) -> None:
        raw = tomllib.loads(DEFAULT_EXPECTATIONS_PATH.read_text())
        for entry in raw["expectation"]:
            if entry["axis_status"] == "RAW_PROVISIONAL":
                assert entry.get("successor"), entry["id"]
