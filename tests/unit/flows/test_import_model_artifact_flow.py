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
import hashlib
import os
from typing import TYPE_CHECKING

import pytest

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.flows.import_model_artifact import (
    _decode_artifact_base64,
    _read_staged_artifact,
    _resolve_artifact_bytes,
    import_model_artifact_flow,
)

if TYPE_CHECKING:
    from pathlib import Path

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


class TestStagedArtifactPath:
    """Plan 307 T2 — the staged-path route: exactly one source, a required
    checksum, and containment enforced by the open rather than by a name
    check that precedes it."""

    @staticmethod
    def _staging(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
        monkeypatch.setattr(
            "sapphire_flow.config.paths.resolve_incoming_dir", lambda *a, **k: root
        )

    def test_refuses_when_both_sources_are_given(self) -> None:
        with pytest.raises(ConfigurationError, match="not both"):
            _resolve_artifact_bytes(
                artifact_base64=base64.b64encode(_RAW_ARTIFACT_BYTES).decode(),
                artifact_path="best.pt",
                expected_artifact_sha256="x",
            )

    def test_refuses_when_neither_source_is_given(self) -> None:
        with pytest.raises(ConfigurationError, match="one of artifact_base64"):
            _resolve_artifact_bytes(
                artifact_base64=None, artifact_path=None, expected_artifact_sha256=None
            )

    def test_refuses_a_path_without_a_checksum(self) -> None:
        """Load-bearing: an implementation that skipped verification when the
        checksum was absent would pass every OTHER case in this class. A
        required parameter only tested when supplied is not required."""
        with pytest.raises(
            ConfigurationError, match="expected_artifact_sha256 is required"
        ):
            _resolve_artifact_bytes(
                artifact_base64=None,
                artifact_path="best.pt",
                expected_artifact_sha256=None,
            )

    def test_refuses_an_absolute_path_outside_the_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "incoming"
        root.mkdir()
        outside = tmp_path / "secret.bin"
        outside.write_bytes(b"do not read me")
        self._staging(monkeypatch, root)
        with pytest.raises(ConfigurationError, match="outside the staging root"):
            _read_staged_artifact(str(outside), "0" * 64)

    def test_refuses_a_parent_traversal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "incoming"
        root.mkdir()
        (tmp_path / "secret.bin").write_bytes(b"do not read me")
        self._staging(monkeypatch, root)
        with pytest.raises(
            ConfigurationError, match="directly inside the staging root"
        ):
            _read_staged_artifact("../secret.bin", "0" * 64)

    def test_refuses_a_symlink_whose_target_escapes_the_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "incoming"
        root.mkdir()
        outside = tmp_path / "secret.bin"
        outside.write_bytes(b"do not read me")
        (root / "best.pt").symlink_to(outside)
        self._staging(monkeypatch, root)
        with pytest.raises(ConfigurationError, match="without following a symlink"):
            _read_staged_artifact("best.pt", "0" * 64)

    def test_refuses_any_subdirectory_component(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Independent review 2026-09-21 (major): pinning a descriptor per
        directory level does NOT give containment — a host writer can MOVE an
        intermediate directory out of the root between two opens and the
        pinned descriptor follows it. Subdirectories are refused outright, so
        that window cannot exist. The refusal must fire on the PATH SHAPE,
        before any open, which is why the subdirectory here is real and
        readable rather than a symlink."""
        root = tmp_path / "incoming"
        (root / "sub").mkdir(parents=True)
        (root / "sub" / "best.pt").write_bytes(_RAW_ARTIFACT_BYTES)
        self._staging(monkeypatch, root)
        with pytest.raises(
            ConfigurationError, match="directly inside the staging root"
        ):
            _read_staged_artifact(
                "sub/best.pt", hashlib.sha256(_RAW_ARTIFACT_BYTES).hexdigest()
            )

    def test_refuses_a_symlinked_staging_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 Confirming review 2026-09-21 (major), proven by execution: the
        first fix added O_NOFOLLOW to the root open while the resolver still
        called .resolve(), so the open received the symlink's TARGET and the
        flag could never fire — a root of `incoming -> /elsewhere` served
        /elsewhere/best.pt with a matching checksum.

        This drives the REAL resolver through the environment. An earlier
        version stubbed the resolver via `_staging`, which bypassed the very
        code path the defect lived in, so it passed against the broken
        implementation. The checksum here MATCHES the outside file, so the
        only thing that can make this test pass is the refusal itself."""
        real = tmp_path / "elsewhere"
        real.mkdir()
        (real / "best.pt").write_bytes(_RAW_ARTIFACT_BYTES)
        root = tmp_path / "incoming"
        root.symlink_to(real, target_is_directory=True)
        monkeypatch.setenv("SAPPHIRE_INCOMING_DIR", str(root))

        with pytest.raises(
            ConfigurationError, match="not available as a real directory"
        ):
            _read_staged_artifact(
                "best.pt", hashlib.sha256(_RAW_ARTIFACT_BYTES).hexdigest()
            )

    def test_refuses_a_staged_fifo_without_blocking(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Independent review 2026-09-21 (minor), confirmed by execution:
        opening a FIFO blocks forever. Neither symlink protection nor the
        checksum helps — the block happens in the open, so the guard must be
        O_NONBLOCK plus a regular-file check. If this test hangs, it has
        failed."""
        root = tmp_path / "incoming"
        root.mkdir()
        os.mkfifo(root / "best.pt")
        self._staging(monkeypatch, root)
        with pytest.raises(ConfigurationError, match="not a regular file"):
            _read_staged_artifact("best.pt", "0" * 64)

    def test_an_outside_file_is_never_opened(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guarantee is "never OPENED", not "never imported" — a checksum
        cannot undo a read. This records every os.open and asserts the outside
        file is absent from it."""
        root = tmp_path / "incoming"
        root.mkdir()
        outside = tmp_path / "secret.bin"
        outside.write_bytes(b"do not read me")
        (root / "best.pt").symlink_to(outside)
        self._staging(monkeypatch, root)

        outside_id = (outside.stat().st_dev, outside.stat().st_ino)
        opened_ids: list[tuple[int, int]] = []
        real_open = os.open

        def recording_open(path, *args, **kwargs):  # type: ignore[no-untyped-def]
            fd = real_open(path, *args, **kwargs)
            info = os.fstat(fd)
            opened_ids.append((info.st_dev, info.st_ino))
            return fd

        monkeypatch.setattr(os, "open", recording_open)
        with pytest.raises(ConfigurationError):
            _read_staged_artifact("best.pt", "0" * 64)

        # 🔴 Confirming review 2026-09-21 (major): an earlier version of this
        # test recorded the NAMES passed to os.open. The artifact is opened as
        # "best.pt" relative to a directory descriptor, so the outside path
        # never appears by name — the test passed even with the guard removed,
        # because the checksum then supplied the expected error. Identity is
        # the only evidence that answers "was it opened".
        assert outside_id not in opened_ids, (outside_id, opened_ids)

    def test_refuses_when_the_digest_does_not_match(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "incoming"
        root.mkdir()
        (root / "best.pt").write_bytes(_RAW_ARTIFACT_BYTES)
        self._staging(monkeypatch, root)
        with pytest.raises(ConfigurationError, match="does not match"):
            _read_staged_artifact("best.pt", "0" * 64)

    def test_reads_a_valid_staged_file_byte_for_byte(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "incoming"
        root.mkdir()
        (root / "best.pt").write_bytes(_RAW_ARTIFACT_BYTES)
        digest = hashlib.sha256(_RAW_ARTIFACT_BYTES).hexdigest()
        self._staging(monkeypatch, root)

        assert _read_staged_artifact("best.pt", digest) == _RAW_ARTIFACT_BYTES
        # ...and through the selector, absolute form, with the root prefix.
        assert (
            _resolve_artifact_bytes(
                artifact_base64=None,
                artifact_path=str(root / "best.pt"),
                expected_artifact_sha256=digest.upper(),
            )
            == _RAW_ARTIFACT_BYTES
        )

    def test_accepts_an_absolute_path_through_a_canonical_ancestor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Confirming review 2026-09-21 (minor): removing .resolve() from the
        resolver fixed the security hole and regressed this — an operator
        naming the file through the ancestor's real path, against a root
        configured through an alias, was refused although both name the same
        directory. Real resolver, real alias."""
        real = tmp_path / "real"
        (real / "incoming").mkdir(parents=True)
        (real / "incoming" / "best.pt").write_bytes(_RAW_ARTIFACT_BYTES)
        alias = tmp_path / "alias"
        alias.symlink_to(real, target_is_directory=True)
        monkeypatch.setenv("SAPPHIRE_INCOMING_DIR", str(alias / "incoming"))

        digest = hashlib.sha256(_RAW_ARTIFACT_BYTES).hexdigest()
        assert (
            _read_staged_artifact(str(real / "incoming" / "best.pt"), digest)
            == _RAW_ARTIFACT_BYTES
        )

    def test_accepts_an_absolute_path_when_the_root_is_configured_relative(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same regression, second shape: with a RELATIVE configured root every
        absolute candidate failed the comparison, because one side was
        relative and the other absolute."""
        root = tmp_path / "incoming"
        root.mkdir()
        (root / "best.pt").write_bytes(_RAW_ARTIFACT_BYTES)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SAPPHIRE_INCOMING_DIR", "incoming")

        digest = hashlib.sha256(_RAW_ARTIFACT_BYTES).hexdigest()
        assert (
            _read_staged_artifact(str(root / "best.pt"), digest) == _RAW_ARTIFACT_BYTES
        )

    def test_an_absolute_path_outside_the_root_is_still_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ordinary outside-path rejection, which is all this test shows.

        ⚠️ Final review 2026-09-21: an earlier docstring claimed this proves
        "the permissive comparison must not widen the boundary". It does not.
        It exercises a path rejected BEFORE the guarded open, not an
        adversarial path the comparison accepts. The real guarantee is
        structural and lives in the code, not here: whatever the comparison
        accepts, only `parts[0]` is ever opened, relative to the O_NOFOLLOW
        root descriptor. Claiming a test proves more than it does is how a
        suite starts looking stronger than it is."""
        root = tmp_path / "incoming"
        root.mkdir()
        outside = tmp_path / "secret.bin"
        outside.write_bytes(_RAW_ARTIFACT_BYTES)
        monkeypatch.setenv("SAPPHIRE_INCOMING_DIR", str(root))

        with pytest.raises(ConfigurationError, match="outside the staging root"):
            _read_staged_artifact(
                str(outside), hashlib.sha256(_RAW_ARTIFACT_BYTES).hexdigest()
            )

    def test_base64_route_still_works_and_needs_no_checksum(self) -> None:
        """T2's Out: the digest is deliberately NOT required here — the bytes
        are already in the parameter."""
        encoded = base64.b64encode(_RAW_ARTIFACT_BYTES).decode()
        assert (
            _resolve_artifact_bytes(
                artifact_base64=encoded,
                artifact_path=None,
                expected_artifact_sha256=None,
            )
            == _RAW_ARTIFACT_BYTES
        )


class TestStagedPathParameterSchema:
    """The registered deployment schema must accept a path-only request and
    must still refuse a missing provenance field (Plan 307 T2/T3b)."""

    def test_validate_parameters_accepts_a_path_only_request(self) -> None:
        validated = import_model_artifact_flow.validate_parameters(
            {
                "model_id": "some_model",
                "artifact_path": "best.pt",
                "expected_artifact_sha256": "ab" * 32,
                "trained_at": "2025-01-01T00:00:00+00:00",
                "training_period_start": "2024-06-01T00:00:00+00:00",
                "training_period_end": "2024-12-01T00:00:00+00:00",
                "expected_config_hash": "some-config-hash",
            }
        )
        assert validated["artifact_path"] == "best.pt"
        assert validated.get("artifact_base64") is None

    def test_validate_parameters_still_refuses_a_missing_provenance_field(self) -> None:
        """Keyword-only parameters stay REQUIRED even though the artifact
        inputs above them now carry defaults — that is the whole reason this
        signature shape was chosen."""
        with pytest.raises(
            Exception, match="(?i)expected_config_hash|missing|required"
        ):
            import_model_artifact_flow.validate_parameters(
                {
                    "model_id": "some_model",
                    "artifact_path": "best.pt",
                    "expected_artifact_sha256": "ab" * 32,
                    "trained_at": "2025-01-01T00:00:00+00:00",
                    "training_period_start": "2024-06-01T00:00:00+00:00",
                    "training_period_end": "2024-12-01T00:00:00+00:00",
                }
            )

    def test_refuses_a_checksum_on_the_base64_route(self) -> None:
        """Independent review 2026-09-21: it was silently IGNORED, so a caller
        who believed they had asked for verification was quietly told
        otherwise. The plan forbids REQUIRING it here; it does not require
        accepting it."""
        with pytest.raises(ConfigurationError, match="applies to artifact_path only"):
            _resolve_artifact_bytes(
                artifact_base64=base64.b64encode(_RAW_ARTIFACT_BYTES).decode(),
                artifact_path=None,
                expected_artifact_sha256="ab" * 32,
            )


class TestPathRouteReachesTheImporter:
    """Independent review 2026-09-21 (major): the helper tests stop short of
    the importer, so nothing recorded WHICH bytes it receives, or that it is
    not reached at all when the digest disagrees."""

    @staticmethod
    def _stub_everything_before_the_importer(
        monkeypatch: pytest.MonkeyPatch, calls: list[bytes]
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub/stub")
        monkeypatch.setattr(
            "sapphire_flow.flows._db.setup_production_stores",
            lambda _url: (
                object(),
                {
                    "artifact_store": object(),
                    "station_store": object(),
                    "group_store": object(),
                },
            ),
        )
        monkeypatch.setattr(
            "sapphire_flow.store.audited_writer.make_audited_writer",
            lambda _conn: object(),
        )
        monkeypatch.setattr(
            "sapphire_flow.services.write_principal.resolve_flow_run_principal",
            lambda **_kw: object(),
        )
        monkeypatch.setattr(
            "sapphire_flow.flows.import_model_artifact._resolve_model_task",
            lambda _model_id: object(),
        )

        def _record(_model, _model_id, artifact_bytes, *args, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(artifact_bytes)
            return "00000000-0000-0000-0000-000000000000"

        monkeypatch.setattr(
            "sapphire_flow.flows.import_model_artifact._import_task", _record
        )

    def test_the_file_bytes_reach_the_importer_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "incoming"
        root.mkdir()
        (root / "best.pt").write_bytes(_RAW_ARTIFACT_BYTES)
        monkeypatch.setattr(
            "sapphire_flow.config.paths.resolve_incoming_dir", lambda *a, **k: root
        )
        calls: list[bytes] = []
        self._stub_everything_before_the_importer(monkeypatch, calls)

        import_model_artifact_flow.fn(
            "some_model",
            artifact_path="best.pt",
            expected_artifact_sha256=hashlib.sha256(_RAW_ARTIFACT_BYTES).hexdigest(),
            trained_at="2025-01-01T00:00:00+00:00",
            training_period_start="2024-06-01T00:00:00+00:00",
            training_period_end="2024-12-01T00:00:00+00:00",
            expected_config_hash="some-config-hash",
        )

        assert calls == [_RAW_ARTIFACT_BYTES]

    def test_the_importer_is_never_reached_on_a_digest_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "incoming"
        root.mkdir()
        (root / "best.pt").write_bytes(_RAW_ARTIFACT_BYTES)
        monkeypatch.setattr(
            "sapphire_flow.config.paths.resolve_incoming_dir", lambda *a, **k: root
        )
        calls: list[bytes] = []
        self._stub_everything_before_the_importer(monkeypatch, calls)

        with pytest.raises(ConfigurationError, match="does not match"):
            import_model_artifact_flow.fn(
                "some_model",
                artifact_path="best.pt",
                expected_artifact_sha256="0" * 64,
                trained_at="2025-01-01T00:00:00+00:00",
                training_period_start="2024-06-01T00:00:00+00:00",
                training_period_end="2024-12-01T00:00:00+00:00",
                expected_config_hash="some-config-hash",
            )

        assert calls == []
