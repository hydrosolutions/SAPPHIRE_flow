from __future__ import annotations

import hashlib
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sapphire_flow.config.fine_tuning import parse_fine_tuning
from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.fine_tuning_evidence import CandidateReceipt
from sapphire_flow.services.fine_tuning_review import ReviewDecision, decide_candidate
from sapphire_flow.store.fine_tuning import parse_receipt
from sapphire_flow.types.enums import AuditEventType, ModelArtifactStatus
from sapphire_flow.types.ids import PrincipalId, TenantId
from sapphire_flow.types.station import StationGroup
from sapphire_flow.types.write_principal import WritePrincipal
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeAuditLogStore,
    FakeModelArtifactStore,
    FakeStationGroupStore,
    FakeStationStore,
)
from tests.unit.config.test_fine_tuning import request_document


def candidate() -> SimpleNamespace:
    request = parse_fine_tuning(request_document())
    station = make_station_config()
    stations = FakeStationStore()
    stations.store_station(station)
    groups = FakeStationGroupStore()
    group = StationGroup(
        id=request.group_id,
        name="review",
        station_ids=frozenset({station.id}),
        created_at=request.fitting_start,
        tenant_id=station.tenant_id,
    )
    groups.store_group(group)
    artifacts = FakeModelArtifactStore(groups)
    donor, _ = artifacts.store_artifact(
        request.model_id,
        b"donor",
        request.fitting_start,
        request.fitting_end,
        request.fitting_end,
        group_id=group.id,
        status=ModelArtifactStatus.ACTIVE,
    )
    aid, _ = artifacts.store_artifact(
        request.model_id,
        b"candidate",
        request.fitting_start,
        request.fitting_end,
        request.fitting_end,
        group_id=group.id,
        status=ModelArtifactStatus.PENDING_APPROVAL,
    )
    request = replace(request, base_artifact_id=donor)
    receipt = CandidateReceipt(
        request=request,
        tenant_id=group.tenant_id,
        artifact_id=aid,
        artifact_sha256=hashlib.sha256(b"candidate").hexdigest(),
        donor_sha256=hashlib.sha256(b"donor").hexdigest(),
        incumbent_ids=(donor,),
        requested_stations=(station.id,),
        fitted_stations=(station.id,),
        fitting_sources_json='{"products": []}',
        evaluation_json='{"state":"complete"}',
    )
    return SimpleNamespace(
        receipt=receipt,
        artifacts=artifacts,
        groups=groups,
        group=group,
        stations=stations,
        audit=FakeAuditLogStore(),
        principal=WritePrincipal(id=PrincipalId("admin"), tenant_id=group.tenant_id),
    )


def decide(
    bundle: SimpleNamespace, decision: ReviewDecision, **overrides: object
) -> None:
    args = dict(
        reviewed_sha256=bundle.receipt.sha256,
        principal=bundle.principal,
        artifacts=bundle.artifacts,
        groups=bundle.groups,
        stations=bundle.stations,
        audit=bundle.audit,
        decision=decision,
        reason="reviewed skill and coverage",
        now=bundle.receipt.request.evaluation_end,
    )
    args.update(overrides)
    decide_candidate(bundle.receipt, **args)


class TestCandidateDecision:
    def test_approval_replaces_incumbent_and_records_review_digest(self) -> None:
        b = candidate()
        decide(b, ReviewDecision.APPROVE)
        assert (
            b.artifacts.fetch_artifact_record(b.receipt.artifact_id).status
            is ModelArtifactStatus.ACTIVE
        )
        assert (
            b.artifacts.fetch_artifact_record(b.receipt.request.base_artifact_id).status
            is ModelArtifactStatus.SUPERSEDED
        )
        assert b.audit.entries[-1].event_type is AuditEventType.FINE_TUNING_DECISION
        assert b.audit.entries[-1].detail["reviewed_sha256"] == b.receipt.sha256

    def test_rejection_preserves_incumbent(self) -> None:
        b = candidate()
        decide(b, ReviewDecision.REJECT)
        assert (
            b.artifacts.fetch_artifact_record(b.receipt.request.base_artifact_id).status
            is ModelArtifactStatus.ACTIVE
        )
        assert (
            b.artifacts.fetch_artifact_record(b.receipt.artifact_id).status
            is ModelArtifactStatus.REJECTED
        )

    @pytest.mark.parametrize(
        "change,reason",
        [
            ("foreign", "authorized tenant"),
            ("stale", "stale"),
            ("incomplete", "incomplete"),
            ("roster", "membership"),
            ("incumbent", "active model changed"),
            ("reason", "requires a reason"),
        ],
    )
    def test_refusal_leaves_candidate_inactive(self, change: str, reason: str) -> None:
        b = candidate()
        overrides = {}
        if change == "foreign":
            overrides["principal"] = replace(b.principal, tenant_id=TenantId(uuid4()))
        if change == "stale":
            overrides["reviewed_sha256"] = "old"
        if change == "incomplete":
            b.receipt = replace(b.receipt, evaluation_json=None)
        if change == "roster":
            b.groups.store_group(replace(b.group, station_ids=frozenset()))
        if change == "incumbent":
            b.artifacts.transition_artifact_status(
                b.receipt.request.base_artifact_id, ModelArtifactStatus.SUPERSEDED
            )
        if change == "reason":
            overrides["reason"] = " "
        with pytest.raises(ConfigurationError, match=reason):
            decide(b, ReviewDecision.APPROVE, **overrides)
        assert (
            b.artifacts.fetch_artifact_record(b.receipt.artifact_id).status
            is ModelArtifactStatus.PENDING_APPROVAL
        )
        assert not b.audit.entries

    def test_receipt_round_trip_binds_configuration_and_evidence(self) -> None:
        original = candidate().receipt
        assert parse_receipt(original.payload()).sha256 == original.sha256
        changed = original.payload()
        changed["request"]["training"]["seed"] = 123
        with pytest.raises(ConfigurationError, match="digest mismatch"):
            parse_receipt(changed)
