from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import date, timedelta
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.config.dhm import DhmBinding, DhmConfig
from sapphire_flow.config.provisional_rating import (
    ApprovedLegacySegmentConfig,
    NativeRatingBinding,
)
from sapphire_flow.services.rating_delivery import admit_rating_deliveries
from sapphire_flow.store.native_capture_store import PgNativeCaptureStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.rating_derived_store import PgRatingDerivedStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ModelId, RatingReferenceProofId, StationId
from sapphire_flow.types.native_capture import (
    CanonicalProviderIdentity,
    CaptureBatchContext,
    CaptureBatchId,
    CapturedLevelReference,
    CaptureRequestInterval,
    DhmRequestEvidence,
    NativeFetchOutcome,
    Sha256Digest,
    accepted_capture_input_digest,
)
from sapphire_flow.types.observation import RawObservation
from sapphire_flow.types.rating_evidence import RatingEvidenceMode
from sapphire_flow.types.rating_reconciliation import (
    RatingHoldReason,
    RatingInputsUnavailable,
)
from sapphire_flow.types.rating_reference import (
    RatingReferenceProof,
    canonical_content,
    curve_snapshot,
)
from tests.conftest import make_observation, make_station_config
from tests.integration.cli.test_import_dhm_delivery import _NOW
from tests.integration.cli.test_rating_only import delivery
from tests.integration.store.test_rejected_forecast_store import savepoint_factory

if TYPE_CHECKING:
    from sapphire_flow.types.auth import AuditEntry
    from sapphire_flow.types.observation import Observation


@pytest.fixture
def source_binding(
    db_connection: sa.Connection,
) -> tuple[NativeRatingBinding, DhmConfig, Observation]:
    conn = db_connection
    station = make_station_config(
        station_id=StationId(uuid4()), code="450", network="dhm", water_level_unit="m"
    )
    PgStationStore(conn).store_station(station)
    admit_rating_deliveries(
        conn,
        station.tenant_id,
        (delivery("selector-test", date(2026, 9, 8)),),
        now=_NOW,
    )
    curve = max(
        PgRatingCurveStore(conn).fetch_all_curves_for_station(station.id),
        key=lambda c: c.valid_from,
    )
    observation = make_observation(
        station_id=station.id, parameter="water_level", value=0.2, timestamp=_NOW
    )
    PgObservationStore(conn).store_observations([observation])
    endpoint = "https://example.invalid/levels/"
    reference = RatingReferenceProof(
        id=RatingReferenceProofId(uuid4()),
        tenant_id=station.tenant_id,
        station_id=station.id,
        endpoint=endpoint,
        api_station_id=1,
        evidence_reference="fixture",
        verified_by="fixture",
        verified_at=_NOW,
        rating_curve_id=curve.id,
        curve=curve_snapshot(curve),
        level_unit="m",
        curve_unit="m",
        level_reference="gauge_zero",
        curve_reference="gauge_zero",
        offset_m=0.0,
    )
    binding = NativeRatingBinding(
        tenant_id=station.tenant_id,
        station_id=station.id,
        station_code=station.code,
        model_ids=frozenset({ModelId("fixture")}),
        reference=reference,
        custody_start=_NOW - timedelta(days=1),
        custody_end=_NOW + timedelta(days=1),
        custody_reference="original exact fixture custody",
        reconciliation_days=1,
    )
    dhm = DhmConfig(
        endpoint=endpoint,
        max_requests_per_batch=1,
        max_bytes_per_batch=1000,
        max_batch_seconds=30.0,
        bindings=(
            DhmBinding(
                network="dhm",
                station_code=station.code,
                api_station_id=1,
                level_reference="gauge_zero",
            ),
        ),
    )
    return binding, dhm, observation


def capture(conn: sa.Connection, binding: NativeRatingBinding) -> None:
    rows = (
        RawObservation(
            station_id=binding.station_id,
            timestamp=_NOW,
            parameter="water_level",
            value=0.2,
            source=ObservationSource.MEASURED,
        ),
    )
    request = DhmRequestEvidence(
        batch_id=CaptureBatchId(uuid4()),
        station_id=binding.station_id,
        provider=CanonicalProviderIdentity.parse(binding.reference.endpoint, 1),
        requested_intervals=(
            CaptureRequestInterval(
                lower_exclusive=_NOW - timedelta(seconds=1),
                upper_exclusive=_NOW + timedelta(seconds=1),
            ),
        ),
        request_started_at=_NOW,
        retrieved_at=_NOW,
        outcome=NativeFetchOutcome.COMPLETE,
        request_count=1,
        accepted_bytes=10,
    )
    batch = CaptureBatchContext(
        request=request,
        tenant_id=binding.tenant_id,
        flow_run_id=None,
        accepted_input_sha256=accepted_capture_input_digest(rows),
        level_reference=CapturedLevelReference.GAUGE_ZERO,
    )
    PgNativeCaptureStore(
        savepoint_factory(conn), clock=lambda: _NOW, id_gen=uuid4
    ).commit_batch(batch, rows)


