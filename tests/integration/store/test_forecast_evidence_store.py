from __future__ import annotations

import hashlib
import json
import random
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.db.metadata import (
    forecast_evidence,
    forecast_evidence_blobs,
    forecast_preservation_attestations,
    forecast_values,
    forecasts,
    model_artifacts,
    station_thresholds,
)
from sapphire_flow.services.forecast_evidence import (
    capture_combined_evidence,
    capture_station_evidence,
    restore_snapshot,
)
from sapphire_flow.services.forecast_preservation import assess_effective_preservation
from sapphire_flow.store.forecast_preservation_store import (
    PgForecastPreservationStore,
)
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import ForecastQcRuleSet, QcFlag, StationThreshold
from sapphire_flow.types.enums import QcStatus, ThresholdSource
from sapphire_flow.types.forecast_evidence import (
    EvidenceStatus,
    ForecastEvidence,
    StationSourceEvidence,
)
from sapphire_flow.types.forecast_preservation import (
    BackupProof,
    BackupProofStatus,
    PreservationAttestation,
    PreservationStatus,
)
from tests.conftest import make_observation
from tests.integration.store.test_forecast_store import (
    _ISSUED_B,
    _make_forecast,
    _seed_artifact,
    _seed_model,
    _seed_station,
    savepoint_factory,
)
from tests.unit.adapters.test_forecast_interface_adapter_predict import (
    _station_model_inputs,
)


def _evidence(thresholds: tuple[StationThreshold, ...] = ()) -> ForecastEvidence:
    snapshot = b"compressed-as-used-inputs"
    artifact = b"model-weights"
    return ForecastEvidence(
        status=EvidenceStatus.COMPLETE,
        manifest_json='{"schema_version":1}',
        snapshot=snapshot,
        snapshot_sha256=hashlib.sha256(snapshot).hexdigest(),
        artifact=artifact,
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
        thresholds=thresholds,
    )


