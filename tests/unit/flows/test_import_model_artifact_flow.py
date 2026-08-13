"""Plan 157 T3 fixer round — the deployed parameter boundary.

`import_model_artifact_flow` is a Prefect deployment: its top-level
parameters cross the wire as JSON. A `bytes`-typed parameter does NOT
base64-decode a JSON string (pydantic's JSON-mode `bytes` validation does
`str.encode()`), so arbitrary binary (a `.pt` checkpoint) cannot round-trip
through it. `artifact_base64: str`, decoded inside the flow, is the fix —
these tests reproduce the exact deployment-parameter transport, not just the
decode helper in isolation.
"""

from __future__ import annotations

import base64

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.import_model_artifact import (
    _decode_artifact_base64,
    import_model_artifact_flow,
)

# Deliberately non-UTF-8 — a real checkpoint is arbitrary binary, and a
# base64 round trip that only survives ASCII/UTF-8 text is not proof of
# anything.
_RAW_ARTIFACT_BYTES = bytes([0, 1, 2, 3, 0xFF, 0xFE, 0x80, 0x81])


class TestDeploymentParameterBoundaryRoundTrips:
    def test_validate_parameters_preserves_artifact_base64_through_json(
        self,
    ) -> None:
        """Simulates exactly what a Prefect deployment run does: JSON-shaped
        parameters go through `Flow.validate_parameters` before `.fn` ever
        runs. The base64 STRING must survive that boundary unchanged."""
        encoded = base64.b64encode(_RAW_ARTIFACT_BYTES).decode()

        validated = import_model_artifact_flow.validate_parameters(
            {
                "model_id": "some_model",
                "artifact_base64": encoded,
                "trained_at": "2025-01-01T00:00:00+00:00",
                "training_period_start": "2024-06-01T00:00:00+00:00",
                "training_period_end": "2024-12-01T00:00:00+00:00",
                "expected_config_hash": "some-config-hash",
            }
        )

        assert validated["artifact_base64"] == encoded
        assert (
            base64.b64decode(validated["artifact_base64"], validate=True)
            == _RAW_ARTIFACT_BYTES
        )

    def test_decode_artifact_base64_reproduces_the_original_bytes(self) -> None:
        encoded = base64.b64encode(_RAW_ARTIFACT_BYTES).decode()

        assert _decode_artifact_base64(encoded) == _RAW_ARTIFACT_BYTES

    def test_decode_artifact_base64_rejects_invalid_base64(self) -> None:
        with pytest.raises(ConfigurationError, match="not valid base64"):
            _decode_artifact_base64("not-valid-base64!!! not even close")


class TestFullFlowInvocationEndToEnd:
    """Major finding (Plan 157 T3 fixer round review): this file previously
    tested ONLY the parameter-boundary decode helper, never the flow
    itself — a bug dropping group_id, invoking training from flow wiring,
    or breaking on the GROUP path would stay green. Invokes the REAL flow
    function (`.fn(...)`, bypassing Prefect orchestration — the same
    pattern `test_onboard_model_flow.py` uses) end to end with a
    discoverable synthetic GROUP FI-style model, asserting an ACTIVE group
    artifact, its provenance, and zero `train()` calls."""

    def test_group_scoped_import_activates_artifact_with_provenance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import UTC, datetime
        from uuid import UUID, uuid4

        from sapphire_flow.flows.import_model_artifact import (
            import_model_artifact_flow,
        )
        from sapphire_flow.services.model_import import _InMemoryAuditedWriter
        from sapphire_flow.types.datetime import ensure_utc
        from sapphire_flow.types.enums import ArtifactScope, ModelArtifactStatus
        from sapphire_flow.types.ids import ArtifactId, ModelId, StationGroupId
        from sapphire_flow.types.station import StationGroup
        from tests.fakes.fake_stores import (
            FakeArtifactProvenanceStore,
            FakeAuditLogStore,
            FakeModelArtifactStore,
            FakeModelStore,
            FakeStationGroupStore,
            FakeStationStore,
        )

        raw_model_id = "aquacast_group_pt"

        class _FlowGroupModel:
            """A discoverable, deserializable synthetic GROUP-scoped
            FI-style model — standing in for a real aquacast-shim entry
            point, per the plan's own 'testable against synthetic FI
            models' framing."""

            config_hash = "flow-config-hash"
            artifact_scope = ArtifactScope.GROUP
            display_name = "Flow group model"
            description = "test double"
            data_requirements = None

            def __init__(self) -> None:
                self.train_calls = 0

            def train(self, *args: object, **kwargs: object) -> object:
                self.train_calls += 1
                raise AssertionError(
                    "train() must never be called from the import path"
                )

            def deserialize_artifact(self, raw: bytes) -> object:
                return {"weights": raw}

        model = _FlowGroupModel()
        monkeypatch.setattr(
            "sapphire_flow.services.model_registry.discover_models",
            lambda: {ModelId(raw_model_id): model},
        )

        group_store = FakeStationGroupStore()
        group = StationGroup(
            id=StationGroupId(uuid4()),
            name="flow-import-group",
            station_ids=frozenset(),
            created_at=ensure_utc(datetime(2025, 1, 1, tzinfo=UTC)),
        )
        group_store.store_group(group)

        artifact_store = FakeModelArtifactStore()
        provenance_store = FakeArtifactProvenanceStore()
        model_store = FakeModelStore()
        audit_log_store = FakeAuditLogStore()

        flow_stores: dict[str, object] = {
            "artifact_store": artifact_store,
            "station_store": FakeStationStore(),
            "group_store": group_store,
            "audit_log_store": audit_log_store,
        }
        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        monkeypatch.setattr(
            "sapphire_flow.flows._db.setup_production_stores",
            lambda url: (None, flow_stores),  # noqa: ARG005
        )

        writer = _InMemoryAuditedWriter(
            artifact_store=artifact_store,
            provenance_store=provenance_store,
            audit_log_store=audit_log_store,
            model_store=model_store,
        )
        monkeypatch.setattr(
            "sapphire_flow.store.audited_writer.make_audited_writer",
            lambda conn: writer,  # noqa: ARG005
        )

        artifact_base64 = base64.b64encode(b"real-checkpoint-bytes").decode()

        result = import_model_artifact_flow.fn(
            model_id=raw_model_id,
            artifact_base64=artifact_base64,
            trained_at="2025-01-01T00:00:00+00:00",
            training_period_start="2024-06-01T00:00:00+00:00",
            training_period_end="2024-12-01T00:00:00+00:00",
            expected_config_hash="flow-config-hash",
            group_id=str(group.id),
            source_repository="hydrosolutions/sapphire-aquacast",
            source_commit="deadbeef",
        )

        artifact_id = ArtifactId(UUID(result))
        record = artifact_store.fetch_artifact_record(artifact_id)
        assert record is not None
        assert record.status == ModelArtifactStatus.ACTIVE
        assert record.group_id == group.id
        assert record.station_id is None

        provenance = provenance_store.fetch(artifact_id)
        assert provenance is not None
        assert provenance.source_repository == "hydrosolutions/sapphire-aquacast"
        assert provenance.config_hash == "flow-config-hash"

        registered = model_store.fetch_model(ModelId(raw_model_id))
        assert registered is not None
        assert registered.artifact_scope == ArtifactScope.GROUP

        assert model.train_calls == 0
