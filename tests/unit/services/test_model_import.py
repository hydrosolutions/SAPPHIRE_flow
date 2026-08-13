"""Plan 157 T3 (G4/G7) — external-artifact import.

Red-first acceptance criteria (plan text):
- an undeserializable blob fails loudly, before any write.
- a valid import yields a promotable artifact with external provenance.
- an acceptance test from the public boundary proving `train()` is never
  called.
Plus the plan's other stated invariants: config/artifact mismatch fails
loudly before any write (with NO warning-only bypass — Plan 157 T3 fixer
round); the tenant is DERIVED from the target, never trusted from a
caller-supplied argument; `trained_at`, `training_period_start`/
`training_period_end` and `imported_at` are three distinct values, never
fabricated from one another.
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

_TRAINING_PERIOD_START = ensure_utc(datetime(2024, 6, 1, tzinfo=UTC))
_TRAINING_PERIOD_END = ensure_utc(datetime(2024, 12, 1, tzinfo=UTC))
_TRAINED_AT = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_IMPORTED_AT = ensure_utc(datetime(2026, 8, 13, tzinfo=UTC))
_MODEL_ID = ModelId("aquacast_cmal_pool_pt")
_CONFIG_HASH = "shim-config-abc123"


class _SpyModel:
    """Structurally satisfies the deserialize_artifact boundary
    `import_external_artifact` calls; `train` is spied on so tests can
    assert the public-boundary invariant that it is NEVER called."""

    config_hash = _CONFIG_HASH
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


def _import(**overrides: object) -> object:
    """All calls default to the valid-config-hash, valid-period shape;
    individual tests override only what they're testing."""
    kwargs: dict[str, object] = {
        "trained_at": _TRAINED_AT,
        "training_period_start": _TRAINING_PERIOD_START,
        "training_period_end": _TRAINING_PERIOD_END,
        "expected_config_hash": _CONFIG_HASH,
        "clock": _clock,
    }
    kwargs.update(overrides)
    return import_external_artifact(**kwargs)  # type: ignore[arg-type]


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
            _import(
                model=model,
                model_id=_MODEL_ID,
                artifact_bytes=b"not-a-real-checkpoint",
                artifact_store=artifact_store,
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
            _import(
                model=model,
                model_id=_MODEL_ID,
                artifact_bytes=b"not-a-real-checkpoint",
                artifact_store=artifact_store,
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
            _import(
                model=model,
                model_id=_MODEL_ID,
                artifact_bytes=b"not-a-real-checkpoint",
                artifact_store=artifact_store,
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
            _import(
                model=model,
                model_id=_MODEL_ID,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
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

    def test_missing_declared_config_hash_is_rejected_before_any_write(self) -> None:
        """The model itself must declare a config_hash — a model that
        declares none (e.g. an FI model whose config_hash was never
        forwarded) is refused, not silently imported without a drift
        check."""

        class _NoConfigHashModel(_SpyModel):
            config_hash = None

        model = _NoConfigHashModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        with pytest.raises(ConfigurationError, match="does not declare a config_hash"):
            _import(
                model=model,
                model_id=_MODEL_ID,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
                station_id=station.id,
                station_store=station_store,
                provenance_recorder=provenance_store,
                model_store=model_store,
            )

        assert model.deserialize_calls == []
        assert (
            artifact_store.fetch_artifacts_by_status(
                _MODEL_ID, ModelArtifactStatus.TRAINING
            )
            == []
        )

    def test_omitted_expected_config_hash_is_rejected_at_the_call_boundary(
        self,
    ) -> None:
        """Plan 157 T3 fixer round: the previous code let a caller OMIT
        expected_config_hash entirely — in which case NO drift check ran at
        all (only a warning was logged), so a mismatched artifact could be
        silently promoted. expected_config_hash is now a REQUIRED
        parameter, so a caller who forgets it fails immediately at the
        Python call boundary, not deep inside a warning-only bypass."""
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        with pytest.raises(TypeError, match="expected_config_hash"):
            import_external_artifact(  # type: ignore[call-arg]
                model=model,  # type: ignore[arg-type]
                model_id=_MODEL_ID,
                artifact_bytes=b"real-checkpoint-bytes",
                trained_at=_TRAINED_AT,
                training_period_start=_TRAINING_PERIOD_START,
                training_period_end=_TRAINING_PERIOD_END,
                clock=_clock,
                artifact_store=artifact_store,
                station_id=station.id,
                station_store=station_store,
                provenance_recorder=provenance_store,
                model_store=model_store,
            )

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
        model_store = FakeModelStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        artifact_id = _import(
            model=model,
            model_id=_MODEL_ID,
            artifact_bytes=b"real-checkpoint-bytes",
            artifact_store=artifact_store,
            station_id=station.id,
            station_store=station_store,
            provenance_recorder=provenance_store,
            model_store=model_store,
            source_repository="hydrosolutions/sapphire-aquacast",
            source_commit="deadbeef",
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

        artifact_id = _import(
            model=model,
            model_id=_MODEL_ID,
            artifact_bytes=b"real-checkpoint-bytes",
            artifact_store=artifact_store,
            station_id=station.id,
            station_store=station_store,
            provenance_recorder=provenance_store,
            model_store=model_store,
            source_repository="hydrosolutions/sapphire-aquacast",
            source_commit="deadbeef",
            imported_by="operator@hydrosolutions.ch",
        )

        provenance = provenance_store.fetch(artifact_id)
        assert provenance is not None
        assert provenance.source_repository == "hydrosolutions/sapphire-aquacast"
        assert provenance.source_commit == "deadbeef"
        assert provenance.config_hash == _CONFIG_HASH
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

        _import(
            model=model,
            model_id=_MODEL_ID,
            artifact_bytes=b"real-checkpoint-bytes",
            artifact_store=artifact_store,
            station_id=station.id,
            station_store=station_store,
            provenance_recorder=provenance_store,
            model_store=model_store,
        )

        assert model.train_calls == 0

    def test_trained_at_training_period_and_imported_at_stay_distinct(self) -> None:
        """None of trained_at / training_period_start / training_period_end
        / imported_at (clock()) is ever synthesized from another — all four
        are caller-supplied and independently preserved."""
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        artifact_id = _import(
            model=model,
            model_id=_MODEL_ID,
            artifact_bytes=b"real-checkpoint-bytes",
            artifact_store=artifact_store,
            station_id=station.id,
            station_store=station_store,
            provenance_recorder=provenance_store,
            model_store=model_store,
        )

        record = artifact_store.fetch_artifact_record(artifact_id)
        provenance = provenance_store.fetch(artifact_id)
        assert record is not None
        assert provenance is not None
        assert record.training_period_start == _TRAINING_PERIOD_START
        assert record.training_period_end == _TRAINING_PERIOD_END
        assert record.trained_at == _TRAINED_AT
        assert provenance.imported_at == _IMPORTED_AT
        values = {
            record.training_period_start,
            record.training_period_end,
            record.trained_at,
            provenance.imported_at,
        }
        assert len(values) == 4, "all four instants must be distinct, never fabricated"


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
            _import(
                model=model,
                model_id=_MODEL_ID,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
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

        artifact_id = _import(
            model=model,
            model_id=_MODEL_ID,
            artifact_bytes=b"real-checkpoint-bytes",
            artifact_store=artifact_store,
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
            _import(
                model=model,
                model_id=_MODEL_ID,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
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
            _import(
                model=model,
                model_id=group_model_id,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
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
            _import(
                model=model,
                model_id=_MODEL_ID,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
                group_id=group.id,
                group_store=group_store,
                provenance_recorder=provenance_store,
                model_store=model_store,
            )
        assert model_store.fetch_model(_MODEL_ID) is None


class TestGroupScopedImportSucceedsThroughTheFullPath:
    """Major finding (Plan 157 T3 fixer round review): every SUCCESS-path
    test above uses a STATION-scoped model — the GROUP model appeared only
    in rejection tests. A bug dropping group_id, or one that only worked by
    accident for STATION scope, would stay green without this."""

    def test_group_artifact_is_active_with_group_id_and_provenance_preserved(
        self,
    ) -> None:
        from sapphire_flow.types.station import StationGroup
        from tests.fakes.fake_stores import FakeStationGroupStore

        group_store = FakeStationGroupStore()
        group = StationGroup(
            id=StationGroupId(uuid4()),
            name="aquacast-pool-group",
            station_ids=frozenset(),
            created_at=_TRAINED_AT,
        )
        group_store.store_group(group)

        group_model_id = ModelId("aquacast_group_pt")
        model = _GroupSpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()

        artifact_id = _import(
            model=model,
            model_id=group_model_id,
            artifact_bytes=b"real-checkpoint-bytes",
            artifact_store=artifact_store,
            group_id=group.id,
            group_store=group_store,
            provenance_recorder=provenance_store,
            model_store=model_store,
            source_repository="hydrosolutions/sapphire-aquacast",
            source_commit="deadbeef",
            imported_by="operator@hydrosolutions.ch",
        )

        record = artifact_store.fetch_artifact_record(artifact_id)
        assert record is not None
        assert record.status == ModelArtifactStatus.ACTIVE
        assert record.group_id == group.id
        assert record.station_id is None

        provenance = provenance_store.fetch(artifact_id)
        assert provenance is not None
        assert provenance.source_repository == "hydrosolutions/sapphire-aquacast"

        registered = model_store.fetch_model(group_model_id)
        assert registered is not None
        assert registered.artifact_scope == ArtifactScope.GROUP
        assert model.train_calls == 0


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

        _import(
            model=model,
            model_id=_MODEL_ID,
            artifact_bytes=b"real-checkpoint-bytes",
            artifact_store=artifact_store,
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
            _import(
                model=model,
                model_id=_MODEL_ID,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
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

    def test_concurrent_registration_drift_is_caught_after_registering(self) -> None:
        """Plan 157 T3 fixer round (race-condition minor): registration is
        `ON CONFLICT DO NOTHING` — a concurrent worker (or a mixed-version
        deployment) could insert a row with a DIFFERENT scope between our
        `fetch_model` check and our own `register_model` call. Simulates
        that by having `register_model` itself install a conflicting row
        (standing in for the concurrent writer that won the race) and
        asserts the import still refuses to proceed on stale in-hand
        assumptions."""

        class _RaceModelStore(FakeModelStore):
            def register_model(self, record: object) -> None:
                # A concurrent worker's row "wins" — GROUP-scoped — instead
                # of whatever this caller intended to register.
                from sapphire_flow.types.model import ModelRecord as _ModelRecord

                super().register_model(
                    _ModelRecord(
                        id=_MODEL_ID,
                        display_name="Concurrent winner",
                        artifact_scope=ArtifactScope.GROUP,
                        description="registered by a simulated concurrent worker",
                        created_at=_TRAINED_AT,
                    )
                )

        model = _SpyModel(deserialize_ok=True)  # declares STATION
        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = _RaceModelStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        assert model_store.fetch_model(_MODEL_ID) is None  # no row yet — no race lost

        with pytest.raises(ConfigurationError, match="registered concurrently"):
            _import(
                model=model,
                model_id=_MODEL_ID,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
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


class TestUnitOfWorkAppliesToTheFakeBackedPath:
    """Plan 157 T3 fixer round: 'require a unit-of-work for every import,
    including fakes.' Without `_InMemoryAuditedWriter`, a mid-sequence
    failure on the fake-backed (audited_writer=None) path left whatever the
    fakes had already mutated — a TRAINING-status artifact stuck forever,
    with no provenance and no promotion. This proves that no longer
    happens: the artifact store rolls back to its PRE-import state."""

    def test_provenance_failure_leaves_no_artifact_row_behind(self) -> None:
        model = _SpyModel(deserialize_ok=True)
        artifact_store = FakeModelArtifactStore()
        model_store = FakeModelStore()
        station_store = FakeStationStore()
        station = make_station_config()
        station_store.store_station(station)

        class _RaisingRecorder(FakeArtifactProvenanceStore):
            def record(self, provenance: object) -> None:  # type: ignore[override]
                raise RuntimeError("provenance boom")

        with pytest.raises(RuntimeError, match="provenance boom"):
            _import(
                model=model,
                model_id=_MODEL_ID,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=artifact_store,
                station_id=station.id,
                station_store=station_store,
                provenance_recorder=_RaisingRecorder(),
                model_store=model_store,
            )

        # Pre-fix, this artifact would have been left behind at TRAINING
        # status forever — the fake-backed path had no rollback at all.
        assert artifact_store._records == {}  # noqa: SLF001
        assert artifact_store._bytes == {}  # noqa: SLF001
