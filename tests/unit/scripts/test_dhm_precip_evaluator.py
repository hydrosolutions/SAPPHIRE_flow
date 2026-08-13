"""Task 3b — expectation evaluator, report mode.

Synthetic expectations and artefacts only (constraint 1) — these tests never
touch the real file, so they are green independently of it.

Includes the KEY D8c criterion: `withdrawn_unreproducible` is the ONLY
disposition exempt from numeric matching — a wildly wrong actual value must
still evaluate as `matched=True` for it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scripts.dhm_precip.domain_types import (
    RunManifest,
)
from scripts.dhm_precip.evaluate import (
    DeclaredTableMismatchError,
    ExpectationCoverageError,
    compare_expectation,
    evaluate_manifest,
    read_computed_tables,
    validate_expectation_coverage,
)
from scripts.dhm_precip.expectations import Expectation


def _expectation(**overrides: object) -> Expectation:
    base: dict[str, object] = {
        "id": "t_1",
        "source": "vision-findings",
        "statistic": "x",
        "value": 10.0,
        "range": None,
        "unit": "count",
        "view": "ON_GRID",
        "grain": "source_timestamp_rows",
        "axis_status": "RAW_PROVISIONAL",
        "population": "p",
        "quoted_precision": 0,
        "disposition": "active",
        "method": {"denominator": "d"},
        "successor": "M-A2",
        "asserted_value": 10.0,
        "asserted_range": None,
    }
    base.update(overrides)
    return Expectation(**base)  # type: ignore[arg-type]


class TestWithdrawnUnreproducibleExemptFromMatching:
    """KEY D8c criterion — the ONLY disposition exempt from numeric matching."""

    def test_a_wildly_wrong_actual_value_still_matches(self) -> None:
        expectation = _expectation(
            disposition="withdrawn_unreproducible",
            value=10.0,
            asserted_value=10.0,
        )
        result = compare_expectation(expectation, 99999.0)
        assert result.matched is True

    def test_a_missing_actual_value_is_a_machinery_failure_not_a_match(self) -> None:
        # D9: the gate cannot pass while the artefacts are wrong. A
        # withdrawn_unreproducible expectation is exempt from NUMERIC
        # matching (the vision's figure is expected to differ), but the
        # runner must still have attempted the computation — a missing
        # value means it never ran at all, which is a real regression the
        # gate must catch, not silently wave through as "exempt".
        expectation = _expectation(disposition="withdrawn_unreproducible")
        result = compare_expectation(expectation, None)
        assert result.matched is False
        assert "no value" in result.reason

    def test_active_disposition_is_not_exempt(self) -> None:
        expectation = _expectation(
            disposition="active", value=10.0, asserted_value=10.0
        )
        result = compare_expectation(expectation, 99999.0)
        assert result.matched is False


class TestScalarComparison:
    def test_matches_at_quoted_precision(self) -> None:
        expectation = _expectation(value=0.05, asserted_value=0.05, quoted_precision=2)
        result = compare_expectation(expectation, 0.0472)  # rounds to 0.05
        assert result.matched is True

    def test_mismatches_beyond_quoted_precision(self) -> None:
        expectation = _expectation(value=0.05, asserted_value=0.05, quoted_precision=3)
        result = compare_expectation(expectation, 0.0472)  # rounds to 0.047
        assert result.matched is False

    def test_string_values_compare_by_equality(self) -> None:
        expectation = _expectation(value="true", asserted_value="true", unit="bool")
        assert compare_expectation(expectation, "true").matched is True
        assert compare_expectation(expectation, "false").matched is False

    def test_none_actual_is_a_mismatch_for_active(self) -> None:
        expectation = _expectation(value=10.0, asserted_value=10.0)
        result = compare_expectation(expectation, None)
        assert result.matched is False
        assert "no value" in result.reason


class TestRangeComparison:
    def test_matches_when_both_bounds_round_correctly(self) -> None:
        expectation = _expectation(
            value=None,
            range=(0.14, 0.55),
            asserted_value=None,
            asserted_range=(0.14, 0.55),
            quoted_precision=2,
        )
        result = compare_expectation(expectation, (0.1401, 0.5499))
        assert result.matched is True

    def test_mismatches_when_a_bound_is_off(self) -> None:
        expectation = _expectation(
            value=None,
            range=(0.14, 0.55),
            asserted_value=None,
            asserted_range=(0.14, 0.55),
            quoted_precision=2,
        )
        result = compare_expectation(expectation, (0.08, 0.55))
        assert result.matched is False

    def test_a_non_range_actual_for_a_range_expectation_is_a_mismatch(self) -> None:
        expectation = _expectation(
            value=None,
            range=(0.14, 0.55),
            asserted_value=None,
            asserted_range=(0.14, 0.55),
        )
        result = compare_expectation(expectation, 0.3)
        assert result.matched is False


class TestCorrectedUsesTheNewValue:
    def test_corrected_asserts_the_corrected_value_not_the_original(self) -> None:
        # asserted_value is what Expectation.from_model computes for
        # disposition=corrected — the evaluator must compare against THAT,
        # never the vision-original `value`.
        expectation = _expectation(
            disposition="corrected", value=46.0, asserted_value=45.0
        )
        assert compare_expectation(expectation, 45.0).matched is True
        assert compare_expectation(expectation, 46.0).matched is False


class TestEvaluateManifest:
    def test_aggregates_matches_and_mismatches(self) -> None:
        expectations = (
            _expectation(id="a", value=1.0, asserted_value=1.0),
            _expectation(id="b", value=2.0, asserted_value=2.0),
        )
        manifest = RunManifest(
            run_id="r",
            source_path="p",
            source_sha256="0" * 64,
            generated_at=datetime(2024, 1, 1, tzinfo=UTC),
            parameters={},
            values={"a": 1.0, "b": 999.0},
        )
        report = evaluate_manifest(manifest, expectations)
        assert len(report.discrepancies) == 2
        assert len(report.mismatches) == 1
        assert report.mismatches[0].expectation_id == "b"
        assert report.all_matched is False


class TestReadComputedTables:
    """`read_computed_tables` reopens every `ComputedTables` field's parquet
    — the D9 machinery blocker-1 fix requires. Full round-trip coverage
    (schema/(view,axis_status)/counts recomputation against real artefacts)
    lives in the integration-level mutation tests
    (`tests/integration/test_dhm_precip_reproduction.py`), which run
    against the real pipeline's actual ~27-table output — a synthetic
    stand-in here would not exercise the real `ComputedTables` shape."""

    def test_raises_when_any_declared_table_file_is_missing(self, tmp_path) -> None:
        (tmp_path / "tables").mkdir()
        with pytest.raises(DeclaredTableMismatchError, match="missing"):
            read_computed_tables(tmp_path)


class TestValidateExpectationCoverage:
    def _manifest(self, values: dict[str, object]) -> RunManifest:
        return RunManifest(
            run_id="r",
            source_path="p",
            source_sha256="0" * 64,
            generated_at=datetime(2024, 1, 1, tzinfo=UTC),
            parameters={},
            values=values,  # type: ignore[arg-type]
        )

    def test_raises_when_an_expectation_id_has_no_runner_produced_key(self) -> None:
        expectations = (_expectation(id="a"), _expectation(id="b"))
        manifest = self._manifest({"a": 1.0})  # "b" never computed at all
        with pytest.raises(ExpectationCoverageError, match="b"):
            validate_expectation_coverage(manifest, expectations)

    def test_passes_when_every_expectation_id_has_a_key_even_if_the_value_is_none(
        self,
    ) -> None:
        expectations = (_expectation(id="a"),)
        # None is a legitimate "not computable this run" value — the key
        # still exists, so this is coverage, not a missing computation.
        manifest = self._manifest({"a": None})
        validate_expectation_coverage(manifest, expectations)  # must not raise
