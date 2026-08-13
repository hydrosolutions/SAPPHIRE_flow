"""Task 4c — the final asserting gate (M-A1 exit).

Constraint 1: "the *only* skip condition is `DHM_PRECIP_XLSX` unset,
applied by the integration test alone." No other module in
`scripts/dhm_precip/` contains pytest semantics — a missing path, digest
mismatch, schema mismatch or parse failure is a runner exit code, never a
skip (see `scripts/dhm_precip/run.py`'s docstring).

Exit gate (from the plan): every `active` and `corrected` expectation
matches under D5, and every `withdrawn_unreproducible` one carries a
complete Phase-4 record (D8c) — the latter is also enforced structurally by
`ExpectationModel` at load time (`expectations.py`), so `load_expectations`
succeeding at all is itself part of the gate.
"""

from __future__ import annotations

import os

import pytest

from scripts.dhm_precip.evaluate import run_report

pytestmark = pytest.mark.skipif(
    not os.environ.get("DHM_PRECIP_XLSX"),
    reason="DHM_PRECIP_XLSX unset — the only skip condition here (constraint 1)",
)


def test_active_and_corrected_expectations_match_under_d5(tmp_path) -> None:
    report = run_report(tmp_path / "m_a1_out")

    asserted = [
        d for d in report.discrepancies if d.disposition in ("active", "corrected")
    ]
    assert asserted, (
        "expected at least one active/corrected expectation to assert against"
    )

    failures = [
        f"{d.expectation_id} ({d.disposition}): expected {d.expected!r}, "
        f"got {d.actual!r} — {d.reason}"
        for d in asserted
        if not d.matched
    ]
    assert not failures, "M-A1 reproduction gate failed:\n" + "\n".join(failures)


def test_withdrawn_unreproducible_expectations_are_exempt_but_recorded(
    tmp_path,
) -> None:
    report = run_report(tmp_path / "m_a1_out")

    withdrawn = [
        d for d in report.discrepancies if d.disposition == "withdrawn_unreproducible"
    ]
    # D8c: exempt from numeric matching — this must hold regardless of the
    # actual value (compare_expectation's own unit tests lock the mechanism;
    # this integration test confirms it holds for the real manifest too).
    assert all(d.matched for d in withdrawn)


def test_every_withdrawn_expectation_has_a_complete_phase4_record() -> None:
    import tomllib

    from scripts.dhm_precip.expectations import DEFAULT_EXPECTATIONS_PATH

    raw = tomllib.loads(DEFAULT_EXPECTATIONS_PATH.read_text())
    for entry in raw["expectation"]:
        if entry.get("disposition") != "withdrawn_unreproducible":
            continue
        has_original = (
            entry.get("original_value") is not None
            or entry.get("original_range") is not None
        )
        assert has_original, f"{entry['id']}: missing original_value/original_range"
        assert entry.get("method_comparison"), (
            f"{entry['id']}: missing method_comparison"
        )
        assert entry.get("evidence"), f"{entry['id']}: missing evidence"
        assert entry.get("successor"), f"{entry['id']}: missing successor"
