"""Plan 157 T3 (G4/G7) — external-artifact import.

Red-first acceptance criteria (plan text):
- an undeserializable blob fails loudly, before any write.
- a valid import yields a promotable artifact with external provenance.
- an acceptance test from the public boundary proving `train()` is never
  called.
Plus the plan's other stated invariants: config/artifact mismatch fails
loudly before any write; the tenant is DERIVED from the target, never
trusted from a caller-supplied argument; `trained_at` stays distinct from
`imported_at`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from sapphire_flow.exceptions import (
    ConfigurationError,
    ModelLoadError,
    TenantIsolationError,
)
from sapphire_flow.services.model_import import import_external_artifact
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ModelArtifactStatus
from sapphire_flow.types.ids import ModelId, TenantId
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeArtifactProvenanceStore,
    FakeModelArtifactStore,
    FakeStationStore,
)

_TRAINED_AT = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_IMPORTED_AT = ensure_utc(datetime(2026, 8, 13, tzinfo=UTC))
_MODEL_ID = ModelId("aquacast_cmal_pool_pt")


class _SpyModel:
    """Structurally satisfies the deserialize_artifact boundary
    `import_external_artifact` calls; `train` is spied on so tests can
    assert the public-boundary invariant that it is NEVER called."""

    config_hash = "shim-config-abc123"

    def __init__(self, *, deserialize_ok: bool = True) -> None:
        self._deserialize_ok = deserialize_ok
        self.train_calls = 0
        self.deserialize_calls: list[bytes] = []

    def train(self, *args: object, **kwargs: object) -> object:  # pragma: no cover
        self.train_calls += 1
        raise AssertionError("train() must never be called from the import path")

    def deserialize_artifact(self, raw: bytes) -> object:
        self.deserialize_calls.append(raw)
        if not self._deserialize_ok:
            raise ValueError("corrupt weights blob")
        return {"weights": raw}


def _clock() -> object:
    return _IMPORTED_AT


class TestUndeserializableBlobFailsLoudly:
    def test_raises_model_load_error(self) -> None:
        model = _SpyModel(deserialize_ok=False)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        with pytest.raises(ModelLoadError, match="failed to deserialize"):
            import_external_artifact(
                model=model,  # type: ignore[arg-type]
                model_id=_MODEL_ID,
                artifact_bytes=b"not-a-real-checkpoint",
                artifact_store=artifact_store,
                trained_at=_TRAINED_AT,
                clock=_clock,
                station_id=station.id,
                station_store=station_store,
                provenance_recorder=provenance_store,
            )

    def test_no_row_or_provenance_written_before_the_failure(self) -> None:
        model = _SpyModel(deserialize_ok=False)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        with pytest.raises(ModelLoadError):
            import_external_artifact(
                model=model,  # type: ignore[arg-type]
                model_id=_MODEL_ID,
                artifact_bytes=b"not-a-real-checkpoint",
                artifact_store=artifact_store,
                trained_at=_TRAINED_AT,
                clock=_clock,
                station_id=station.id,
                station_store=station_store,
                provenance_recorder=provenance_store,
            )

        assert (
            artifact_store.fetch_artifacts_by_status(
                _MODEL_ID, ModelArtifactStatus.TRAINING
            )
            == []
        )
        assert provenance_store._records == {}  # noqa: SLF001

    def test_train_never_called(self) -> None:
        model = _SpyModel(deserialize_ok=False)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        with pytest.raises(ModelLoadError):
            import_external_artifact(
                model=model,  # type: ignore[arg-type]
                model_id=_MODEL_ID,
                artifact_bytes=b"not-a-real-checkpoint",
                artifact_store=artifact_store,
                trained_at=_TRAINED_AT,
                clock=_clock,
                station_id=station.id,
                station_store=station_store,
                provenance_recorder=provenance_store,
            )

        assert model.train_calls == 0


class TestConfigArtifactMismatchFailsLoudly:
    def test_raises_configuration_error_before_deserializing(self) -> None:
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        with pytest.raises(ConfigurationError, match="config/artifact mismatch"):
            import_external_artifact(
                model=model,  # type: ignore[arg-type]
                model_id=_MODEL_ID,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
                trained_at=_TRAINED_AT,
                clock=_clock,
                station_id=station.id,
                station_store=station_store,
                provenance_recorder=provenance_store,
                expected_config_hash="a-completely-different-hash",
            )

        # Fails BEFORE deserialize_artifact is even attempted.
        assert model.deserialize_calls == []
        assert (
            artifact_store.fetch_artifacts_by_status(
                _MODEL_ID, ModelArtifactStatus.TRAINING
            )
            == []
        )


class TestValidImportYieldsPromotableArtifactWithProvenance:
    def test_artifact_is_active_after_import(self) -> None:
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        artifact_id = import_external_artifact(
            model=model,  # type: ignore[arg-type]
            model_id=_MODEL_ID,
            artifact_bytes=b"real-checkpoint-bytes",
            artifact_store=artifact_store,
            trained_at=_TRAINED_AT,
            clock=_clock,
            station_id=station.id,
            station_store=station_store,
            provenance_recorder=provenance_store,
            source_repository="hydrosolutions/sapphire-aquacast",
            source_commit="deadbeef",
            expected_config_hash="shim-config-abc123",
            imported_by="operator@hydrosolutions.ch",
        )

        record = artifact_store.fetch_artifact_record(artifact_id)
        assert record is not None
        assert record.status == ModelArtifactStatus.ACTIVE

    def test_provenance_row_records_external_source(self) -> None:
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        artifact_id = import_external_artifact(
            model=model,  # type: ignore[arg-type]
            model_id=_MODEL_ID,
            artifact_bytes=b"real-checkpoint-bytes",
            artifact_store=artifact_store,
            trained_at=_TRAINED_AT,
            clock=_clock,
            station_id=station.id,
            station_store=station_store,
            provenance_recorder=provenance_store,
            source_repository="hydrosolutions/sapphire-aquacast",
            source_commit="deadbeef",
            expected_config_hash="shim-config-abc123",
            imported_by="operator@hydrosolutions.ch",
        )

        provenance = provenance_store.fetch(artifact_id)
        assert provenance is not None
        assert provenance.source_repository == "hydrosolutions/sapphire-aquacast"
        assert provenance.source_commit == "deadbeef"
        assert provenance.config_hash == "shim-config-abc123"
        assert provenance.imported_by == "operator@hydrosolutions.ch"

    def test_train_never_called_on_the_success_path(self) -> None:
        """Plan 157 T3's explicit public-boundary acceptance criterion."""
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        import_external_artifact(
            model=model,  # type: ignore[arg-type]
            model_id=_MODEL_ID,
            artifact_bytes=b"real-checkpoint-bytes",
            artifact_store=artifact_store,
            trained_at=_TRAINED_AT,
            clock=_clock,
            station_id=station.id,
            station_store=station_store,
            provenance_recorder=provenance_store,
        )

        assert model.train_calls == 0

    def test_trained_at_distinct_from_imported_at(self) -> None:
        """trained_at (the external training-completion instant, supplied
        by the caller) must never be conflated with imported_at (clock(),
        this import's own instant)."""
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        artifact_id = import_external_artifact(
            model=model,  # type: ignore[arg-type]
            model_id=_MODEL_ID,
            artifact_bytes=b"real-checkpoint-bytes",
            artifact_store=artifact_store,
            trained_at=_TRAINED_AT,
            clock=_clock,
            station_id=station.id,
            station_store=station_store,
            provenance_recorder=provenance_store,
        )

        record = artifact_store.fetch_artifact_record(artifact_id)
        provenance = provenance_store.fetch(artifact_id)
        assert record is not None
        assert provenance is not None
        assert record.trained_at == _TRAINED_AT
        assert provenance.imported_at == _IMPORTED_AT
        assert record.trained_at != provenance.imported_at


