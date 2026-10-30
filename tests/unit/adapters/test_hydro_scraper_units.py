from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from sapphire_flow.adapters.hydro_scraper import HydroScraperAdapter
from sapphire_flow.config.bafu_units import parse_bafu_unit_registry
from sapphire_flow.types.bafu_capture import (
    NormalizedDischargeCapture,
    QuarantinedDischargeCapture,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import FetchOutcomeCause
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from sapphire_flow.types.bafu_unit_registry import BafuUnitRegistry
    from sapphire_flow.types.observation import HydroScraperBatchResult


@pytest.fixture
def registry() -> BafuUnitRegistry:
    return parse_bafu_unit_registry(
        {
            "schema_version": 1,
            "source": "lindas_foen_hydro",
            "parameter": "discharge",
            "version": "synthetic-v1",
            "rules": [
                {
                    "rule_id": "synthetic",
                    "gauge_code": "2206",
                    "kind": "river",
                    "unit": "l/s",
                    "valid_from": "2026-10-20T00:00:00Z",
                    "verified_at": "2026-10-20T00:00:00Z",
                    "refresh_due": "2026-10-30T00:00:00Z",
                    "evidence_reference": "synthetic",
                    "evidence_sha256": "a" * 64,
                }
            ],
        }
    )


def fetch(
    registry: BafuUnitRegistry,
    fields: tuple[tuple[str, str], ...],
    *,
    code: str = "2206",
    malformed_sibling: dict[str, object] | None = None,
) -> HydroScraperBatchResult:
    bindings = [
        {
            "subject": {
                "value": f"https://environment.ld.admin.ch/foen/hydro/river/observation/{code}"
            },
            "predicate": {
                "value": f"https://environment.ld.admin.ch/foen/hydro/dimension/{name}"
            },
            "object": {"value": value},
        }
        for name, value in fields
    ]

    payload_bindings: list[object] = list(bindings)
    if malformed_sibling is not None:
        payload_bindings.append(
            {
                "subject": {
                    "value": f"https://environment.ld.admin.ch/foen/hydro/river/observation/{code}"
                },
                **malformed_sibling,
            }
        )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": {"bindings": payload_bindings}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = HydroScraperAdapter(
            endpoint="https://lindas.admin.ch/query",
            http_client=client,
            unit_registry=registry,
            clock=lambda: ensure_utc(datetime(2026, 10, 22, tzinfo=UTC)),
        )
        return adapter.fetch_observations_batch([make_station_config(code=code)], {})


