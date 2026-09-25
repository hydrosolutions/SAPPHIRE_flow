"""Plan 328 T3 — the two DEVELOPER-TOOL readers of `forecasts`.

⚠️ **These assertions are SOURCE-LEVEL, and that is a deliberate compromise.**
`scripts/plan100_forecast_feed_resilience.py` and `tools/standing_snapshot.py`
are ad-hoc operator tools: one takes a live database and CLI date range, the
other an ssh target. Standing either up in a test would cost far more than the
disposition is worth, and would test the harness rather than the query.

What a source-level check CAN do is fail the moment someone adds a status
predicate to either query — which is the only way these two dispositions
change. ⛔ It cannot prove the tools behave correctly; the served surfaces
carry behavioural assertions instead
(`tests/integration/api/test_dashboard_forecasts.py`).

🔴 **Both checks are STRUCTURAL, not positional or line-based.** The first
version of this file sliced a fixed 600 characters and filtered by lines
containing the word `forecasts`, so a predicate added on the NEXT line — or
just past the slice — filtered the query while every assertion passed. That is
the defect Plan 328 exists to fix, reproduced in its own test. Each check now
extracts the WHOLE query (or union branch) and collapses whitespace before
looking at it, so a line break cannot hide anything.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]

#: `forecasts` as a whole word — never `weather_forecasts` or
#: `hindcast_forecasts`, which are different tables with their own dispositions.
_FORECASTS_TABLE = re.compile(r"(?<![a-z_])forecasts\b", re.IGNORECASE)


def _source(relative: str) -> str:
    return (_ROOT / relative).read_text(encoding="utf-8")


def _triple_quoted_blocks(source: str) -> list[str]:
    return re.findall(r'"""(.*?)"""', source, re.DOTALL)


def _flatten(text: str) -> str:
    """Collapse every run of whitespace, so a predicate cannot hide behind a
    line break from a check that reads the query as one string."""
    return " ".join(text.split())


class TestTheDiagnosticExportStaysUnfiltered:
    """`scripts/plan100_forecast_feed_resilience.py` — a forensic dump of an
    issue-time window. Hiding replaced rows is the opposite of what it is
    for: the blackout it investigates is precisely the kind of incident where
    you want to see that a forecast was replaced."""

    def test_the_blackout_query_has_no_status_predicate(self) -> None:
        blocks = [
            _flatten(block)
            for block in _triple_quoted_blocks(
                _source("scripts/plan100_forecast_feed_resilience.py")
            )
            if "FROM forecasts" in block and _FORECASTS_TABLE.search(block)
        ]

        assert blocks, "guard: the blackout query must still select from forecasts"
        for query in blocks:
            assert "status" not in query.lower(), (
                f"the diagnostic export must stay a HISTORICAL reader — a "
                f"status filter would hide replaced rows from an incident "
                f"dump. Offending query: {query}"
            )


class TestTheStandingSnapshotStaysUnfiltered:
    """`tools/standing_snapshot.py` — forecast row count and latest issue
    time. These are totals over the table; filtering them would make the
    snapshot disagree with the database it is reporting on."""

    def _forecast_branches(self) -> list[str]:
        """Every `union all` branch of `_COUNTER_SQL` that reads `forecasts`.

        🔑 Split on the UNION, not on newlines: a branch is the unit a `where`
        attaches to, and it may span any number of lines.
        """
        source = _source("tools/standing_snapshot.py")
        start = source.index("_COUNTER_SQL")
        opening = source.index('"""', start)
        counter_sql = source[opening + 3 : source.index('"""', opening + 3)]
        return [
            _flatten(branch)
            for branch in re.split(r"\bunion\s+all\b", counter_sql, flags=re.IGNORECASE)
            if _FORECASTS_TABLE.search(branch)
        ]

    def test_the_counter_query_has_no_status_predicate(self) -> None:
        branches = self._forecast_branches()

        # The two readers the inventory records: a row count and a MAX.
        assert any("count(*)" in branch.lower() for branch in branches), (
            "guard: the forecast row count must still be there"
        )
        assert any("max(issued_at)" in branch.lower() for branch in branches), (
            "guard: the latest-issue-time counter must still be there"
        )
        for branch in branches:
            assert "status" not in branch.lower(), (
                f"the standing snapshot's forecast total and latest issue "
                f"time are totals over ALL rows — a status filter would "
                f"misreport the table. Offending branch: {branch}"
            )