def native_binding(binding: NativeRatingBinding) -> NativeRatingBinding:
    return replace(
        binding,
        evidence_mode=RatingEvidenceMode.NATIVE_CAPTURE,
        custody_start=None,
        custody_end=None,
        custody_reference=None,
    )


class TestNativeRatingCaptureSelection:
    def test_missing_capture_is_a_typed_hold(
        self,
        db_connection: sa.Connection,
        source_binding: tuple[NativeRatingBinding, DhmConfig, Observation],
    ) -> None:
        binding, dhm, _ = source_binding
        store = PgRatingDerivedStore(savepoint_factory(db_connection))
        binding = native_binding(binding)
        result = store.read_current_window(
            binding, dhm, start=_NOW, end=_NOW + timedelta(hours=1), at=_NOW
        )
        assert result.source_count == 1
        assert [held.reason for held in result.held] == [
            RatingHoldReason.MISSING_CAPTURE
        ]
        with pytest.raises(RatingInputsUnavailable, match="missing_capture"):
            store.read_current(
                binding, dhm, start=_NOW, end=_NOW + timedelta(hours=1), at=_NOW
            )

    def test_native_v2_persists_and_unreconciled_stays_numerically_omitted(
        self,
        db_connection: sa.Connection,
        source_binding: tuple[NativeRatingBinding, DhmConfig, Observation],
    ) -> None:
        binding, dhm, observation = source_binding
        capture(db_connection, binding)
        store = PgRatingDerivedStore(savepoint_factory(db_connection))
        binding = native_binding(binding)
        window = dict(start=_NOW, end=_NOW + timedelta(hours=1), at=_NOW)
        (record,) = store.reconcile_chunk(binding, dhm, **window)
        assert record.evidence_version == 2
        assert record.discharge == pytest.approx(4.0)
        PgObservationStore(db_connection).update_qc(
            observation.id, QcStatus.QC_PASSED, [], qc_rule_version="new-generation"
        )
        result = store.read_current_window(binding, dhm, **window)
        assert [held.reason for held in result.held] == [RatingHoldReason.UNRECONCILED]
        assert result.held[0].source_snapshot_sha256 is not None
        assert store.read_current(binding, dhm, **window) == ()

    def test_approved_v1_wins_overlap_until_qc_changes(
        self,
        db_connection: sa.Connection,
        source_binding: tuple[NativeRatingBinding, DhmConfig, Observation],
    ) -> None:
        binding, dhm, observation = source_binding
        store = PgRatingDerivedStore(savepoint_factory(db_connection))
        window = dict(start=_NOW, end=_NOW + timedelta(hours=1), at=_NOW)
        (original,) = store.reconcile_chunk(binding, dhm, **window)
        feed_content = canonical_content(json.loads(original.content)["feed"])
        segment = ApprovedLegacySegmentConfig(
            feed_content=feed_content,
            feed_sha256=Sha256Digest.of(feed_content).value,
            reference_sha256=Sha256Digest.of(
                canonical_content(asdict(binding.reference))
            ).value,
            fingerprints=(original.fingerprint,),
        ).to_domain()
        capture(db_connection, binding)
        native = replace(native_binding(binding), legacy_segments=(segment,))
        assert store.reconcile_chunk(native, dhm, **window) == (original,)
        PgObservationStore(db_connection).update_qc(
            observation.id, QcStatus.QC_PASSED, [], qc_rule_version="changed"
        )
        (revised,) = store.reconcile_chunk(native, dhm, **window)
        assert revised.evidence_version == 2
        assert revised.fingerprint != original.fingerprint