class TestTenantDerivedFromTargetNotTrustedArgument:
    def test_supplied_tenant_mismatching_derived_tenant_is_rejected(self) -> None:
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        station_store = FakeStationStore()

        station = make_station_config()
        station_store.store_station(station)
        wrong_tenant = TenantId(uuid4())
        assert wrong_tenant != station.tenant_id

        with pytest.raises(TenantIsolationError, match="does not match"):
            import_external_artifact(
                model=model,  # type: ignore[arg-type]
                model_id=_MODEL_ID,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
                trained_at=_TRAINED_AT,
                clock=_clock,
                station_id=station.id,
                station_store=station_store,
                provenance_recorder=provenance_store,
                supplied_target_tenant_id=wrong_tenant,
            )
        assert (
            artifact_store.fetch_artifacts_by_status(
                _MODEL_ID, ModelArtifactStatus.TRAINING
            )
            == []
        )

    def test_supplied_tenant_matching_derived_tenant_is_accepted(self) -> None:
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        artifact_id = import_external_artifact(
            model=model,  # type: ignore[arg-type]
            model_id=_MODEL_ID,
            artifact_bytes=b"real-checkpoint-bytes",
            artifact_store=artifact_store,
            trained_at=_TRAINED_AT,
            clock=_clock,
            station_id=station.id,
            station_store=station_store,
            provenance_recorder=provenance_store,
            supplied_target_tenant_id=station.tenant_id,
        )
        assert artifact_store.fetch_artifact_record(artifact_id) is not None

    def test_neither_station_nor_group_id_is_rejected(self) -> None:
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()

        with pytest.raises(ConfigurationError, match="exactly one of"):
            import_external_artifact(
                model=model,  # type: ignore[arg-type]
                model_id=_MODEL_ID,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
                trained_at=_TRAINED_AT,
                clock=_clock,
                provenance_recorder=provenance_store,
            )
