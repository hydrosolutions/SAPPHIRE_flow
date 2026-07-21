from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from sapphire_flow.exceptions import AdapterError
from sapphire_flow.types.datetime import ensure_utc

# Red-first guard: the collector-local adapter module (T2) does not exist yet
# at the start of this task. Import it lazily inside each test via this
# helper so a missing module surfaces as a genuine pytest.fail() ASSERTION,
# never a collection-time ImportError.


def _import_adapter() -> object:
    try:
        from sapphire_flow.adapters import bafu_observation

        return bafu_observation
    except ImportError as exc:  # pragma: no cover - red-first guard
        pytest.fail(
            "sapphire_flow.adapters.bafu_observation does not exist yet "
            f"(T2 not implemented): {exc}"
        )


_ENDPOINT = "https://lindas.admin.ch/query"
_DIM = "https://environment.ld.admin.ch/foen/hydro/dimension"
_RIVER_BASE = "https://environment.ld.admin.ch/foen/hydro/river/observation"
_LAKE_BASE = "https://environment.ld.admin.ch/foen/hydro/lake/observation"


def _binding(subject: str, predicate: str, obj: str) -> dict[str, dict[str, str]]:
    return {
        "subject": {"type": "uri", "value": subject},
        "predicate": {"type": "uri", "value": predicate},
        "object": {"type": "literal", "value": obj},
    }


def _river_triples(code: str, ts: str = "2026-07-21T15:00:00Z") -> list[dict]:
    subject = f"{_RIVER_BASE}/{code}"
    return [
        _binding(subject, f"{_DIM}/measurementTime", ts),
        _binding(subject, f"{_DIM}/discharge", "12.3"),
        _binding(subject, f"{_DIM}/waterLevel", "372.1"),
        _binding(subject, f"{_DIM}/waterTemperature", "18.5"),
    ]


def _lake_triples(code: str, ts: str = "2026-07-21T15:00:00Z") -> list[dict]:
    subject = f"{_LAKE_BASE}/{code}"
    return [
        _binding(subject, f"{_DIM}/measurementTime", ts),
        _binding(subject, f"{_DIM}/waterLevel", "394.2"),
    ]


def _sparql_response(bindings: list[dict]) -> dict[str, object]:
    return {
        "head": {"vars": ["subject", "predicate", "object"]},
        "results": {"bindings": bindings},
    }


def _make_adapter(handler):  # type: ignore[no-untyped-def]
    module = _import_adapter()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return module.BafuObservationAdapter(endpoint=_ENDPOINT, http_client=client)  # type: ignore[attr-defined]


class TestFetchAllObservations:
    def test_multiple_subjects_are_grouped_not_merged(self) -> None:
        bindings = _river_triples("2135") + _river_triples("2200")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_sparql_response(bindings))

        adapter = _make_adapter(handler)
        rows = adapter.fetch_all_observations()

        codes = {row.gauge_code for row in rows}
        assert codes == {"2135", "2200"}, (
            "subjects must be grouped per-gauge, not merged into one row"
        )
        # 3 params per river subject x 2 subjects = 6 rows
        assert len(rows) == 6

    def test_lake_subject_yields_water_level_only(self) -> None:
        bindings = _lake_triples("3001")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_sparql_response(bindings))

        adapter = _make_adapter(handler)
        rows = adapter.fetch_all_observations()

        assert len(rows) == 1
        assert rows[0].parameter == "water_level"
        assert rows[0].lindas_kind == "lake"
        assert rows[0].gauge_code == "3001"

    def test_river_subject_yields_all_three_parameters(self) -> None:
        bindings = _river_triples("2135")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_sparql_response(bindings))

        adapter = _make_adapter(handler)
        rows = adapter.fetch_all_observations()

        params = {row.parameter for row in rows}
        assert params == {"discharge", "water_level", "water_temperature"}
        assert all(row.lindas_kind == "river" for row in rows)
        assert all(row.gauge_code == "2135" for row in rows)

    def test_measurement_time_parsed_as_utc(self) -> None:
        bindings = _river_triples("2135", ts="2026-07-21T15:00:00Z")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_sparql_response(bindings))

        adapter = _make_adapter(handler)
        rows = adapter.fetch_all_observations()

        expected = ensure_utc(datetime(2026, 7, 21, 15, 0, tzinfo=UTC))
        assert all(row.measurement_time == expected for row in rows)

    def test_same_numeric_code_under_river_and_lake_kept_distinct(self) -> None:
        # The 2004-class collision: the same gauge code appears under BOTH
        # URI paths with DIFFERENT parameter sets. (gauge_code, lindas_kind)
        # must keep them distinct, never collapsed into one identity.
        bindings = _river_triples("2004") + _lake_triples("2004")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_sparql_response(bindings))

        adapter = _make_adapter(handler)
        rows = adapter.fetch_all_observations()

        river_rows = [r for r in rows if r.lindas_kind == "river"]
        lake_rows = [r for r in rows if r.lindas_kind == "lake"]
        assert {r.gauge_code for r in river_rows} == {"2004"}
        assert {r.gauge_code for r in lake_rows} == {"2004"}
        assert {r.parameter for r in river_rows} == {
            "discharge",
            "water_level",
            "water_temperature",
        }
        assert {r.parameter for r in lake_rows} == {"water_level"}
        # 3 (river) + 1 (lake) = 4 total, not collapsed/deduped across kinds.
        assert len(rows) == 4

    def test_unmapped_predicate_raises_schema_drift_error(self) -> None:
        subject = f"{_RIVER_BASE}/2135"
        bindings = [
            _binding(subject, f"{_DIM}/measurementTime", "2026-07-21T15:00:00Z"),
            _binding(subject, f"{_DIM}/someNewPredicate", "1.0"),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_sparql_response(bindings))

        adapter = _make_adapter(handler)
        with pytest.raises(AdapterError):
            adapter.fetch_all_observations()

    def test_truncated_fetch_at_limit_raises(self) -> None:
        module = _import_adapter()
        limit = module._QUERY_LIMIT  # type: ignore[attr-defined]
        # Build `limit` bindings all sharing one subject/predicate/timestamp —
        # content doesn't matter, only that len(bindings) >= LIMIT is hit.
        subject = f"{_RIVER_BASE}/2135"
        bindings = [
            _binding(subject, f"{_DIM}/measurementTime", "2026-07-21T15:00:00Z")
            for _ in range(limit)
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_sparql_response(bindings))

        adapter = _make_adapter(handler)
        with pytest.raises(AdapterError, match="LIMIT"):
            adapter.fetch_all_observations()

    def test_query_selects_subject_predicate_object_no_bind(self) -> None:
        # DC-1 lock: the whole-graph query MUST project ?subject (not BIND a
        # single one). This is the structural difference from
        # hydro_scraper.py's per-station _build_sparql_query.
        module = _import_adapter()
        query = module.BafuObservationAdapter._build_query()  # type: ignore[attr-defined]
        assert "SELECT ?subject ?predicate ?object" in query
        assert "BIND" not in query
