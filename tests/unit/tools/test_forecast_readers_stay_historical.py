"""Historical tooling includes superseded rows within each explicit data class.

Runtime-role integration tests prove projection and result behavior. These bounded
source checks retain the independent no-lifecycle-filter contract.
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


def _flatten(text: str) -> str:
    """Collapse every run of whitespace, so a predicate cannot hide behind a
    line break from a check that reads the query as one string."""
    return " ".join(text.split())


class TestTheDiagnosticExportStaysHistorical:
    def test_safe_projection_keeps_status_but_never_filters_lifecycle(self) -> None:
        import ast

        tree = ast.parse(_source("scripts/forecast_feed_resilience.py"))
        capture = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "capture_snapshot"
        )
        calls = [node for node in ast.walk(capture) if isinstance(node, ast.Call)]
        predicates = [
            ast.unparse(arg)
            for call in calls
            if isinstance(call.func, ast.Attribute) and call.func.attr == "where"
            for arg in call.args
        ]
        assert any("ForecastDataUse.STANDARD" in item for item in predicates)
        assert not any("forecasts.c.status" in item for item in predicates)
        # Real runtime-role output/history behavior is exercised in integration/tools.
        assert "forecasts.c.status" in ast.unparse(capture)


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