class TestNativeCaptureIngestFlow:
    @pytest.mark.parametrize("fetch_case", ["empty", "fresh", "backfill"])
    def test_native_ingest_or_empty_fetch_recovers_then_converts(
        self,
        db_connection: sa.Connection,
        source_binding: tuple[NativeRatingBinding, DhmConfig, Observation],
        monkeypatch: pytest.MonkeyPatch,
        fetch_case: Literal["empty", "fresh", "backfill"],
    ) -> None:
        from types import SimpleNamespace

        from sapphire_flow.config.provisional_rating import ProvisionalRatingConfig
        from sapphire_flow.flows import ingest_observations as flow
        from sapphire_flow.types.domain import QcRuleSet
        from sapphire_flow.types.observation import (
            HydroScraperBatchResult,
            StationFetchOutcome,
        )
        from tests.fakes.fake_stores import FakeClimBaselineStore

        binding, dhm, old = source_binding
        fresh_fetch = fetch_case == "fresh"
        capture(db_connection, binding)
        if not fresh_fetch:
            PgObservationStore(db_connection).update_qc(old.id, QcStatus.RAW, [])
        end = _NOW + timedelta(minutes=30)
        now = _NOW + timedelta(days=10) if fetch_case == "backfill" else end
        rows = (
            (
                RawObservation(
                    station_id=binding.station_id,
                    timestamp=_NOW + timedelta(minutes=5),
                    parameter="water_level",
                    value=0.4,
                    source=ObservationSource.MEASURED,
                ),
            )
            if fresh_fetch
            else ()
        )
        evidence = DhmRequestEvidence(
            batch_id=CaptureBatchId(uuid4()),
            station_id=binding.station_id,
            provider=CanonicalProviderIdentity.parse(dhm.endpoint, 1),
            requested_intervals=(
                CaptureRequestInterval(
                    lower_exclusive=_NOW - timedelta(seconds=1),
                    upper_exclusive=now + timedelta(seconds=1),
                ),
            ),
            request_started_at=now,
            retrieved_at=now,
            outcome=NativeFetchOutcome.COMPLETE,
            request_count=1,
            accepted_bytes=10,
        )
        batch = HydroScraperBatchResult(
            outcomes=(
                StationFetchOutcome(
                    station_id=binding.station_id,
                    observations=rows,
                    dhm_request_evidence=evidence,
                    failure_cause=None,
                    failure_detail=None,
                ),
            )
        )
        adapter = SimpleNamespace(fetch_observations_batch=lambda *_: batch)
        configured = ProvisionalRatingConfig(
            tenant_id=binding.tenant_id,
            station_id=binding.station_id,
            station_code=binding.station_code,
            model_ids=("fixture",),
            reference_content=canonical_content(asdict(binding.reference)),
            reconciliation_days=1,
            evidence_mode="native_capture",
        )
        monkeypatch.setattr(flow, "_load_adapter_config", lambda: dhm)
        monkeypatch.setattr(
            flow, "_fetch_observations_task", flow._fetch_observations_task.fn
        )
        monkeypatch.setattr(flow, "_run_qc_task", flow._run_qc_task.fn)
        monkeypatch.setattr(
            flow,
            "_reconcile_native_ratings_task",
            flow._reconcile_native_ratings_task.fn,
        )

        def generic_forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("native water levels must not use generic persistence")

        monkeypatch.setattr(flow, "_store_raw_task", generic_forbidden)
        if fetch_case == "backfill":
            monkeypatch.setattr(flow, "_fetch_observations_task", generic_forbidden)
        captures = PgNativeCaptureStore(
            savepoint_factory(db_connection), clock=lambda: now, id_gen=uuid4
        )
        result = flow.ingest_observations_flow.fn(
            station_store=PgStationStore(db_connection),
            obs_store=PgObservationStore(db_connection),
            baseline_store=FakeClimBaselineStore(),
            adapter=adapter,
            qc_rules=QcRuleSet(version="none", rules=()),
            deployment_config=SimpleNamespace(
                provisional_ratings=(configured,), enable_observation_alerts=False
            ),
            clock=lambda: now,
            native_capture_store=captures,
            rating_derived_store=PgRatingDerivedStore(savepoint_factory(db_connection)),
            rating_backfill_station_ids=[str(binding.station_id)]
            if fetch_case == "backfill"
            else None,
            rating_backfill_start=_NOW if fetch_case == "backfill" else None,
            rating_backfill_end=end if fetch_case == "backfill" else None,
        )
        assert result.observations_stored == int(fresh_fetch)
        assert result.qc_newly_checked == 1
        assert result.stations_failed == 0
        assert sum(part.accepted for part in result.rating_results) == (
            2 if fresh_fetch else 1
        )


