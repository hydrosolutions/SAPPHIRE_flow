"""Plan 238 T2 — § M-A11c's embedded report is bound to
`scripts/dhm_precip/ifs_event_timing.py`'s output (D2).

The document embeds BOTH of the module's blocks between
`<!-- m-a11c-report:start -->` and `<!-- m-a11c-report:end -->`: a ```text
fence holding `format_report()` (what a human reads) and a ```json fence
holding `render_machine_block()` (what this binding actually checks).

🔴 The two are compared differently, on purpose, because the first review of
this binding defeated it by ROUNDING: with a text-only comparison, changing
`miss_fraction` from 0.5 to 0.5000001 on the real inputs left every printed
number identical (`.3f` results, `:g` convention) and the binding passed. The
same comparison meanwhile REJECTED a harmless re-wrap of one long line — the
tolerance was exactly backwards. So:

* the JSON block is compared as PARSED VALUES at full precision, so a drift
  of 1e-7 in a default fails
  (`test_a_default_drift_below_the_printed_precision_fails_the_binding`), and
* the text block is compared whitespace-insensitively across the whole block,
  so re-wrapping a line passes
  (`test_rewrapping_a_line_in_the_text_block_still_matches`) while any changed
  digit or word fails.

Both directions are proven WITHOUT touching the checked-in file: the document
side is mutated in memory, the module side is rendered from an altered
default.
"""

from __future__ import annotations

import json
import math
import os
import re
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
    EventTimingReport,
    StationSeasonCell,
    build_cells,
    build_report,
    consumed_input_digests,
    format_report,
    render_machine_block,
    report_payload,
    tigge_series_paths,
)
from scripts.dhm_precip.tigge_gauge_timing import MIN_HARMONIC_AMPLITUDE

DOC_PATH = Path("docs/design/dhm-precipitation-m-a11-tigge-ifs-screening.md")
START_MARKER = "<!-- m-a11c-report:start -->"
END_MARKER = "<!-- m-a11c-report:end -->"
_TEXT_FENCE = re.compile(r"```text\n(.*?)\n```", re.DOTALL)
_JSON_FENCE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)


class DocumentBindingError(Exception):
    """Fail closed — a missing marker, a missing or duplicated fence, an
    unparseable JSON block and a content mismatch are all reported
    distinctly rather than silently ignored."""


def _between_markers(doc_text: str) -> str:
    start = doc_text.find(START_MARKER)
    end = doc_text.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        raise DocumentBindingError(
            f"{START_MARKER!r}/{END_MARKER!r} markers not found, in order, "
            "in the document"
        )
    return doc_text[start + len(START_MARKER) : end]


def _one_fence(region: str, pattern: re.Pattern[str], *, language: str) -> str:
    matches = pattern.findall(region)
    if len(matches) != 1:
        raise DocumentBindingError(
            f"expected exactly one ```{language} fence between the "
            f"m-a11c-report markers, found {len(matches)}"
        )
    return str(matches[0])


def extract_text_block(doc_text: str) -> str:
    return _one_fence(_between_markers(doc_text), _TEXT_FENCE, language="text")


def extract_payload(doc_text: str) -> object:
    raw = _one_fence(_between_markers(doc_text), _JSON_FENCE, language="json")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise DocumentBindingError(
            "the ```json fence between the m-a11c-report markers is not "
            f"parseable JSON: {error}"
        ) from error


def _flatten(text: str) -> str:
    """Whitespace-insensitive across the WHOLE block: line breaks and column
    padding are layout, not content, so re-wrapping a long line must not fail
    the binding. Any changed digit or word still does."""
    return " ".join(text.split())


def _canonical(value: object) -> object:
    """NaN is the one value JSON round-trips to something that does not
    compare equal to itself; nothing else is normalised, so float comparison
    stays EXACT (D2 — the point is that a 1e-7 drift is not tolerated)."""
    if isinstance(value, float) and math.isnan(value):
        return "NaN"
    if isinstance(value, dict):
        items: dict[str, object] = value  # pyright: ignore[reportUnknownVariableType]
        return {key: _canonical(item) for key, item in items.items()}
    if isinstance(value, list):
        entries: list[object] = value  # pyright: ignore[reportUnknownVariableType]
        return [_canonical(item) for item in entries]
    return value


def assert_document_matches(doc_text: str, *, text_block: str, payload: object) -> None:
    """🔴 The VALUE check is the binding; the text check only keeps the human
    block honest about the same numbers."""
    embedded_payload = _canonical(extract_payload(doc_text))
    fresh_payload = _canonical(payload)
    if embedded_payload != fresh_payload:
        raise DocumentBindingError(
            "§ M-A11c's embedded JSON block does not match a freshly computed "
            f"report payload.\n--- document ---\n{embedded_payload}\n"
            f"--- fresh ---\n{fresh_payload}"
        )
    embedded_text = _flatten(extract_text_block(doc_text))
    if embedded_text != _flatten(text_block):
        raise DocumentBindingError(
            "§ M-A11c's embedded text block does not match a freshly rendered "
            f"report.\n--- document ---\n{embedded_text}\n"
            f"--- fresh ---\n{_flatten(text_block)}"
        )


