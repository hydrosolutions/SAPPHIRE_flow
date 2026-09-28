"""Plan 402 T4 — the committed map contract must never drift from
`api/map_contract.py::build_map_openapi()` (Plan 198 D15's mechanism)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from sapphire_flow.api.map_contract import MAP_CONTRACT_VERSION, build_map_openapi
from sapphire_flow.api.schemas import (
    ForecastQcRuleSetResponse,
    ObservationQcRuleSetResponse,
    QcRulesResponse,
    StationObservationQcRuleResponse,
    StationObservationQcRuleSetResponse,
    StationQcRulesResponse,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTRACT_PATH = _REPO_ROOT / "docs/spec/api-v1-map.openapi.json"

_EXPECTED_PATHS = {
    "/api/v1/stations",
    "/api/v1/stations/{station_id}",
    "/api/v1/stations/{station_id}/observations",
    "/api/v1/stations/{station_id}/forecasts",
    "/api/v1/forecasts/{forecast_id}",
    "/api/v1/qc/rules",
    "/api/v1/stations/{station_id}/skill",
}


def _load_committed() -> dict[str, Any]:
    return json.loads(_CONTRACT_PATH.read_text())


class TestCommittedContractMatchesGenerated:
    def test_committed_file_equals_generated_document(self) -> None:
        committed = _load_committed()
        generated = build_map_openapi()
        assert committed == generated, (
            "docs/spec/api-v1-map.openapi.json has drifted from "
            "build_map_openapi() — regenerate it with "
            "`uv run python tools/generate_map_contract.py` and bump "
            "MAP_CONTRACT_VERSION per D14."
        )

    def test_route_list_holds_no_other_route(self) -> None:
        committed = _load_committed()
        assert set(committed["paths"].keys()) == _EXPECTED_PATHS

    def test_every_operation_carries_the_bearer_security_requirement(self) -> None:
        committed = _load_committed()
        for path, path_item in committed["paths"].items():
            for method, operation in path_item.items():
                assert operation.get("security") == [{"bearerAuth": []}], (
                    f"{method.upper()} {path} is missing the bearer "
                    "security requirement"
                )

    def test_info_version_is_the_d14_constant(self) -> None:
        committed = _load_committed()
        assert committed["info"]["version"] == MAP_CONTRACT_VERSION


class TestQcRulesResponseMatchesExactlyOneBranch:
    """A `/qc/rules` response validates against the committed operation's
    200 schema, and — since that schema is a `oneOf` with a `scope`
    discriminator — matches exactly ONE branch (jsonschema's `oneOf` raises
    on 0 or on 2+ matches)."""

    @pytest.fixture
    def response_schema(self) -> dict[str, Any]:
        committed = _load_committed()
        operation = committed["paths"]["/api/v1/qc/rules"]["get"]
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        return {**schema, "components": committed["components"]}

    def test_deployment_scope_example_matches_exactly_one_branch(
        self, response_schema: dict[str, Any]
    ) -> None:
        example = QcRulesResponse(
            observation=ObservationQcRuleSetResponse(
                version="1.0.0", source="builtin_default", rules=[]
            ),
            forecast=ForecastQcRuleSetResponse(
                version="1.0.0", source="builtin_default", rules=[]
            ),
        ).model_dump(mode="json")
        jsonschema.validate(
            instance=example,
            schema=response_schema,
            resolver=jsonschema.RefResolver.from_schema(response_schema),
        )

    def test_station_scope_example_matches_exactly_one_branch(
        self, response_schema: dict[str, Any]
    ) -> None:
        example = StationQcRulesResponse(
            station_id="00000000-0000-0000-0000-000000000001",
            station_qc="judged",
            water_level_datum_masl=None,
            observation=StationObservationQcRuleSetResponse(
                version="1.0.0",
                source="builtin_default",
                rules=[
                    StationObservationQcRuleResponse(
                        rule_id="range_check",
                        rule_version="1.0.0",
                        parameter="discharge",
                        time_step_seconds=600,
                        network=None,
                        severity="qc_failed",
                        thresholds={"value_min": 0.0, "value_max": 100.0},
                        station_thresholds=["value_max"],
                        skipped=None,
                    )
                ],
            ),
            forecast=ForecastQcRuleSetResponse(
                version="1.0.0", source="builtin_default", rules=[]
            ),
        ).model_dump(mode="json")
        jsonschema.validate(
            instance=example,
            schema=response_schema,
            resolver=jsonschema.RefResolver.from_schema(response_schema),
        )
