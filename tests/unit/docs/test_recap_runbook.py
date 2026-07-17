"""Plan 082 Task 4A: the recap Gateway operations runbook covers every
required operator anchor as a HEADING/section — not a bare substring match
that a stray body-text mention could satisfy accidentally."""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNBOOK_PATH = _REPO_ROOT / "docs" / "operations" / "recap-gateway-runbook.md"

_REQUIRED_ANCHORS = [
    "RECAP_API_KEY",
    "coverage manifest",
    "historical back-extraction",
    "config_error",
    "all_unmappable",
    "auth",
    "source_data_missing",
    "live_recap",
    "snow",
]


def _headings(text: str) -> list[str]:
    return [
        line.lstrip("#").strip()
        for line in text.splitlines()
        if re.match(r"^#{1,6}\s", line)
    ]


class TestRunbookSections:
    def test_runbook_exists(self) -> None:
        assert _RUNBOOK_PATH.is_file(), f"missing runbook: {_RUNBOOK_PATH}"

    def test_every_required_anchor_appears_as_a_heading(self) -> None:
        text = _RUNBOOK_PATH.read_text()
        headings = _headings(text)
        headings_lower = [h.lower() for h in headings]
        for anchor in _REQUIRED_ANCHORS:
            assert any(anchor.lower() in h for h in headings_lower), (
                f"anchor {anchor!r} not found as a heading; headings were: {headings}"
            )

    def test_all_four_nwp_delivery_reasons_present_as_headings(self) -> None:
        text = _RUNBOOK_PATH.read_text()
        headings_lower = [h.lower() for h in _headings(text)]
        reasons = ["config_error", "all_unmappable", "auth", "source_data_missing"]
        for reason in reasons:
            assert any(reason in h for h in headings_lower), (
                f"NWP_DELIVERY reason {reason!r} not found as a heading"
            )
