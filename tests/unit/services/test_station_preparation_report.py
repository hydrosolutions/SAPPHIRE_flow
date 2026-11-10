from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from sapphire_flow.services.station_preparation_report import (
    render_station_preparation_report,
)
from sapphire_flow.types.domain import GeoCoord
from sapphire_flow.types.enums import StationKind
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.station_preparation import (
    PackageIdentity,
    PreparationAssessment,
    PreparationBinding,
    PreparationHold,
    PreparationProvenance,
    PreparationRequest,
    ProposedStation,
    StationIdentity,
)


def request() -> PreparationRequest:
    return PreparationRequest(
        provenance=PreparationProvenance.SYNTHETIC,
        station=ProposedStation(
            identity=StationIdentity(
                tenant_id=TenantId(UUID(int=1)), network="research", code="00001"
            ),
            name="Trishuli synthetic proposal",
            kind=StationKind.RIVER,
            location=GeoCoord(lon=85.0, lat=28.0),
        ),
        package=PackageIdentity(
            package_id="package",
            fingerprint="canonical",
            source_fingerprint="source",
            derived_fingerprint="review",
        ),
        bindings=(
            PreparationBinding(
                basin_code="basin",
                gauge_id="static-gauge",
                gateway_hru_name="hru",
                polygon_name="polygon",
            ),
        ),
    )


class TestRenderStationPreparationReport:
    def test_truthful_labels_and_no_operational_claims(self) -> None:
        report = render_station_preparation_report(
            PreparationAssessment(request=request(), holds=())
        )
        for phrase in (
            "OFFLINE_PREFLIGHT_ONLY",
            "CALLER-SUPPLIED SYNTHETIC NO-OBSERVATION SCENARIO",
            "NOT ASSESSED",
            "No such state was inspected or changed",
            "Not production admission",
        ):
            assert phrase in report
        for phrase in (
            "forecasts OFF",
            "no observations available",
            "ADMITTED",
            "APPROVED",
            "READY",
        ):
            assert phrase not in report
        assert (
            "No activation, admission, approval, readiness or authentication "
            "permission is granted" in report
        )

    def test_escapes_all_requested_text_without_resources(self) -> None:
        value = request()
        hostile = '<script src="https://example.invalid">&</script>'
        value = replace(
            value,
            station=replace(value.station, name=hostile),
            bindings=(replace(value.bindings[0], gauge_id=hostile),),
        )
        report = render_station_preparation_report(
            PreparationAssessment(request=value, holds=())
        )
        assert "&lt;script" in report
        for markup in ("<script", "<a ", "<link", "<img", "<form", "<iframe"):
            assert markup not in report

    def test_repeated_render_is_identical(self) -> None:
        result = PreparationAssessment(
            request=request(), holds=(PreparationHold.BINDING_MISMATCH,)
        )
        assert render_station_preparation_report(
            result
        ) == render_station_preparation_report(result)

    def test_maximum_binding_count_is_rendered_without_silent_truncation(self) -> None:
        value = request()
        value = replace(
            value,
            bindings=tuple(
                replace(value.bindings[0], polygon_name=f"polygon-{i}")
                for i in range(64)
            ),
        )
        report = render_station_preparation_report(
            PreparationAssessment(request=value, holds=())
        )
        assert report.count("<tr>") == 65

    def test_escaped_output_limit_fails_instead_of_truncating(self) -> None:
        value = request()
        large = '"' * 256
        bindings = tuple(
            PreparationBinding(
                basin_code=large,
                gauge_id=large,
                gateway_hru_name=large,
                polygon_name=str(i) + large[:250],
            )
            for i in range(64)
        )
        value = replace(value, bindings=bindings)
        with pytest.raises(ValueError, match="report exceeds 128 KiB"):
            render_station_preparation_report(
                PreparationAssessment(request=value, holds=())
            )