def assert_document_matches_report(
    doc_text: str, report: EventTimingReport, *, n_stations: int, n_cells: int
) -> None:
    assert_document_matches(
        doc_text,
        text_block=format_report(report, n_stations=n_stations, n_cells=n_cells),
        payload=report_payload(report),
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


@pytest.fixture(scope="module")
def production_cells() -> list[StationSeasonCell]:
    """Built once: `build_cells` does not depend on `miss_fraction`, and each
    of the production tests below would otherwise re-read the workbook."""
    return build_cells(
        tigge_root=DEFAULT_TIGGE_ROOT,
        seasons=DEFAULT_SEASONS,
        leads=CONTINUOUS_DAY_LEADS,
        init_hour=DEFAULT_INIT_HOUR,
        params=EventTimingParams(search_window_h=DEFAULT_SEARCH_WINDOWS_H[0]),
    )


def _production_report(
    cells: list[StationSeasonCell], *, base: EventTimingParams
) -> EventTimingReport:
    return build_report(
        cells,
        windows_h=DEFAULT_SEARCH_WINDOWS_H,
        base=base,
        leads=CONTINUOUS_DAY_LEADS,
        lead_label=CONTINUOUS_DAY_LABEL,
        init_hour=DEFAULT_INIT_HOUR,
        seasons=DEFAULT_SEASONS,
        null_shift_days=DEFAULT_NULL_SHIFT_DAYS,
        min_amplitude=MIN_HARMONIC_AMPLITUDE,
        input_digests=consumed_input_digests(
            tigge_root=DEFAULT_TIGGE_ROOT, seasons=DEFAULT_SEASONS
        ),
    )


@requires_production_inputs
def test_the_document_matches_the_module_on_production_data(
    production_cells: list[StationSeasonCell],
) -> None:
    report = _production_report(
        production_cells,
        base=EventTimingParams(search_window_h=DEFAULT_SEARCH_WINDOWS_H[0]),
    )
    assert_document_matches_report(
        DOC_PATH.read_text(encoding="utf-8"),
        report,
        n_stations=len({str(cell.station) for cell in production_cells}),
        n_cells=len(production_cells),
    )


@requires_production_inputs
def test_a_default_drift_below_the_printed_precision_fails_the_binding(
    production_cells: list[StationSeasonCell],
) -> None:
    """🔴 The regression this binding was rewritten for, and the probe D2
    actually requires: a changed default that moves NO published figure must
    still fail closed.

    `miss_fraction` 0.5 → 0.5000001 leaves every printed number identical —
    asserted here, not assumed — because the results round to `.3f` and the
    convention line prints with `:g`. The previous text-only binding passed.
    """
    drifted = _production_report(
        production_cells,
        base=EventTimingParams(
            search_window_h=DEFAULT_SEARCH_WINDOWS_H[0], miss_fraction=0.5000001
        ),
    )
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    rendered = format_report(
        drifted,
        n_stations=len({str(cell.station) for cell in production_cells}),
        n_cells=len(production_cells),
    )
    assert rendered == extract_text_block(doc_text), (
        "the probe no longer moves zero printed numbers — pick a smaller drift"
    )
    with pytest.raises(DocumentBindingError, match="JSON block"):
        assert_document_matches(
            doc_text, text_block=rendered, payload=report_payload(drifted)
        )


@requires_production_inputs
def test_changing_a_frozen_default_fails_the_binding(
    production_cells: list[StationSeasonCell],
) -> None:
    """D2/D3 — the coarse direction of the same property: a default change
    large enough to move the table (`miss_fraction` 0.5 → 0.45) fails too."""
    altered = _production_report(
        production_cells,
        base=EventTimingParams(
            search_window_h=DEFAULT_SEARCH_WINDOWS_H[0], miss_fraction=0.45
        ),
    )
    with pytest.raises(DocumentBindingError):
        assert_document_matches_report(
            DOC_PATH.read_text(encoding="utf-8"),
            altered,
            n_stations=len({str(cell.station) for cell in production_cells}),
            n_cells=len(production_cells),
        )


def _document_as_its_own_fresh_side() -> tuple[str, str, object]:
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    return doc_text, extract_text_block(doc_text), extract_payload(doc_text)


def test_perturbing_a_table_cell_fails_closed() -> None:
    """Needs no production data: the document's own current, correct blocks
    are the fresh side; a copy with one digit changed is the perturbed side."""
    doc_text, text_block, payload = _document_as_its_own_fresh_side()
    mutated = doc_text.replace("exact window      0.179", "exact window      0.999", 1)
    assert mutated != doc_text, "fixture value not found — update this test"
    with pytest.raises(DocumentBindingError, match="text block"):
        assert_document_matches(mutated, text_block=text_block, payload=payload)


def test_perturbing_a_json_value_fails_closed() -> None:
    """The same, on the block that carries the authority — and at a precision
    the human table cannot show."""
    doc_text, text_block, payload = _document_as_its_own_fresh_side()
    mutated = doc_text.replace('"miss_fraction":0.5,', '"miss_fraction":0.5000001,', 1)
    assert mutated != doc_text, "fixture value not found — update this test"
    with pytest.raises(DocumentBindingError, match="JSON block"):
        assert_document_matches(mutated, text_block=text_block, payload=payload)


def test_rewrapping_a_line_in_the_text_block_still_matches() -> None:
    """⚠️ The tolerance the first binding had backwards: harmless reflow must
    NOT fail, while value drift must."""
    doc_text, text_block, payload = _document_as_its_own_fresh_side()
    long_line = next(
        line for line in text_block.splitlines() if line.startswith("uncertainty: ")
    )
    head, _, tail = long_line.partition(" — ")
    rewrapped = doc_text.replace(long_line, f"{head}\n  — {tail}", 1)
    assert rewrapped != doc_text, "fixture line not found — update this test"
    assert_document_matches(rewrapped, text_block=text_block, payload=payload)


def test_missing_markers_fail_closed_with_a_distinct_error() -> None:
    with pytest.raises(DocumentBindingError, match="markers not found"):
        extract_text_block("no markers here")


def test_missing_fence_fails_closed_with_a_distinct_error() -> None:
    with pytest.raises(DocumentBindingError, match="exactly one ```text fence"):
        extract_text_block(f"{START_MARKER}\nno fence here\n{END_MARKER}")


def test_unparseable_json_fails_closed_with_a_distinct_error() -> None:
    with pytest.raises(DocumentBindingError, match="not parseable JSON"):
        extract_payload(f"{START_MARKER}\n```json\n{{oops\n```\n{END_MARKER}")


class TestSyntheticDefaultPerturbation:
    """The same properties without production data, so the mechanism stays
    covered wherever the suite runs (`data/` is gitignored, so CI skips every
    gated test above)."""

    def _report(
        self, *, base: EventTimingParams, min_amplitude: float = MIN_HARMONIC_AMPLITUDE
    ) -> EventTimingReport:
        return build_report(
            [],
            windows_h=(24,),
            base=base,
            leads=CONTINUOUS_DAY_LEADS,
            lead_label=CONTINUOUS_DAY_LABEL,
            init_hour=DEFAULT_INIT_HOUR,
            seasons=DEFAULT_SEASONS,
            null_shift_days=(1,),
            min_amplitude=min_amplitude,
            input_digests={},
        )

    def _document(self, report: EventTimingReport) -> str:
        text = format_report(report, n_stations=0, n_cells=0)
        machine = render_machine_block(report)
        return (
            f"{START_MARKER}\n```text\n{text}\n```\n"
            f"```json\n{machine}\n```\n{END_MARKER}"
        )

    def test_an_unchanged_report_matches_its_own_rendered_document(self) -> None:
        report = self._report(base=EventTimingParams(search_window_h=24))
        assert_document_matches_report(
            self._document(report), report, n_stations=0, n_cells=0
        )

    def test_a_default_that_prints_identically_still_fails(self) -> None:
        """`min_amplitude` 0.05 → 0.050000001 prints as `0.05` under `:g` and
        moves no result, so ONLY the value comparison can catch it."""
        base = EventTimingParams(search_window_h=24)
        published = self._report(base=base)
        drifted = self._report(base=base, min_amplitude=0.050000001)
        assert format_report(drifted, n_stations=0, n_cells=0) == format_report(
            published, n_stations=0, n_cells=0
        )
        with pytest.raises(DocumentBindingError, match="JSON block"):
            assert_document_matches_report(
                self._document(published), drifted, n_stations=0, n_cells=0
            )

    def test_a_different_report_never_matches_a_fixed_reference_block(self) -> None:
        report = self._report(
            base=EventTimingParams(search_window_h=24, miss_fraction=0.9)
        )
        reference = (
            f"{START_MARKER}\n```text\nwindow  events  matched  statistic  "
            "observed\n ±12 h    10    5  exact window      0.500\n```\n"
            '```json\n{"convention":{},"input_digests":{},"rows":[]}\n```\n'
            f"{END_MARKER}"
        )
        with pytest.raises(DocumentBindingError):
            assert_document_matches_report(reference, report, n_stations=0, n_cells=0)


if __name__ == "__main__":
    pytest.main([__file__])