class TestForecastEvidenceStore:
    def test_observation_revision_does_not_change_as_used_snapshot(
        self, db_connection: sa.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_IMAGE_DIGEST", "sha256:" + "a" * 64)
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        flag = QcFlag(
            rule_id="range_check",
            rule_version="1",
            status=QcStatus.QC_PASSED,
            detail="within range",
        )
        observed = replace(
            make_observation(
                station_id=sid,
                timestamp=ensure_utc(datetime(2025, 1, 1, tzinfo=UTC)),
                qc_status=QcStatus.QC_PASSED,
                value=42.0,
                rng=random.Random(3),
            ),
            qc_flags=[flag],
            qc_rule_version="1",
        )
        observation_store = PgObservationStore(db_connection)
        observation_store.store_observations([observed])
        inputs = replace(
            _station_model_inputs(),
            station_id=sid,
            source_evidence=StationSourceEvidence(observations=(observed,)),
        )
        evidence = capture_station_evidence(
            inputs=inputs,
            model=object(),
            model_id=mid,
            artifact_bytes=b"model-weights",
            prior_state=None,
            rng_state=random.Random(4).getstate(),
            config=DeploymentConfig(max_retention_days=1000),
            qc_rules=ForecastQcRuleSet(version="1", rules=()),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=None,
        ).with_thresholds(())
        forecast = replace(_make_forecast(sid, mid, aid), evidence=evidence)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_forecast(forecast)
        observation_store.store_observations(
            [replace(observed, value=99.0, qc_flags=[], qc_rule_version="2")]
        )

        saved = store.fetch_evidence(forecast.id)
        assert saved is not None
        assert saved.status is EvidenceStatus.INCOMPLETE
        assert saved.snapshot is not None
        source = restore_snapshot(saved.snapshot)["source_records"]["observations"][0]
        assert source["value"] == 42.0
        assert source["qc_flags"][0]["rule_version"] == "1"
        assert source["qc_rule_version"] == "1"

    def test_as_used_threshold_survives_current_row_change(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        threshold = StationThreshold(
            station_id=sid,
            danger_level="moderate",
            parameter="discharge",
            value=150.0,
            source=ThresholdSource.AUTHORITY,
            created_at=ensure_utc(datetime(2025, 1, 1, tzinfo=UTC)),
            updated_at=ensure_utc(datetime(2025, 1, 1, tzinfo=UTC)),
        )
        PgStationStore(db_connection).store_thresholds([threshold])
        forecast = replace(
            _make_forecast(sid, mid, aid), evidence=_evidence((threshold,))
        )
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_forecast(forecast)

        db_connection.execute(
            sa.update(station_thresholds)
            .where(station_thresholds.c.station_id == sid)
            .values(value=250.0)
        )
        saved = store.fetch_evidence(forecast.id)
        assert saved is not None
        assert saved.status is EvidenceStatus.COMPLETE
        assert json.loads(saved.thresholds_json or "[]")[0]["value"] == 150.0
        assert saved.snapshot == b"compressed-as-used-inputs"
        assert saved.artifact == b"model-weights"

    def test_identical_blobs_are_deduplicated(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        for issued_at in (None, _ISSUED_B):
            forecast = _make_forecast(
                sid, mid, aid, **({"issued_at": issued_at} if issued_at else {})
            )
            store.store_forecast(replace(forecast, evidence=_evidence()))
        assert (
            db_connection.scalar(
                sa.select(sa.func.count()).select_from(forecast_evidence)
            )
            == 2
        )
        assert (
            db_connection.scalar(
                sa.select(sa.func.count()).select_from(forecast_evidence_blobs)
            )
            == 2
        )

    def test_combination_marks_missing_persisted_contributor_incomplete(
        self, db_connection: sa.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SAPPHIRE_IMAGE_DIGEST", "sha256:" + "a" * 64)
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        persisted = replace(_make_forecast(sid, mid, aid), evidence=_evidence())
        missing = replace(
            _make_forecast(sid, mid, aid, issued_at=_ISSUED_B), evidence=_evidence()
        )
        store.store_forecast(persisted)
        combined_evidence = capture_combined_evidence(
            model_id=mid,
            strategy="pooled",
            contributors=(persisted, missing),
            weights=None,
            qc_rules=ForecastQcRuleSet(version="1", rules=()),
            qc_overrides=[],
            baselines=[],
            water_level_datum_masl=None,
        ).with_thresholds(())
        combined = replace(
            _make_forecast(sid, mid, aid, issued_at=_ISSUED_B),
            combination_strategy="pooled",
            evidence=combined_evidence,
        )
        store.store_forecast(combined)
        saved = store.fetch_evidence(combined.id)
        assert saved is not None
        assert saved.status is EvidenceStatus.INCOMPLETE
        assert "contributor_evidence_not_persisted" in (saved.reason or "")

    def test_bad_hash_rolls_back_forecast_and_values(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        forecast = replace(
            _make_forecast(sid, mid, aid),
            evidence=replace(_evidence(), snapshot_sha256="0" * 64),
        )
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        with pytest.raises(ValueError, match="hash mismatch"):
            store.store_forecast(forecast)
        assert (
            db_connection.scalar(
                sa.select(sa.func.count())
                .select_from(forecasts)
                .where(forecasts.c.id == forecast.id)
            )
            == 0
        )

    def test_corrupt_deduplicated_blob_rolls_back_forecast(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        evidence = _evidence()
        assert evidence.snapshot_sha256 is not None
        db_connection.execute(
            sa.insert(forecast_evidence_blobs).values(
                sha256=evidence.snapshot_sha256,
                payload=b"corrupt",
                byte_length=len(b"corrupt"),
            )
        )
        forecast = replace(_make_forecast(sid, mid, aid), evidence=evidence)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        with pytest.raises(
            ValueError, match="retained forecast evidence blob mismatch"
        ):
            store.store_forecast(forecast)
        assert (
            db_connection.scalar(
                sa.select(sa.func.count())
                .select_from(forecasts)
                .where(forecasts.c.id == forecast.id)
            )
            == 0
        )

    def test_new_forecast_without_capture_is_marked_incomplete(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        forecast = _make_forecast(sid, mid, aid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_forecast(forecast)
        saved = store.fetch_evidence(forecast.id)
        assert saved is not None
        assert saved.status is EvidenceStatus.INCOMPLETE
        assert "prediction_capture_unavailable" in (saved.reason or "")

    def test_pre_capture_forecast_is_marked_incomplete(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        forecast = _make_forecast(sid, mid, aid)
        db_connection.execute(
            sa.insert(forecasts).values(
                id=forecast.id,
                station_id=sid,
                model_id=mid,
                model_artifact_id=aid,
                issued_at=forecast.issued_at,
                representation=forecast.representation.value,
                parameter=forecast.ensemble.parameter,
                units=forecast.ensemble.units,
            )
        )
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        saved = store.fetch_evidence(forecast.id)
        assert saved is not None
        assert saved.status is EvidenceStatus.INCOMPLETE
        assert saved.reason == "pre_capture_forecast"

    @pytest.mark.parametrize("table", (forecast_evidence, forecast_evidence_blobs))
    @pytest.mark.parametrize("action", ("update", "delete", "truncate"))
    def test_owner_cannot_mutate_evidence(
        self, db_connection: sa.Connection, table: sa.Table, action: str
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_forecast(
            replace(_make_forecast(sid, mid, aid), evidence=_evidence())
        )
        statement = {
            "update": sa.update(table).values(created_at=sa.func.now()),
            "delete": sa.delete(table),
            "truncate": sa.text(f"TRUNCATE {table.name} CASCADE"),
        }[action]
        with (
            pytest.raises(sa.exc.DBAPIError, match="append-only"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(statement)

    @pytest.mark.parametrize("table", (forecast_evidence, forecast_evidence_blobs))
    @pytest.mark.parametrize("action", ("update", "delete", "truncate"))
    def test_privileged_nonowner_cannot_mutate_evidence(
        self, db_connection: sa.Connection, table: sa.Table, action: str
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_forecast(
            replace(_make_forecast(sid, mid, aid), evidence=_evidence())
        )
        role = f"evidence_reviewer_{uuid4().hex[:8]}"
        db_connection.execute(sa.text(f"CREATE ROLE {role}"))
        db_connection.execute(
            sa.text(
                f"GRANT ALL ON forecast_evidence, forecast_evidence_blobs, "
                f"forecast_preservation_attestations TO {role}"
            )
        )
        statement = {
            "update": f"UPDATE {table.name} SET created_at = now()",
            "delete": f"DELETE FROM {table.name}",
            "truncate": f"TRUNCATE {table.name} CASCADE",
        }[action]
        with (
            pytest.raises(sa.exc.DBAPIError, match="append-only"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(sa.text(f"SET ROLE {role}"))
            db_connection.execute(sa.text(statement))


class TestForecastPreservation:
    def test_legacy_output_update_keeps_new_value(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        forecast_id = uuid4()
        issued_at = datetime(2026, 9, 25, tzinfo=UTC)
        db_connection.execute(
            sa.insert(forecasts).values(
                id=forecast_id,
                station_id=sid,
                model_id=mid,
                issued_at=issued_at,
                representation="members",
                parameter="discharge",
                units="m3/s",
            )
        )
        db_connection.execute(
            sa.insert(forecast_values).values(
                id=uuid4(),
                forecast_id=forecast_id,
                issued_at=issued_at,
                valid_time=issued_at,
                lead_time_hours=0,
                member_id=0,
                value=1.0,
            )
        )
        db_connection.execute(
            sa.update(forecast_values)
            .where(forecast_values.c.forecast_id == forecast_id)
            .values(value=999.0)
        )
        values = db_connection.scalars(
            sa.select(forecast_values.c.value).where(
                forecast_values.c.forecast_id == forecast_id
            )
        ).all()
        assert values and set(values) == {999.0}
        db_connection.execute(
            sa.delete(forecast_values).where(
                forecast_values.c.forecast_id == forecast_id
            )
        )
        db_connection.execute(sa.delete(forecasts).where(forecasts.c.id == forecast_id))
        assert (
            db_connection.scalar(
                sa.select(sa.func.count())
                .select_from(forecasts)
                .where(forecasts.c.id == forecast_id)
            )
            == 0
        )

    def test_evidence_linked_output_and_artifact_survive_cleanup(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        forecast = replace(_make_forecast(sid, mid, aid), evidence=_evidence())
        PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        ).store_forecast(forecast)
        for statement in (
            sa.delete(forecast_values).where(
                forecast_values.c.forecast_id == forecast.id
            ),
            sa.update(forecast_values)
            .where(forecast_values.c.forecast_id == forecast.id)
            .values(value=999.0),
            sa.insert(forecast_values).values(
                id=uuid4(),
                forecast_id=forecast.id,
                issued_at=datetime(2026, 9, 25, tzinfo=UTC),
                valid_time=datetime(2026, 9, 26, tzinfo=UTC),
                lead_time_hours=24,
                member_id=0,
                value=999.0,
            ),
            sa.delete(forecasts).where(forecasts.c.id == forecast.id),
            sa.update(forecasts)
            .where(forecasts.c.id == forecast.id)
            .values(parameter="water_level"),
            sa.delete(model_artifacts).where(model_artifacts.c.id == aid),
            sa.text("TRUNCATE forecast_values CASCADE"),
        ):
            with (
                pytest.raises(sa.exc.DBAPIError, match="evidence-linked"),
                db_connection.begin_nested(),
            ):
                db_connection.execute(statement)
        db_connection.execute(
            sa.update(forecasts)
            .where(forecasts.c.id == forecast.id)
            .values(status="reviewed", version=2)
        )
        assert (
            db_connection.scalar(
                sa.select(forecasts.c.status).where(forecasts.c.id == forecast.id)
            )
            == "reviewed"
        )
        assert (
            db_connection.scalar(
                sa.select(sa.func.count())
                .select_from(forecast_values)
                .where(forecast_values.c.forecast_id == forecast.id)
            )
            > 0
        )

    def test_attestation_keeps_capture_status_and_is_append_only(
        self, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, sid, mid)
        image = "sha256:" + "a" * 64
        source = replace(
            _evidence(),
            status=EvidenceStatus.INCOMPLETE,
            manifest_json=json.dumps({"runtime_image_digest": image}),
            reason="runtime_image_bytes_unpinned",
        )
        forecast = replace(_make_forecast(sid, mid, aid), evidence=source)
        store = PgForecastStore(
            db_connection, transaction_factory=savepoint_factory(db_connection)
        )
        store.store_forecast(forecast)
        saved = store.fetch_evidence(forecast.id)
        assert saved is not None
        values_hash = db_connection.scalar(
            sa.text(
                "SELECT encode(sha256(convert_to("
                "COALESCE(jsonb_agg(jsonb_build_array(id, issued_at, "
                "valid_time, lead_time_hours, member_id, quantile, value) "
                "ORDER BY id)::text, '[]'), 'UTF8')), 'hex') "
                "FROM forecast_values WHERE forecast_id = :forecast_id"
            ),
            {"forecast_id": forecast.id},
        )
        attestation = PreservationAttestation(
            id=uuid4(),
            forecast_id=forecast.id,
            backup_id=uuid4(),
            capture_manifest_sha256=hashlib.sha256(
                saved.manifest_json.encode()
            ).hexdigest(),
            snapshot_sha256=saved.snapshot_sha256 or "",
            forecast_values_sha256=values_hash,
            artifact_sha256=saved.artifact_sha256,
            runtime_image_digest=image,
            backup_manifest_sha256="b" * 64,
            database_dump_sha256="c" * 64,
            image_archive_sha256="d" * 64,
            restored_at=datetime(2026, 9, 25, tzinfo=UTC),
        )
        preservation = PgForecastPreservationStore(db_connection)
        with pytest.raises(ValueError, match="live forecast chain"):
            preservation.append(
                replace(
                    attestation,
                    id=uuid4(),
                    backup_id=uuid4(),
                    forecast_values_sha256="0" * 64,
                )
            )
        preservation.append(attestation)
        preservation.append(replace(attestation, id=uuid4()))
        assert (
            db_connection.scalar(
                sa.select(sa.func.count())
                .select_from(forecast_preservation_attestations)
                .where(forecast_preservation_attestations.c.forecast_id == forecast.id)
            )
            == 1
        )
        with pytest.raises(ValueError, match="conflicts with existing proof"):
            preservation.append(
                replace(attestation, id=uuid4(), image_archive_sha256="9" * 64)
            )

        assert saved.status is EvidenceStatus.INCOMPLETE
        assert (
            assess_effective_preservation(
                forecast.id,
                saved,
                preservation.latest(forecast.id),
                BackupProof(
                    status=BackupProofStatus.VERIFIED,
                    backup_id=attestation.backup_id,
                    manifest_sha256=attestation.backup_manifest_sha256,
                    database_dump_sha256=attestation.database_dump_sha256,
                    image_archives=((image, attestation.image_archive_sha256),),
                    sample_forecast_id=forecast.id,
                    capture_manifest_sha256=attestation.capture_manifest_sha256,
                    snapshot_sha256=attestation.snapshot_sha256,
                    forecast_values_sha256=attestation.forecast_values_sha256,
                    artifact_sha256=attestation.artifact_sha256,
                    runtime_image_digest=image,
                    restored_at=attestation.restored_at,
                ),
            ).effective_status
            is PreservationStatus.COMPLETE
        )
        for statement in (
            sa.update(forecast_preservation_attestations).values(
                restored_at=datetime(2026, 9, 26, tzinfo=UTC)
            ),
            sa.delete(forecast_preservation_attestations),
            sa.text("TRUNCATE forecast_preservation_attestations"),
        ):
            with (
                pytest.raises(sa.exc.DBAPIError, match="append-only"),
                db_connection.begin_nested(),
            ):
                db_connection.execute(statement)
