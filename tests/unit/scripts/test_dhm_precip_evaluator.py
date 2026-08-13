"""Task 3b — expectation evaluator, report mode.

Synthetic expectations and artefacts only (constraint 1) — these tests never
touch the real file, so they are green independently of it.

Includes the KEY D8c criterion: `withdrawn_unreproducible` is the ONLY
disposition exempt from numeric matching — a wildly wrong actual value must
still evaluate as `matched=True` for it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

from scripts.dhm_precip.domain_types import (
    AxisStatus,
    RunManifest,
    TableDeclaration,
    View,
)
from scripts.dhm_precip.evaluate import (
    DeclaredTableMismatchError,
    compare_expectation,
    evaluate_manifest,
    validate_declared_tables,
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

    def test_a_missing_actual_value_still_matches(self) -> None:
        expectation = _expectation(disposition="withdrawn_unreproducible")
        result = compare_expectation(expectation, None)
        assert result.matched is True

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


class TestValidateDeclaredTables:
    def _manifest(self, tables: tuple[TableDeclaration, ...]) -> RunManifest:
        return RunManifest(
            run_id="r",
            source_path="p",
            source_sha256="0" * 64,
            generated_at=datetime(2024, 1, 1, tzinfo=UTC),
            parameters={},
            tables=tables,
        )

    def test_raises_when_a_declared_table_file_is_missing(self, tmp_path) -> None:
        manifest = self._manifest(
            (TableDeclaration(name="missing_table", view_axis_pairs=()),)
        )
        with pytest.raises(DeclaredTableMismatchError, match="missing"):
            validate_declared_tables(manifest, tmp_path)

    def test_raises_when_view_axis_pairs_disagree(self, tmp_path) -> None:
        tables_dir = tmp_path / "tables"
        tables_dir.mkdir()
        frame = pl.DataFrame({"view": ["ON_GRID"], "axis_status": ["RAW_PROVISIONAL"]})
        frame.write_parquet(tables_dir / "t.parquet")
        declared = ((View.RAW, AxisStatus.RAW_AXIS_DIAGNOSTIC),)
        manifest = self._manifest(
            (TableDeclaration(name="t", view_axis_pairs=declared),)
        )
        with pytest.raises(DeclaredTableMismatchError, match="declared"):
            validate_declared_tables(manifest, tmp_path)

    def test_passes_when_the_table_matches_its_declaration(self, tmp_path) -> None:
        tables_dir = tmp_path / "tables"
        tables_dir.mkdir()
        frame = pl.DataFrame({"view": ["ON_GRID"], "axis_status": ["RAW_PROVISIONAL"]})
        frame.write_parquet(tables_dir / "t.parquet")
        declared = ((View.ON_GRID, AxisStatus.RAW_PROVISIONAL),)
        manifest = self._manifest(
            (TableDeclaration(name="t", view_axis_pairs=declared),)
        )
        validate_declared_tables(manifest, tmp_path)  # must not raise
