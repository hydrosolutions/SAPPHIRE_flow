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
from sapphire_flow.types.enums import ArtifactScope, ModelArtifactStatus
from sapphire_flow.types.ids import ModelId, StationGroupId, TenantId
from tests.conftest import make_station_config
from tests.fakes.fake_stores import (
    FakeArtifactProvenanceStore,
    FakeModelArtifactStore,
    FakeModelStore,
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
    artifact_scope = ArtifactScope.STATION
    display_name = "Spy Model"
    description = "test double"
    data_requirements = None

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
        model_store = FakeModelStore()
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
                model_store=model_store,
            )

    def test_no_row_or_provenance_written_before_the_failure(self) -> None:
        model = _SpyModel(deserialize_ok=False)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()
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
                model_store=model_store,
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
        model_store = FakeModelStore()
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
                model_store=model_store,
            )

        assert model.train_calls == 0


class TestConfigArtifactMismatchFailsLoudly:
    def test_raises_configuration_error_before_deserializing(self) -> None:
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()
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
                model_store=model_store,
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

    def test_omitted_expected_config_hash_is_observable_via_a_warning_log(
        self,
    ) -> None:
        """The drift check is opt-in per call (a caller who omits
        expected_config_hash gets NO check) — that must at least be
        OBSERVABLE, not a silent no-op."""
        from structlog.testing import capture_logs

        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        with capture_logs() as logs:
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
                model_store=model_store,
                # expected_config_hash deliberately omitted.
            )

        warnings = [
            e for e in logs if e.get("event") == "model_import.no_expected_config_hash"
        ]
        assert len(warnings) == 1
        assert warnings[0]["log_level"] == "warning"


class TestValidImportYieldsPromotableArtifactWithProvenance:
    def test_artifact_is_active_after_import(self) -> None:
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()
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
            model_store=model_store,
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
        model_store = FakeModelStore()
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
            model_store=model_store,
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
        model_store = FakeModelStore()
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
            model_store=model_store,
        )

        assert model.train_calls == 0

    def test_trained_at_distinct_from_imported_at(self) -> None:
        """trained_at (the external training-completion instant, supplied
        by the caller) must never be conflated with imported_at (clock(),
        this import's own instant)."""
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()
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
            model_store=model_store,
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
        model_store = FakeModelStore()
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
                model_store=model_store,
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
        model_store = FakeModelStore()
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
            model_store=model_store,
            supplied_target_tenant_id=station.tenant_id,
        )
        assert artifact_store.fetch_artifact_record(artifact_id) is not None

    def test_neither_station_nor_group_id_is_rejected(self) -> None:
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()

        with pytest.raises(ConfigurationError, match="exactly one of"):
            import_external_artifact(
                model=model,  # type: ignore[arg-type]
                model_id=_MODEL_ID,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
                trained_at=_TRAINED_AT,
                clock=_clock,
                provenance_recorder=provenance_store,
                model_store=model_store,
            )


class _GroupSpyModel(_SpyModel):
    artifact_scope = ArtifactScope.GROUP


class TestModelScopeValidatedAgainstTarget:
    """G6: a GROUP-scoped model imported against a station_id (or vice
    versa) must be rejected — reported success would otherwise be
    operationally invisible to whichever lookup path queries by the OTHER
    scope."""

    def test_group_scoped_model_imported_against_station_id_is_rejected(
        self,
    ) -> None:
        model = _GroupSpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)
        group_model_id = ModelId("aquacast_group_pt")

        with pytest.raises(ConfigurationError, match="GROUP-scoped"):
            import_external_artifact(
                model=model,  # type: ignore[arg-type]
                model_id=group_model_id,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
                trained_at=_TRAINED_AT,
                clock=_clock,
                station_id=station.id,
                station_store=station_store,
                provenance_recorder=provenance_store,
                model_store=model_store,
            )

        assert (
            artifact_store.fetch_artifacts_by_status(
                group_model_id, ModelArtifactStatus.TRAINING
            )
            == []
        )
        assert model_store.fetch_model(group_model_id) is None

    def test_station_scoped_model_imported_against_group_id_is_rejected(
        self,
    ) -> None:
        from sapphire_flow.types.station import StationGroup
        from tests.fakes.fake_stores import FakeStationGroupStore

        group_store = FakeStationGroupStore()
        group = StationGroup(
            id=StationGroupId(uuid4()),
            name="scope-mismatch-group",
            station_ids=frozenset(),
            created_at=_TRAINED_AT,
        )
        group_store.store_group(group)

        model = _SpyModel(deserialize_ok=True)  # STATION-scoped
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()

        with pytest.raises(ConfigurationError, match="STATION-scoped"):
            import_external_artifact(
                model=model,  # type: ignore[arg-type]
                model_id=_MODEL_ID,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
                trained_at=_TRAINED_AT,
                clock=_clock,
                group_id=group.id,
                group_store=group_store,
                provenance_recorder=provenance_store,
                model_store=model_store,
            )
        assert model_store.fetch_model(_MODEL_ID) is None


class TestFreshExternalModelIsRegistered:
    """G2: `model_artifacts.model_id` is an FK into `models`. The first
    import of a genuinely new model must REGISTER it, not depend on some
    unrelated earlier flow having already done so."""

    def test_fresh_model_gets_registered(self) -> None:
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        assert model_store.fetch_model(_MODEL_ID) is None

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
            model_store=model_store,
        )

        registered = model_store.fetch_model(_MODEL_ID)
        assert registered is not None
        assert registered.artifact_scope == ArtifactScope.STATION

    def test_existing_model_with_conflicting_scope_is_rejected_before_any_write(
        self,
    ) -> None:
        from sapphire_flow.types.model import ModelRecord

        model = _SpyModel(deserialize_ok=True)  # declares STATION
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        model_store.register_model(
            ModelRecord(
                id=_MODEL_ID,
                display_name="Stale registration",
                artifact_scope=ArtifactScope.GROUP,
                description="pre-existing, now-stale registry row",
                created_at=_TRAINED_AT,
            )
        )

        with pytest.raises(ConfigurationError, match="registry drift"):
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
                model_store=model_store,
            )

        assert (
            artifact_store.fetch_artifacts_by_status(
                _MODEL_ID, ModelArtifactStatus.TRAINING
            )
            == []
        )