class TestNativeReconciliationOutcomes:
    @pytest.mark.parametrize("audit_failure", ["once", "always"])
    def test_audit_failure_rolls_back_science_and_reports_retry_state(
        self,
        db_connection: sa.Connection,
        source_binding: tuple[NativeRatingBinding, DhmConfig, Observation],
        monkeypatch: pytest.MonkeyPatch,
        audit_failure: Literal["once", "always"],
    ) -> None:
        from sapphire_flow.db.metadata import audit_log, rating_derived_discharge
        from sapphire_flow.store.audit_log_store import PgAuditLogStore
        from sapphire_flow.types.rating_reconciliation import AuditRecordState

        binding, dhm, observation = source_binding
        capture(db_connection, binding)
        original_append = PgAuditLogStore.append_entry
        calls = 0

        def fail_append(store: PgAuditLogStore, entry: AuditEntry) -> None:
            nonlocal calls
            calls += 1
            if calls == 1 or audit_failure == "always":
                raise ValueError("fixture audit unavailable")
            original_append(store, entry)

        monkeypatch.setattr(PgAuditLogStore, "append_entry", fail_append)
        result = PgRatingDerivedStore(
            savepoint_factory(db_connection)
        ).reconcile_chunk_outcome(
            native_binding(binding),
            dhm,
            start=_NOW,
            end=_NOW + timedelta(hours=1),
            at=_NOW,
        )
        assert result.inserted == 0
        assert result.audit_record_state is (
            AuditRecordState.RECORDED
            if audit_failure == "once"
            else AuditRecordState.FAILED
        )
        assert (
            db_connection.scalar(
                sa.select(sa.func.count()).select_from(rating_derived_discharge)
            )
            == 0
        )
        assert (
            PgObservationStore(db_connection).fetch_observation_by_id(observation.id)
            is not None
        )
        summaries = (
            db_connection.execute(
                sa.select(audit_log.c.detail).where(
                    audit_log.c.detail["command"].astext == "rating-derived-reconcile",
                )
            )
            .scalars()
            .all()
        )
        assert len(summaries) == (1 if audit_failure == "once" else 0)
        if summaries:
            assert summaries[0]["held_counts"] == {"storage_error": 1}

    def test_valid_sources_are_persisted_alongside_explicit_missing_capture_hold(
        self,
        db_connection: sa.Connection,
        source_binding: tuple[NativeRatingBinding, DhmConfig, Observation],
    ) -> None:
        binding, dhm, _ = source_binding
        capture(db_connection, binding)
        PgObservationStore(db_connection).store_raw_observations(
            [
                RawObservation(
                    station_id=binding.station_id,
                    timestamp=_NOW + timedelta(minutes=5),
                    parameter="water_level",
                    value=0.3,
                    source=ObservationSource.MEASURED,
                )
            ]
        )
        store = PgRatingDerivedStore(savepoint_factory(db_connection))
        result = store.reconcile_chunk_outcome(
            native_binding(binding),
            dhm,
            start=_NOW,
            end=_NOW + timedelta(hours=1),
            at=_NOW + timedelta(hours=1),
        )
        assert (result.accepted, result.inserted) == (1, 1)
        assert [held.reason for held in result.held] == [
            RatingHoldReason.MISSING_CAPTURE
        ]


class TestLegacyNativeHistoryBarrier:
    def test_capture_blocks_legacy_even_after_uncaptured_correction(
        self,
        db_connection: sa.Connection,
        source_binding: tuple[NativeRatingBinding, DhmConfig, Observation],
    ) -> None:
        binding, dhm, observation = source_binding
        store = PgRatingDerivedStore(savepoint_factory(db_connection))
        window = dict(start=_NOW, end=_NOW + timedelta(hours=1), at=_NOW)
        (original,) = store.reconcile_chunk(binding, dhm, **window)
        capture(db_connection, binding)
        for changed in (False, True):
            if changed:
                PgObservationStore(db_connection).store_raw_observations(
                    [
                        RawObservation(
                            station_id=binding.station_id,
                            timestamp=_NOW,
                            parameter="water_level",
                            value=0.3,
                            source=ObservationSource.MEASURED,
                        )
                    ]
                )
            outcome = store.reconcile_chunk_outcome(binding, dhm, **window)
            assert outcome.inserted == 0
            assert [hold.reason for hold in outcome.held] == [
                RatingHoldReason.SOURCE_IDENTITY_MISMATCH
            ]
            with pytest.raises(
                RatingInputsUnavailable, match="source_identity_mismatch"
            ):
                store.read_current(binding, dhm, **window)
        from sapphire_flow.services.rating_inputs import NativeRatingInputs

        inputs = NativeRatingInputs(reader=store, bindings=(binding,), dhm=dhm)
        with pytest.raises(RatingInputsUnavailable, match="source_identity_mismatch"):
            inputs.read_window(binding.station_id, ModelId("fixture"), **window)
        native = store.read_current_window(native_binding(binding), dhm, **window)
        assert [hold.reason for hold in native.held] == [
            RatingHoldReason.MISSING_CAPTURE
        ]
        from sapphire_flow.db.metadata import rating_derived_discharge

        stored = db_connection.scalar(
            sa.select(rating_derived_discharge.c.content).where(
                rating_derived_discharge.c.fingerprint == original.fingerprint
            )
        )
        assert stored == original.content
