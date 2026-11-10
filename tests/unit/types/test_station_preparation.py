from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from uuid import UUID

import pytest

from sapphire_flow.types.domain import GeoCoord
from sapphire_flow.types.enums import StationKind
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.station_preparation import (
    EvidenceState,
    OpaqueEvidence,
    PackageIdentity,
    PreparationAssessment,
    PreparationBinding,
    PreparationHold,
    PreparationOutcome,
    PreparationProvenance,
    PreparationRequest,
    ProposedStation,
    StationIdentity,
    VisiblePackage,
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


class TestPreparationRequest:
    def test_keeps_literal_identity_and_is_immutable(self) -> None:
        value = request()
        assert value.station.identity.code == "00001"
        with pytest.raises(FrozenInstanceError, match="cannot assign"):
            value.bindings = ()  # type: ignore[misc]

    @pytest.mark.parametrize(
        "field",
        ["package_id", "fingerprint", "source_fingerprint", "derived_fingerprint"],
    )
    @pytest.mark.parametrize("value", ["", " ", "x" * 257])
    def test_rejects_invalid_identity_text(self, field: str, value: str) -> None:
        with pytest.raises(ValueError, match="text must be"):
            replace(request().package, **{field: value})

    @pytest.mark.parametrize(
        "field", ["basin_code", "gauge_id", "gateway_hru_name", "polygon_name"]
    )
    def test_requires_each_binding_identity(self, field: str) -> None:
        with pytest.raises(ValueError, match="text must be"):
            replace(request().bindings[0], **{field: ""})

    @pytest.mark.parametrize("conflicting_gauge", ["static-gauge", "another-gauge"])
    def test_duplicate_or_conflicting_expected_keys_refused(
        self, conflicting_gauge: str
    ) -> None:
        value = request()
        with pytest.raises(ValueError, match="duplicate or conflicting"):
            replace(
                value,
                bindings=(
                    value.bindings[0],
                    replace(value.bindings[0], gauge_id=conflicting_gauge),
                ),
            )

    def test_competing_basin_for_same_polygon_refused(self) -> None:
        value = request()
        with pytest.raises(ValueError, match="duplicate or conflicting"):
            replace(
                value,
                bindings=(
                    value.bindings[0],
                    replace(value.bindings[0], basin_code="other-basin"),
                ),
            )

    def test_binding_count_and_empty_request(self) -> None:
        value = request()
        with pytest.raises(ValueError, match="at least one"):
            replace(value, bindings=())
        entries = tuple(
            replace(value.bindings[0], polygon_name=str(i)) for i in range(65)
        )
        with pytest.raises(ValueError, match="at most 64"):
            replace(value, bindings=entries)
        assert len(replace(value, bindings=entries[:64]).bindings) == 64

    def test_binding_order_is_canonical(self) -> None:
        value = request()
        second = replace(value.bindings[0], polygon_name="a")
        assert replace(value, bindings=(second, *value.bindings)) == replace(
            value, bindings=(*value.bindings, second)
        )

    @pytest.mark.parametrize("field", ["lon", "lat", "altitude_masl"])
    @pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
    def test_nonfinite_metadata_refused(self, field: str, number: float) -> None:
        value = request()
        with pytest.raises(ValueError, match="out of range|must be finite"):
            replace(
                value.station,
                location=replace(value.station.location, **{field: number}),
            )

    def test_optional_altitude_and_finite_altitude(self) -> None:
        value = request().station
        assert value.location.altitude_masl is None
        assert (
            replace(
                value, location=replace(value.location, altitude_masl=1200.0)
            ).location.altitude_masl
            == 1200.0
        )

    def test_raw_provenance_not_accepted(self) -> None:
        with pytest.raises(ValueError, match="synthetic evidence only"):
            replace(request(), provenance="synthetic")


class TestEvidenceVariants:
    @pytest.mark.parametrize("state", list(EvidenceState))
    def test_opaque_has_no_payload(self, state: EvidenceState) -> None:
        with pytest.raises(TypeError, match="unexpected keyword"):
            OpaqueEvidence(state=state, payload="foreign")  # type: ignore[call-arg]
        assert not hasattr(OpaqueEvidence(state=state), "__dict__")

    def test_visible_evidence_cannot_hide_duplicates(self) -> None:
        value = request()
        with pytest.raises(ValueError, match="duplicate or conflicting"):
            VisiblePackage(
                provenance=value.provenance,
                identity=value.station.identity,
                package=value.package,
                bindings=value.bindings * 2,
            )

    def test_assessment_carries_only_request_and_typed_holds(self) -> None:
        value = PreparationAssessment(
            request=request(), holds=(PreparationHold.EVIDENCE_UNAVAILABLE,)
        )
        assert value.outcome is PreparationOutcome.HELD
        with pytest.raises(ValueError, match="expected PreparationHold"):
            replace(value, holds=("foreign-sentinel",))
