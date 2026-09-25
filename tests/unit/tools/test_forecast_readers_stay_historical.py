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
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _source(relative: str) -> str:
    return (_ROOT / relative).read_text(encoding="utf-8")


class TestTheDiagnosticExportStaysUnfiltered:
    """`scripts/plan100_forecast_feed_resilience.py` — a forensic dump of an
    issue-time window. Hiding replaced rows is the opposite of what it is
    for: the blackout it investigates is precisely the kind of incident where
    you want to see that a forecast was replaced."""

    def test_the_blackout_query_has_no_status_predicate(self) -> None:
        source = _source("scripts/plan100_forecast_feed_resilience.py")
        start = source.index("blackout_forecasts")
        query = source[start : start + 600]

        assert "FROM forecasts" in query
        assert "status" not in query, (
            "the diagnostic export must stay a HISTORICAL reader — a status "
            "filter here would hide replaced rows from an incident dump"
        )


class TestTheStandingSnapshotStaysUnfiltered:
    """`tools/standing_snapshot.py` — forecast row count and latest issue
    time. These are totals over the table; filtering them would make the
    snapshot disagree with the database it is reporting on."""

    def test_the_counter_query_has_no_status_predicate(self) -> None:
        source = _source("tools/standing_snapshot.py")
        start = source.index("_COUNTER_SQL")
        opening = source.index('"""', start)
        counter_sql = source[start : source.index('"""', opening + 3)]

        assert "count(*)::text from forecasts" in counter_sql
        assert "max(issued_at)" in counter_sql
        forecast_lines = [
            line
            for line in counter_sql.splitlines()
            if "forecasts" in line and "weather_forecasts" not in line
        ]
        assert forecast_lines, "guard: the forecast counters must still be there"
        assert not any("status" in line for line in forecast_lines), (
            "the standing snapshot's forecast total and latest issue time are "
            "totals over ALL rows — a status filter would misreport the table"
        )
