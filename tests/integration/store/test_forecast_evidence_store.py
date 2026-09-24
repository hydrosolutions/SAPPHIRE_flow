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
    forecasts,
    station_thresholds,
)
from sapphire_flow.services.forecast_evidence import (
    capture_combined_evidence,
    capture_station_evidence,
    restore_snapshot,
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

    def test_pre_capture_forecast_is_marked_incomplete(
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

    @pytest.mark.parametrize("action", ("update", "delete", "truncate"))
    def test_privileged_nonowner_cannot_mutate_evidence(
        self, db_connection: sa.Connection, action: str
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
        db_connection.execute(sa.text(f"GRANT ALL ON forecast_evidence TO {role}"))
        statement = {
            "update": "UPDATE forecast_evidence SET created_at = now()",
            "delete": "DELETE FROM forecast_evidence",
            "truncate": "TRUNCATE forecast_evidence CASCADE",
        }[action]
        with (
            pytest.raises(sa.exc.DBAPIError, match="append-only"),
            db_connection.begin_nested(),
        ):
            db_connection.execute(sa.text(f"SET ROLE {role}"))
            db_connection.execute(sa.text(statement))