class TestHydroScraperCapturedUnits:
    def test_normalizes_discharge_but_not_water_level(
        self, registry: BafuUnitRegistry
    ) -> None:
        result = fetch(
            registry,
            (
                ("measurementTime", "2026-10-21T00:00:00Z"),
                ("discharge", "22"),
                ("waterLevel", "341.25"),
            ),
        )
        assert {o.parameter: o.value for o in result.observations} == {
            "discharge": 0.022,
            "water_level": 341.25,
        }
        assert isinstance(
            result.captured_outcomes[0].source_captures[0].result,
            NormalizedDischargeCapture,
        )

    def test_unknown_unit_retains_other_parameters_and_capture(
        self, registry: BafuUnitRegistry
    ) -> None:
        result = fetch(
            registry,
            (
                ("measurementTime", "2026-10-21T00:00:00Z"),
                ("discharge", "22"),
                ("waterLevel", "341.25"),
            ),
            code="2044",
        )
        assert {o.parameter: o.value for o in result.observations} == {
            "water_level": 341.25
        }
        assert len(result.captured_outcomes) == 1
        assert result.outcomes[0].parameter_diagnostics
        assert result.failed == ()

    def test_quarantine_only_outcome_survives(self, registry: BafuUnitRegistry) -> None:
        result = fetch(
            registry,
            (("measurementTime", "2026-10-21T00:00:00Z"), ("discharge", "22")),
            code="2044",
        )
        assert result.observations == []
        assert isinstance(
            result.captured_outcomes[0].source_captures[0].result,
            QuarantinedDischargeCapture,
        )

    def test_conflicting_discharge_is_not_last_wins(
        self, registry: BafuUnitRegistry
    ) -> None:
        result = fetch(
            registry,
            (
                ("measurementTime", "2026-10-21T00:00:00Z"),
                ("discharge", "22"),
                ("discharge", "23"),
                ("waterLevel", "341.25"),
            ),
        )
        assert {o.parameter: o.value for o in result.observations} == {
            "water_level": 341.25
        }
        assert result.captured_outcomes[0].source_captures[0].source_literals == (
            "22",
            "23",
        )

    def test_conflicting_time_invalidates_all_parameters(
        self, registry: BafuUnitRegistry
    ) -> None:
        result = fetch(
            registry,
            (
                ("measurementTime", "2026-10-21T00:00:00Z"),
                ("measurementTime", "2026-10-22T00:00:00Z"),
                ("discharge", "22"),
                ("waterLevel", "341.25"),
            ),
        )
        assert result.observations == []
        assert result.failed[0].failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE
        captured = result.captured_outcomes[0].source_captures[0].result
        assert isinstance(captured, QuarantinedDischargeCapture)
        assert captured.measurement_time is None

    def test_bad_other_parameter_preserves_discharge_evidence(
        self, registry: BafuUnitRegistry
    ) -> None:
        result = fetch(
            registry,
            (
                ("measurementTime", "2026-10-21T00:00:00Z"),
                ("discharge", "22"),
                ("waterLevel", "bad"),
            ),
        )
        assert result.observations == []
        assert result.failed
        assert isinstance(
            result.captured_outcomes[0].source_captures[0].result,
            QuarantinedDischargeCapture,
        )

    @pytest.mark.parametrize(
        "sibling",
        [
            {
                "predicate": {
                    "value": "https://environment.ld.admin.ch/foen/hydro/dimension/waterLevel"
                },
                "object": {},
            },
            {
                "predicate": {
                    "value": "https://environment.ld.admin.ch/foen/hydro/dimension/waterLevel"
                },
                "object": {"value": 2.0},
            },
            {"predicate": {}, "object": {"value": "bad"}},
        ],
    )
    def test_malformed_sibling_preserves_recoverable_discharge(
        self,
        registry: BafuUnitRegistry,
        sibling: dict[str, object],
    ) -> None:
        batch = fetch(
            registry,
            (("measurementTime", "2026-10-21T00:00:00Z"), ("discharge", "22")),
            malformed_sibling=sibling,
        )
        assert batch.failed
        assert batch.observations == []
        capture = batch.captured_outcomes[0].source_captures[0]
        assert capture.source_literals == ("22",)
        assert capture.measurement_literals == ("2026-10-21T00:00:00Z",)
        assert isinstance(capture.result, QuarantinedDischargeCapture)


@pytest.mark.parametrize("parameter", ["waterLevel", "waterTemperature"])
@pytest.mark.parametrize("literal", ["NaN", "INF", "-INF"])
def test_nonfinite_ancillary_quarantines_original_capture(
    registry: BafuUnitRegistry, parameter: str, literal: str
) -> None:
    from sapphire_flow.types.bafu_capture import DischargeCaptureFailure

    result = fetch(
        registry,
        (
            ("measurementTime", "2026-10-21T00:00:00Z"),
            ("discharge", "22"),
            (parameter, literal),
        ),
    )
    assert result.observations == []
    assert result.failed[0].failure_cause is FetchOutcomeCause.MALFORMED_RESPONSE
    capture = result.captured_outcomes[0].source_captures[0]
    assert capture.source_literals == ("22",)
    assert isinstance(capture.result, QuarantinedDischargeCapture)
    assert capture.result.reason is DischargeCaptureFailure.SUBJECT_MALFORMED
