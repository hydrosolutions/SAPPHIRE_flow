"""Task 3b — expectation evaluator, report mode.

Invokes the runner into a directory (D9: the caller passes a temp directory
for a real run), reopens every declared parquet table into a fresh
`ComputedTables`, RECOMPUTES every expectation value and every view's D6b
counts from those reopened artefacts (never trusting `results.json`'s own
numbers at face value), and compares each expectation under D5's tolerance
rule, emitting a discrepancy report. In report mode this NEVER raises on a
numeric mismatch — a mismatch is data (D8c is Phase 4's job); it raises only
if the *machinery* fails (the runner errors, a declared table is missing or
has an unexpected schema, a table's declared (view, axis_status) set
disagrees with what it actually contains, the recomputed values disagree
with `results.json`, the recomputed view counts disagree with
`results.json`, or an expectation id has no corresponding runner-produced
key at all) — D9's "cannot pass while the artefacts are wrong".
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING

import polars as pl
import structlog

from scripts.dhm_precip.domain_types import ViewCounts
from scripts.dhm_precip.expectations import (
    DEFAULT_EXPECTATIONS_PATH,
    Expectation,
    load_expectations,
)
from scripts.dhm_precip.loader import EXPECTED_WORKBOOK_COLUMNS
from scripts.dhm_precip.manifest_io import read_manifest
from scripts.dhm_precip.numeric import as_int
from scripts.dhm_precip.pipeline import (
    ComputedTables,
    extract_values,
    table_declarations,
)

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


class ExpectationCoverageError(ValueError):
    """D9/D5: every expectation id must have a runner-produced key in
    `results.json` — a missing key means the pipeline never computed this
    statistic at all, which is a machinery failure, not data."""


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

    if actual is None:
        # A missing value is a MACHINERY failure regardless of disposition —
        # including withdrawn_unreproducible. D8c exempts withdrawn entries
        # from NUMERIC matching (the vision's original figure is expected to
        # differ from ours), but the pipeline must still have attempted the
        # computation and produced *some* value to record as evidence; a
        # withdrawn expectation the runner never computed at all is not
        # "reproduced-but-different", it is broken.
        return Discrepancy(
            expectation_id=expectation.id,
            disposition=expectation.disposition,
            expected=expected,
            actual=None,
            matched=False,
            reason="the runner produced no value for this expectation id",
        )

    if expectation.disposition == "withdrawn_unreproducible":
        # D8c: the only disposition exempt from NUMERIC matching — but only
        # once the runner actually produced a value (see the `actual is
        # None` branch above).
        return Discrepancy(
            expectation_id=expectation.id,
            disposition=expectation.disposition,
            expected=expected,
            actual=actual,
            matched=True,
            reason="withdrawn_unreproducible is exempt from numeric matching (D8c)",
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


def read_computed_tables(out: Path) -> ComputedTables:
    """Reopen every `ComputedTables` field's declared parquet file from
    `out/tables/`. Raises if any is missing — the gate cannot recompute
    from artefacts that are not there."""
    tables_dir = out / "tables"
    kwargs: dict[str, pl.DataFrame] = {}
    for field in fields(ComputedTables):
        path = tables_dir / f"{field.name}.parquet"
        if not path.exists():
            raise DeclaredTableMismatchError(
                f"declared table {field.name!r} missing at {path}"
            )
        kwargs[field.name] = pl.read_parquet(path)
    return ComputedTables(**kwargs)  # type: ignore[arg-type]


def _validate_table_schemas_and_declarations(
    manifest: RunManifest, tables: ComputedTables, out: Path
) -> None:
    """D9: every declared table's (view, axis_status) row set must match
    what the manifest claims — recomputed from the reopened parquet files,
    not merely re-asserted from `results.json`. Also rejects any file under
    `tables/` that is not a `ComputedTables` field (an orphaned/extra
    artefact) and any `ComputedTables` field with no parquet on disk."""
    on_disk = {p.stem for p in (out / "tables").glob("*.parquet")}
    expected_names = {field.name for field in fields(ComputedTables)}
    if on_disk != expected_names:
        raise DeclaredTableMismatchError(
            f"tables/ on disk {sorted(on_disk)} != expected {sorted(expected_names)}"
        )
    for field in fields(ComputedTables):
        frame = getattr(tables, field.name)
        if "view" not in frame.columns or "axis_status" not in frame.columns:
            raise DeclaredTableMismatchError(
                f"table {field.name!r} is missing its view/axis_status columns"
            )
    recomputed_declarations = table_declarations(tables)
    if recomputed_declarations != manifest.tables:
        raise DeclaredTableMismatchError(
            f"(view, axis_status) pairs recomputed from the reopened artefacts "
            f"{recomputed_declarations} != results.json's declared "
            f"{manifest.tables}"
        )


def _recomputed_counts_by_view(tables: ComputedTables) -> dict[str, ViewCounts]:
    """Independently derive `counts_by_view` from persisted artefacts —
    `row_counts` and `off_grid_obs` (Task 2b, view=RAW) already carry every
    number needed, since the melt is fully rectangular (every source row
    carries all 37 station columns, always)."""
    n_stations = len(EXPECTED_WORKBOOK_COLUMNS)
    raw_total_rows = as_int(tables.row_counts["total_rows"][0])
    raw_off_grid_rows = as_int(tables.row_counts["off_grid_row_count"][0])
    raw_non_null = as_int(tables.off_grid_obs["total_non_null_observations"][0])
    off_grid_non_null = as_int(tables.off_grid_obs["off_grid_observation_count"][0])
    on_grid_rows = raw_total_rows - raw_off_grid_rows
    return {
        "RAW": ViewCounts(
            source_timestamp_rows=raw_total_rows,
            station_timestamp_cells=raw_total_rows * n_stations,
            non_null_observations=raw_non_null,
        ),
        "ON_GRID": ViewCounts(
            source_timestamp_rows=on_grid_rows,
            station_timestamp_cells=on_grid_rows * n_stations,
            non_null_observations=raw_non_null - off_grid_non_null,
        ),
    }


def validate_artefacts(manifest: RunManifest, out: Path) -> ComputedTables:
    """D9: 'The gate reopens every declared parquet file' and 'cannot pass
    while the artefacts are wrong' — reopens every table into a fresh
    `ComputedTables`, validates schema and (view, axis_status) coverage,
    RECOMPUTES every expectation-bearing value and every view's D6b counts
    from those reopened artefacts, and requires them to agree exactly with
    `results.json`. A tampered or stale `results.json` that no longer
    matches its own parquet files fails here, independent of any
    expectation's disposition."""
    tables = read_computed_tables(out)
    _validate_table_schemas_and_declarations(manifest, tables, out)

    recomputed_values = extract_values(tables)
    if recomputed_values != manifest.values:
        mismatched = sorted(
            k
            for k in recomputed_values.keys() | manifest.values.keys()
            if recomputed_values.get(k) != manifest.values.get(k)
        )
        raise DeclaredTableMismatchError(
            "values recomputed from the reopened artefacts disagree with "
            f"results.json for: {mismatched}"
        )

    recomputed_counts = _recomputed_counts_by_view(tables)
    if recomputed_counts != manifest.counts_by_view:
        raise DeclaredTableMismatchError(
            f"counts_by_view recomputed from the reopened artefacts "
            f"{recomputed_counts} != results.json's {manifest.counts_by_view}"
        )
    return tables


def validate_expectation_coverage(
    manifest: RunManifest, expectations: tuple[Expectation, ...]
) -> None:
    """Every expectation id must have a runner-produced key in
    `results.json.values` — regardless of disposition. A missing key means
    the pipeline never attempted this statistic at all (a wiring bug), which
    is a machinery failure, distinct from a legitimate `None` ("not
    computable this run", still an explicit key)."""
    expectation_ids = {e.id for e in expectations}
    missing = sorted(expectation_ids - manifest.values.keys())
    if missing:
        raise ExpectationCoverageError(
            f"expectation id(s) with no runner-produced value at all: {missing}"
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
    validate_artefacts(manifest, out)
    expectations = load_expectations(expectations_path)
    validate_expectation_coverage(manifest, expectations)
    log.info("dhm_precip.evaluate.report", n_expectations=len(expectations))
    return evaluate_manifest(manifest, expectations)
