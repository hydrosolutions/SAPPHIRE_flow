"""Plan 238 T2 — § M-A11c's embedded report block is bound to
`scripts/dhm_precip/ifs_event_timing.py`'s output (D2).

The document embeds `format_report()`'s exact output inside a delimited
block (`<!-- m-a11c-report:start -->` ... `<!-- m-a11c-report:end -->`
wrapping a ```text fence). Binding compares that block against a freshly
computed report line-for-line, ignoring only whitespace — so it covers the
frozen CONVENTION and the input digests printed inside the block, not only
the observed/null/increment table (D2's second finding: table equality alone
cannot prove that editing a default fails, because a default can change
without moving a displayed, rounded number — this compares every line,
including the ones that do not round).

Two tests prove the binding fails in each direction WITHOUT touching the
checked-in file: `test_perturbing_a_table_cell_fails_closed` mutates a copy
of the document text in memory, and
`test_changing_a_frozen_default_fails_the_binding` renders a report built
from an altered default. Both call `assert_document_matches_report` on data
that never touches disk.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.dhm_precip.ifs_event_timing import (
    CONTINUOUS_DAY_LABEL,
    CONTINUOUS_DAY_LEADS,
    DEFAULT_INIT_HOUR,
    DEFAULT_NULL_SHIFT_DAYS,
    DEFAULT_SEARCH_WINDOWS_H,
    DEFAULT_SEASONS,
    DEFAULT_TIGGE_ROOT,
    EventTimingParams,
    build_cells,
    build_report,
    consumed_input_digests,
    format_report,
    tigge_series_paths,
)
from scripts.dhm_precip.tigge_gauge_timing import MIN_HARMONIC_AMPLITUDE

DOC_PATH = Path("docs/design/dhm-precipitation-m-a11-tigge-ifs-screening.md")
START_MARKER = "<!-- m-a11c-report:start -->"
END_MARKER = "<!-- m-a11c-report:end -->"
_FENCE = re.compile(r"```text\n(.*?)\n```", re.DOTALL)


class DocumentBindingError(Exception):
    """Fail closed — a missing marker, a missing fence, or a content
    mismatch are all reported distinctly rather than silently ignored."""


def extract_embedded_block(doc_text: str) -> str:
    start = doc_text.find(START_MARKER)
    end = doc_text.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        raise DocumentBindingError(
            f"{START_MARKER!r}/{END_MARKER!r} markers not found, in order, "
            "in the document"
        )
    between = doc_text[start + len(START_MARKER) : end]
    match = _FENCE.search(between)
    if match is None:
        raise DocumentBindingError(
            "no ```text fence found between the m-a11c-report markers"
        )
    return match.group(1)


def _normalize(text: str) -> list[str]:
    """Key on stable headings, row labels and numeric cells; ignore
    emphasis and whitespace (D2) — collapse whitespace runs within a line,
    keep blank lines as section-break landmarks."""
    return [" ".join(line.split()) for line in text.splitlines()]


def assert_document_matches_report(doc_text: str, rendered: str) -> None:
    embedded = _normalize(extract_embedded_block(doc_text))
    fresh = _normalize(rendered)
    if embedded != fresh:
        raise DocumentBindingError(
            "§ M-A11c's embedded report block does not match a freshly "
            f"rendered report.\n--- document ---\n{embedded}\n--- fresh ---\n{fresh}"
        )


def _production_inputs_available() -> bool:
    xlsx = os.environ.get("DHM_PRECIP_XLSX")
    if not xlsx or not Path(xlsx).exists():
        return False
    return all(
        path.exists()
        for path in tigge_series_paths(
            tigge_root=DEFAULT_TIGGE_ROOT, seasons=DEFAULT_SEASONS
        )
    )


requires_production_inputs = pytest.mark.skipif(
    not _production_inputs_available(),
    reason=(
        "DHM_PRECIP_XLSX and the six TIGGE season parquet files under "
        f"{DEFAULT_TIGGE_ROOT} must be present locally"
    ),
)


def _production_report_text() -> str:
    base = EventTimingParams(search_window_h=DEFAULT_SEARCH_WINDOWS_H[0])
    cells = build_cells(
        tigge_root=DEFAULT_TIGGE_ROOT,
        seasons=DEFAULT_SEASONS,
        leads=CONTINUOUS_DAY_LEADS,
        init_hour=DEFAULT_INIT_HOUR,
        params=base,
    )
    digests = consumed_input_digests(
        tigge_root=DEFAULT_TIGGE_ROOT, seasons=DEFAULT_SEASONS
    )
    result = build_report(
        cells,
        windows_h=DEFAULT_SEARCH_WINDOWS_H,
        base=base,
        leads=CONTINUOUS_DAY_LEADS,
        lead_label=CONTINUOUS_DAY_LABEL,
        init_hour=DEFAULT_INIT_HOUR,
        seasons=DEFAULT_SEASONS,
        null_shift_days=DEFAULT_NULL_SHIFT_DAYS,
        min_amplitude=MIN_HARMONIC_AMPLITUDE,
        input_digests=digests,
    )
    stations = sorted({str(cell.station) for cell in cells})
    return format_report(result, n_stations=len(stations), n_cells=len(cells))


@requires_production_inputs
def test_the_document_matches_the_module_on_production_data() -> None:
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    assert_document_matches_report(doc_text, _production_report_text())


@requires_production_inputs
def test_changing_a_frozen_default_fails_the_binding() -> None:
    """D2/D3 — perturbing ONE frozen default (here `miss_fraction`, the
    knob D3 names explicitly) must fail the binding even though it only
    changes the `missed` column and the printed convention line, proving
    the comparison covers the convention block, not only the table."""
    base = EventTimingParams(
        search_window_h=DEFAULT_SEARCH_WINDOWS_H[0], miss_fraction=0.45
    )
    cells = build_cells(
        tigge_root=DEFAULT_TIGGE_ROOT,
        seasons=DEFAULT_SEASONS,
        leads=CONTINUOUS_DAY_LEADS,
        init_hour=DEFAULT_INIT_HOUR,
        params=base,
    )
    digests = consumed_input_digests(
        tigge_root=DEFAULT_TIGGE_ROOT, seasons=DEFAULT_SEASONS
    )
    altered = build_report(
        cells,
        windows_h=DEFAULT_SEARCH_WINDOWS_H,
        base=base,
        leads=CONTINUOUS_DAY_LEADS,
        lead_label=CONTINUOUS_DAY_LABEL,
        init_hour=DEFAULT_INIT_HOUR,
        seasons=DEFAULT_SEASONS,
        null_shift_days=DEFAULT_NULL_SHIFT_DAYS,
        min_amplitude=MIN_HARMONIC_AMPLITUDE,
        input_digests=digests,
    )
    stations = sorted({str(cell.station) for cell in cells})
    rendered = format_report(altered, n_stations=len(stations), n_cells=len(cells))
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    with pytest.raises(DocumentBindingError):
        assert_document_matches_report(doc_text, rendered)


def test_perturbing_a_table_cell_fails_closed() -> None:
    """Needs no production data: the document's own current, correct block
    is the fresh side; a copy of the document with one digit changed is the
    perturbed side."""
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    rendered = extract_embedded_block(doc_text)
    mutated_doc = doc_text.replace(
        "exact window      0.179", "exact window      0.999", 1
    )
    assert mutated_doc != doc_text, "fixture value not found — update this test"
    with pytest.raises(DocumentBindingError):
        assert_document_matches_report(mutated_doc, rendered)


def test_missing_markers_fail_closed_with_a_distinct_error() -> None:
    with pytest.raises(DocumentBindingError, match="markers not found"):
        extract_embedded_block("no markers here")


def test_missing_fence_fails_closed_with_a_distinct_error() -> None:
    with pytest.raises(DocumentBindingError, match="no ```text fence"):
        extract_embedded_block(f"{START_MARKER}\nno fence here\n{END_MARKER}")


class TestSyntheticDefaultPerturbation:
    """A non-gated proof the comparison mechanism itself discriminates a
    changed default, independent of production data being present. The
    real-data proof is `test_changing_a_frozen_default_fails_the_binding`."""

    def test_a_different_report_never_matches_a_fixed_reference_block(self) -> None:
        reference = (
            f"{START_MARKER}\n```text\nwindow  events  matched  statistic  "
            f"observed\n ±12 h    10    5  exact window      0.500\n```\n{END_MARKER}"
        )
        base = EventTimingParams(search_window_h=24)  # a different window
        altered_base = replace(base, miss_fraction=0.9)
        result = build_report(
            [],
            windows_h=(24,),
            base=altered_base,
            leads=CONTINUOUS_DAY_LEADS,
            lead_label=CONTINUOUS_DAY_LABEL,
            init_hour=DEFAULT_INIT_HOUR,
            seasons=DEFAULT_SEASONS,
            null_shift_days=(1,),
            min_amplitude=MIN_HARMONIC_AMPLITUDE,
            input_digests={},
        )
        rendered = format_report(result, n_stations=0, n_cells=0)
        with pytest.raises(DocumentBindingError):
            assert_document_matches_report(reference, rendered)


if __name__ == "__main__":
    pytest.main([__file__])
