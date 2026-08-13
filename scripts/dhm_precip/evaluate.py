"""Task 3b — expectation evaluator, report mode.

Invokes the runner into a directory (D9: the caller passes a temp directory
for a real run), reopens every declared parquet table, compares each
expectation under D5's tolerance rule, and emits a discrepancy report. In
report mode this NEVER raises on a mismatch — a mismatch is data (D8c is
Phase 4's job); it raises only if the *machinery* fails (the runner errors,
a declared table is missing, or a table's declared (view, axis_status) set
disagrees with what it actually contains — D9's "cannot pass while the
artefacts are wrong").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl
import structlog

from scripts.dhm_precip.expectations import (
    DEFAULT_EXPECTATIONS_PATH,
    Expectation,
    load_expectations,
)
from scripts.dhm_precip.manifest_io import read_manifest

if TYPE_CHECKING:
    from pathlib import Path

    from scripts.dhm_precip.domain_types import (
        ExpectationValueRange,
        ExpectationValueScalar,
        RunManifest,
    )

log = structlog.get_logger(__name__)


class RunnerFailedError(RuntimeError):
    pass


class DeclaredTableMismatchError(ValueError):
    pass


@dataclass(frozen=True, kw_only=True, slots=True)
class Discrepancy:
    expectation_id: str
    disposition: str
    expected: ExpectationValueScalar | ExpectationValueRange | None
    actual: ExpectationValueScalar | ExpectationValueRange | None
    matched: bool
    reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class EvaluationReport:
    discrepancies: tuple[Discrepancy, ...]

    @property
    def mismatches(self) -> tuple[Discrepancy, ...]:
        return tuple(d for d in self.discrepancies if not d.matched)

    @property
    def all_matched(self) -> bool:
        return len(self.mismatches) == 0


def _round(value: float, precision: int) -> float:
    return round(float(value), precision)


def _try_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def compare_expectation(
    expectation: Expectation,
    actual: ExpectationValueScalar | ExpectationValueRange | None,
) -> Discrepancy:
    expected = (
        expectation.asserted_range
        if expectation.asserted_range is not None
        else expectation.asserted_value
    )

    if expectation.disposition == "withdrawn_unreproducible":
        # D8c: the only disposition exempt from numeric matching.
        return Discrepancy(
            expectation_id=expectation.id,
            disposition=expectation.disposition,
            expected=expected,
            actual=actual,
            matched=True,
            reason="withdrawn_unreproducible is exempt from numeric matching (D8c)",
        )

    if actual is None:
        return Discrepancy(
            expectation_id=expectation.id,
            disposition=expectation.disposition,
            expected=expected,
            actual=None,
            matched=False,
            reason="the runner produced no value for this expectation id",
        )

    if expectation.asserted_range is not None:
        if not isinstance(actual, (tuple, list)) or len(actual) != 2:
            return Discrepancy(
                expectation_id=expectation.id,
                disposition=expectation.disposition,
                expected=expected,
                actual=actual,
                matched=False,
                reason=f"expectation declares a range; runner produced {actual!r}",
            )
        lo, hi = expectation.asserted_range
        actual_lo, actual_hi = actual
        matched = _round(actual_lo, expectation.quoted_precision) == _round(
            lo, expectation.quoted_precision
        ) and _round(actual_hi, expectation.quoted_precision) == _round(
            hi, expectation.quoted_precision
        )
        return Discrepancy(
            expectation_id=expectation.id,
            disposition=expectation.disposition,
            expected=expected,
            actual=(float(actual_lo), float(actual_hi)),
            matched=matched,
            reason="range min/max compared at quoted_precision (D5)",
        )

    expected_scalar = expectation.asserted_value
    if isinstance(expected_scalar, str):
        matched = str(actual) == expected_scalar
    else:
        actual_f = _try_float(actual)
        expected_f = _try_float(expected_scalar)
        matched = (
            actual_f is not None
            and expected_f is not None
            and _round(actual_f, expectation.quoted_precision)
            == _round(expected_f, expectation.quoted_precision)
        )
    return Discrepancy(
        expectation_id=expectation.id,
        disposition=expectation.disposition,
        expected=expected,
        actual=actual,
        matched=matched,
        reason="scalar compared at quoted_precision, exact match (D5)",
    )


def evaluate_manifest(
    manifest: RunManifest, expectations: tuple[Expectation, ...]
) -> EvaluationReport:
    discrepancies = tuple(
        compare_expectation(expectation, manifest.values.get(expectation.id))
        for expectation in expectations
    )
    return EvaluationReport(discrepancies=discrepancies)


def validate_declared_tables(manifest: RunManifest, out: Path) -> None:
    """D9: 'The gate reopens every declared parquet file' — cannot pass
    while the artefacts are wrong, independent of expectation values."""
    for table in manifest.tables:
        path = out / "tables" / f"{table.name}.parquet"
        if not path.exists():
            raise DeclaredTableMismatchError(
                f"declared table {table.name!r} missing at {path}"
            )
        frame = pl.read_parquet(path)
        if "view" not in frame.columns or "axis_status" not in frame.columns:
            raise DeclaredTableMismatchError(
                f"table {table.name!r} is missing its view/axis_status columns"
            )
        observed = {
            (row[0], row[1])
            for row in frame.select("view", "axis_status").unique().rows()
        }
        declared = {(view.value, axis.value) for view, axis in table.view_axis_pairs}
        if observed != declared:
            raise DeclaredTableMismatchError(
                f"table {table.name!r}: declared {declared} != observed {observed}"
            )


def run_report(
    out: Path, expectations_path: Path = DEFAULT_EXPECTATIONS_PATH
) -> EvaluationReport:
    """Invoke the runner into `out`, reopen its artefacts, and evaluate every
    expectation. Report mode: exits (returns) normally whenever the machinery
    works — a mismatch is data, never an exception here."""
    # Local import: avoid import cost when only evaluate_manifest is needed.
    from scripts.dhm_precip.run import run as run_pipeline

    exit_code = run_pipeline(out)
    if exit_code != 0:
        raise RunnerFailedError(
            f"runner exited {exit_code} — see stderr for the loader error"
        )

    manifest = read_manifest(out / "results.json")
    validate_declared_tables(manifest, out)
    expectations = load_expectations(expectations_path)
    log.info("dhm_precip.evaluate.report", n_expectations=len(expectations))
    return evaluate_manifest(manifest, expectations)
