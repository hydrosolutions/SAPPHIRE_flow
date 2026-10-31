"""Plan 402 T4 — the committed map contract must never drift from
`api/map_contract.py::build_map_openapi()` (Plan 198 D15's mechanism)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import jsonschema
import pytest
from fastapi.routing import APIRoute

from sapphire_flow.api.forecast_lab_schemas import ForecastLabSnapshot
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
    # Plan 404 T3 — additive (MAP_CONTRACT_VERSION 1.0 -> 1.1, D14).
    "/api/v1/stations/{station_id}/rejected-forecasts",
    "/api/v1/stations/{station_id}/evaluation-forecasts",
    "/api/v1/forecast-lab/snapshot",
    "/api/v1/observations/latest",
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
            scope="deployment",
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
            scope="station",
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


class TestQcRulesDiscriminatorIsActuallyRequired:
    """The `oneOf`/`discriminator` on `/qc/rules` is only usable by an
    external client if the discriminator field is REQUIRED, not merely
    present-with-a-default: a pydantic default on the discriminator field is
    omitted from the generated `required` list, so a payload missing `scope`
    would validate against both branches and the `oneOf` would discriminate
    nothing (review finding, 2026-09-28)."""

    @pytest.fixture
    def response_schema(self) -> dict[str, Any]:
        committed = _load_committed()
        operation = committed["paths"]["/api/v1/qc/rules"]["get"]
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        return {**schema, "components": committed["components"]}

    def test_scope_is_required_on_both_branches(self) -> None:
        committed = _load_committed()
        schemas = committed["components"]["schemas"]
        assert "scope" in schemas["QcRulesResponse"]["required"]
        assert "scope" in schemas["StationQcRulesResponse"]["required"]

    def test_station_row_skipped_is_required(self) -> None:
        committed = _load_committed()
        schemas = committed["components"]["schemas"]
        assert "skipped" in schemas["StationObservationQcRuleResponse"]["required"]

    def test_a_deployment_payload_missing_scope_matches_no_branch(
        self, response_schema: dict[str, Any]
    ) -> None:
        example = QcRulesResponse(
            scope="deployment",
            observation=ObservationQcRuleSetResponse(
                version="1.0.0", source="builtin_default", rules=[]
            ),
            forecast=ForecastQcRuleSetResponse(
                version="1.0.0", source="builtin_default", rules=[]
            ),
        ).model_dump(mode="json")
        del example["scope"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(
                instance=example,
                schema=response_schema,
                resolver=jsonschema.RefResolver.from_schema(response_schema),
            )


class TestSnapshotContract:
    def test_snapshot_export_is_valid_openapi(self) -> None:
        from fastapi.openapi.models import OpenAPI

        doc = build_map_openapi()
        OpenAPI.model_validate(doc)
        operation = doc["paths"]["/api/v1/forecast-lab/snapshot"]["get"]
        assert operation["responses"]["200"]["content"]["application/json"][
            "schema"
        ] == {"$ref": "#/components/schemas/ForecastLabSnapshot"}
        assert {"401", "403", "404", "422", "500", "503"} <= operation[
            "responses"
        ].keys()

    def test_export_does_not_mutate_runtime_schema_or_routes(self) -> None:
        from sapphire_flow.api import app

        before = ForecastLabSnapshot.model_json_schema()
        route = next(
            r
            for r in app.routes
            if isinstance(r, APIRoute) and r.path == "/api/v1/forecast-lab/snapshot"
        )
        response_field = route.response_field
        build_map_openapi()
        assert route.response_field is response_field
        assert ForecastLabSnapshot.model_json_schema() == before

    def test_nested_union_retains_representation_but_not_boolean_discriminator(
        self,
    ) -> None:
        doc = build_map_openapi()
        properties = doc["components"]["schemas"]["StationEntrySchema"]["properties"]
        for union in [
            properties["sapphire_forecasts"]["items"],
            properties["combined_forecast"],
        ]:
            assert "discriminator" not in union
            available = next(branch for branch in union["oneOf"] if "oneOf" in branch)
            assert available["discriminator"]["propertyName"] == "representation"
            assert all(
                isinstance(ref, str)
                for ref in available["discriminator"]["mapping"].values()
            )


class TestSnapshotContractValidation:
    @pytest.mark.parametrize("representation", ["members", "quantiles", "unavailable"])
    def test_response_variants_validate(self, representation: str) -> None:
        doc = build_map_openapi()
        example = json.loads(
            (
                _REPO_ROOT
                / "tests/fixtures/forecast_lab/forecast_lab_snapshot_example.json"
            ).read_text()
        )
        entry = example["stations"][0]["sapphire_forecasts"][0]
        assert entry["available"] is True
        if representation == "quantiles":
            entry["representation"] = "quantiles"
            entry.pop("ensemble_size")
            entry["quantile_level_count"] = 7
            for point in entry["points"]:
                point["minimum"] = None
                point["maximum"] = None
        elif representation == "unavailable":
            example["stations"][0]["sapphire_forecasts"][0] = {
                "available": False,
                "model": {"key": entry["model"]["key"]},
                "reason": "no_forecast",
            }
        schema = {
            "$ref": "#/components/schemas/ForecastLabSnapshot",
            "components": doc["components"],
        }
        jsonschema.validate(
            instance=example, schema=schema, cls=jsonschema.Draft202012Validator
        )
        ForecastLabSnapshot.model_validate(example)

    @pytest.mark.parametrize("variant", ["quantiles", "members", "unavailable"])
    def test_invalid_representation_fields_are_rejected(self, variant: str) -> None:
        doc = build_map_openapi()
        example = json.loads(
            (
                _REPO_ROOT
                / "tests/fixtures/forecast_lab/forecast_lab_snapshot_example.json"
            ).read_text()
        )
        entry = example["stations"][0]["sapphire_forecasts"][0]
        assert entry["available"] is True
        if variant == "quantiles":
            entry["representation"] = "quantiles"
        elif variant == "members":
            entry.pop("ensemble_size")
            entry["quantile_level_count"] = 7
        else:
            entry["available"] = False
            entry["reason"] = "no_forecast"
        schema = {
            "$ref": "#/components/schemas/ForecastLabSnapshot",
            "components": doc["components"],
        }
        with pytest.raises(
            jsonschema.ValidationError,
            match="Additional properties"
            if variant == "unavailable"
            else "was expected",
        ):
            jsonschema.validate(
                instance=example, schema=schema, cls=jsonschema.Draft202012Validator
            )

    def test_every_local_reference_resolves(self) -> None:
        doc = build_map_openapi()
        pending: list[Any] = [doc]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                mapping = cast("dict[str, Any]", node)
                if "$ref" in mapping:
                    ref = mapping["$ref"]
                    assert isinstance(ref, str) and ref.startswith("#/")
                    target = doc
                    for part in ref[2:].split("/"):
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                pending.extend(mapping.values())
            elif isinstance(node, list):
                pending.extend(cast("list[Any]", node))
