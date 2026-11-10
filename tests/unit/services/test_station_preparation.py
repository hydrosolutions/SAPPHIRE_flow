from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from sapphire_flow.services.station_preparation import assess_station_preparation
from sapphire_flow.services.station_preparation_report import (
    render_station_preparation_report,
)
from sapphire_flow.types.domain import GeoCoord
from sapphire_flow.types.enums import StationKind
from sapphire_flow.types.ids import TenantId
from sapphire_flow.types.station_preparation import (
    EvidenceState,
    OpaqueEvidence,
    PackageIdentity,
    PreparationBinding,
    PreparationHold,
    PreparationOutcome,
    PreparationProvenance,
    PreparationRequest,
    ProposedStation,
    StationIdentity,
    VisiblePackage,
    VisibleStation,
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


class FakeEvidence:
    def __init__(self, value: PreparationRequest) -> None:
        self.station_evidence: VisibleStation | OpaqueEvidence = OpaqueEvidence(
            state=EvidenceState.ABSENT
        )
        self.package_evidence: VisiblePackage | OpaqueEvidence = VisiblePackage(
            provenance=value.provenance,
            identity=value.station.identity,
            package=value.package,
            bindings=value.bindings,
        )
        self.calls: list[tuple[StationIdentity, str | None]] = []
        self.private_facts = (
            "foreign-tenant",
            "foreign-station",
            "foreign-package",
            "foreign-gauge",
            "foreign-basin",
            "foreign-hru",
            "foreign-polygon",
        )

    def station(self, identity: StationIdentity) -> VisibleStation | OpaqueEvidence:
        self.calls.append((identity, None))
        return self.station_evidence

    def package(
        self, identity: StationIdentity, package_id: str
    ) -> VisiblePackage | OpaqueEvidence:
        self.calls.append((identity, package_id))
        return self.package_evidence


class TestAssessStationPreparation:
    def test_synthetic_path_is_read_only_and_deterministic(self) -> None:
        value = request()
        repo = FakeEvidence(value)
        before = repr((value, repo.station_evidence, repo.package_evidence))
        result = assess_station_preparation(value, repo)
        assert result.outcome is PreparationOutcome.OFFLINE_PREFLIGHT_ONLY
        assert result == assess_station_preparation(value, FakeEvidence(value))
        assert repo.calls == [
            (value.station.identity, None),
            (value.station.identity, "package"),
        ]
        assert repr((value, repo.station_evidence, repo.package_evidence)) == before
        assert "00001" in render_station_preparation_report(result)

    def test_existing_station_holds_without_package_read(self) -> None:
        value = request()
        repo = FakeEvidence(value)
        repo.station_evidence = VisibleStation(
            provenance=value.provenance, identity=value.station.identity
        )
        result = assess_station_preparation(value, repo)
        assert result.holds == (PreparationHold.EXISTING_STATION,)
        assert repo.calls == [(value.station.identity, None)]

    @pytest.mark.parametrize(
        "state",
        [
            EvidenceState.UNKNOWN,
            EvidenceState.HIDDEN,
            EvidenceState.FOREIGN,
            EvidenceState.CONFLICT,
        ],
    )
    @pytest.mark.parametrize("stage", ["station", "package"])
    def test_opaque_evidence_never_discloses_private_sentinels(
        self, state: EvidenceState, stage: str
    ) -> None:
        value = request()
        repo = FakeEvidence(value)
        if stage == "station":
            repo.station_evidence = OpaqueEvidence(state=state)
        else:
            repo.package_evidence = OpaqueEvidence(state=state)
        result = assess_station_preparation(value, repo)
        assert result.holds == (PreparationHold.EVIDENCE_UNAVAILABLE,)
        for secret in repo.private_facts:
            assert secret not in repr(result)
            assert secret not in render_station_preparation_report(result)

    def test_missing_package_is_not_consistent(self) -> None:
        value = request()
        repo = FakeEvidence(value)
        repo.package_evidence = OpaqueEvidence(state=EvidenceState.ABSENT)
        assert assess_station_preparation(value, repo).holds == (
            PreparationHold.EVIDENCE_UNAVAILABLE,
        )

    @pytest.mark.parametrize("field", ["tenant_id", "network", "code"])
    @pytest.mark.parametrize("stage", ["station", "package"])
    def test_wrong_visible_scope_is_generic_and_payload_free(
        self, field: str, stage: str
    ) -> None:
        value = request()
        repo = FakeEvidence(value)
        foreign_identity = replace(
            value.station.identity,
            **{
                field: TenantId(UUID(int=999))
                if field == "tenant_id"
                else "foreign-station"
            },
        )
        if stage == "station":
            repo.station_evidence = VisibleStation(
                provenance=value.provenance, identity=foreign_identity
            )
        else:
            repo.package_evidence = VisiblePackage(
                provenance=value.provenance,
                identity=foreign_identity,
                package=PackageIdentity(
                    package_id="foreign-package",
                    fingerprint="foreign-canonical",
                    source_fingerprint="foreign-source",
                    derived_fingerprint="foreign-review",
                ),
                bindings=(
                    PreparationBinding(
                        basin_code="foreign-basin",
                        gauge_id="foreign-gauge",
                        gateway_hru_name="foreign-hru",
                        polygon_name="foreign-polygon",
                    ),
                ),
            )
        result = assess_station_preparation(value, repo)
        assert result.holds == (PreparationHold.EVIDENCE_UNAVAILABLE,)
        for secret in (
            *repo.private_facts,
            str(UUID(int=999)),
            "foreign-canonical",
            "foreign-source",
            "foreign-review",
        ):
            assert secret not in repr(result)
            assert secret not in render_station_preparation_report(result)

    @pytest.mark.parametrize(
        "field",
        ["package_id", "fingerprint", "source_fingerprint", "derived_fingerprint"],
    )
    def test_each_package_identity_is_compared_to_same_field(self, field: str) -> None:
        value = request()
        repo = FakeEvidence(value)
        assert isinstance(repo.package_evidence, VisiblePackage)
        repo.package_evidence = replace(
            repo.package_evidence,
            package=replace(value.package, **{field: "drift-sentinel"}),
        )
        result = assess_station_preparation(value, repo)
        assert result.holds == (PreparationHold.IDENTITY_MISMATCH,)
        assert "drift-sentinel" not in repr(result) + render_station_preparation_report(
            result
        )

    def test_crossed_fingerprints_are_not_interchangeable(self) -> None:
        value = request()
        repo = FakeEvidence(value)
        assert isinstance(repo.package_evidence, VisiblePackage)
        repo.package_evidence = replace(
            repo.package_evidence,
            package=replace(
                value.package,
                source_fingerprint=value.package.derived_fingerprint,
                derived_fingerprint=value.package.source_fingerprint,
            ),
        )
        assert assess_station_preparation(value, repo).holds == (
            PreparationHold.IDENTITY_MISMATCH,
        )

    @pytest.mark.parametrize(
        "field", ["basin_code", "gauge_id", "gateway_hru_name", "polygon_name"]
    )
    def test_same_package_still_checks_each_binding(self, field: str) -> None:
        value = request()
        repo = FakeEvidence(value)
        assert isinstance(repo.package_evidence, VisiblePackage)
        repo.package_evidence = replace(
            repo.package_evidence,
            bindings=(replace(value.bindings[0], **{field: "binding-sentinel"}),),
        )
        result = assess_station_preparation(value, repo)
        assert result.holds == (PreparationHold.BINDING_MISMATCH,)
        assert "binding-sentinel" not in repr(
            result
        ) + render_station_preparation_report(result)

    def test_missing_binding_holds(self) -> None:
        value = request()
        repo = FakeEvidence(value)
        assert isinstance(repo.package_evidence, VisiblePackage)
        repo.package_evidence = replace(repo.package_evidence, bindings=())
        assert assess_station_preparation(value, repo).holds == (
            PreparationHold.BINDING_MISMATCH,
        )
