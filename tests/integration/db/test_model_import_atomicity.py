"""Plan 157 T3 — all-or-nothing external-artifact import (REAL Postgres).

`import_external_artifact` runs `store_artifact` + provenance record +
`promote_artifact` in ONE real transaction via the production
`AuditedWriter` seam (mirrors `test_slice_e_audit_atomicity.py`'s pattern).
A matched pair, same structure as that file:

- ``test_provenance_failure_rolls_back_everything_in_real_transaction`` —
  the FIX: a real txn -> a mid-sequence failure leaves NO artifact row, NO
  provenance row, and the just-written artifact file is deleted (no
  orphan).
- ``test_provenance_failure_persists_orphan_on_autocommit_wiring`` — the
  characterization: the SAME failure on a plain AUTOCOMMIT connection (no
  `AuditedWriter`) leaves the artifact row committed and the file on disk —
  exactly the non-atomic behaviour the real-txn wiring closes.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from testcontainers.postgres import PostgresContainer

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.services.model_import import import_external_artifact
from sapphire_flow.store.audited_writer import AuditedWriter
from sapphire_flow.store.model_artifact_provenance import PgArtifactProvenanceStore
from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
from sapphire_flow.store.model_store import PgModelStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ArtifactScope, ModelArtifactStatus
from sapphire_flow.types.ids import ModelId, StationId
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_TRAINED_AT = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))


class _FakeModel:
    config_hash = None
    artifact_scope = ArtifactScope.STATION
    display_name = "Import Atomicity Fake"
    description = "test double"
    data_requirements = None

    def deserialize_artifact(self, raw: bytes) -> object:
        return {"weights": raw}

    def train(self, *args: object, **kwargs: object) -> object:  # pragma: no cover
        raise AssertionError("train() must never be called from the import path")


def _raise_record(self: object, provenance: object) -> None:  # noqa: ARG001
    raise RuntimeError("provenance boom")


@pytest.fixture(scope="module")
def pg_engine() -> Iterator[sa.Engine]:
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_import_atomicity",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        prior = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)
        try:
            from alembic.config import Config

            from alembic import command

            cfg = Config("alembic.ini")
            cfg.set_main_option("sqlalchemy.url", url)
            command.upgrade(cfg, "head")
            yield engine
        finally:
            engine.dispose()
            if prior is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prior


def _seed_model_and_station(engine: sa.Engine) -> tuple[ModelId, StationId]:
    model_id = ModelId(f"import_atomicity_{uuid4().hex[:8]}")
    station = make_station_config(
        station_id=StationId(uuid4()),
        code=f"IMPORT-ATOM-{uuid4().hex[:8]}",
    )
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO models (id, display_name, artifact_scope, description) "
                "VALUES (:id, :dn, 'station', :d)"
            ),
            {"id": str(model_id), "dn": "Import atomicity", "d": "test model"},
        )
        PgStationStore(conn).store_station(station)
    return model_id, station.id


def _seed_station_only(engine: sa.Engine) -> tuple[ModelId, StationId]:
    """Like `_seed_model_and_station`, but deliberately does NOT insert a
    `models` row — the exact "genuinely new model, empty registry" starting
    condition finding G2 flagged as untested."""
    model_id = ModelId(f"import_fresh_{uuid4().hex[:8]}")
    station = make_station_config(
        station_id=StationId(uuid4()),
        code=f"IMPORT-FRESH-{uuid4().hex[:8]}",
    )
    with engine.begin() as conn:
        PgStationStore(conn).store_station(station)
    return model_id, station.id


def _model_row(engine: sa.Engine, model_id: ModelId) -> sa.engine.row.RowMapping | None:
    with engine.connect() as conn:
        return (
            conn.execute(
                sa.text("SELECT * FROM models WHERE id = :m"), {"m": str(model_id)}
            )
            .mappings()
            .one_or_none()
        )


def _artifact_row_count(engine: sa.Engine, model_id: ModelId) -> int:
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT count(*) FROM model_artifacts WHERE model_id = :m"),
            {"m": str(model_id)},
        ).scalar_one()


def _provenance_row_count(engine: sa.Engine, model_id: ModelId) -> int:
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT count(*) FROM model_artifact_provenance p "
                "JOIN model_artifacts a ON a.id = p.artifact_id "
                "WHERE a.model_id = :m"
            ),
            {"m": str(model_id)},
        ).scalar_one()


class TestImportAtomicity:
    def test_provenance_failure_rolls_back_everything_in_real_transaction(
        self,
        pg_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        model_id, station_id = _seed_model_and_station(pg_engine)
        monkeypatch.setattr(PgArtifactProvenanceStore, "record", _raise_record)
        monkeypatch.setattr(
            "sapphire_flow.config.paths.resolve_artifact_dir", lambda: tmp_path
        )

        writer = AuditedWriter(begin=pg_engine.begin)

        with (
            pg_engine.connect() as station_conn,
            pytest.raises(RuntimeError, match="provenance boom"),
        ):
            import_external_artifact(
                model=_FakeModel(),  # type: ignore[arg-type]
                model_id=model_id,
                artifact_bytes=b"real-checkpoint-bytes",
                trained_at=_TRAINED_AT,
                clock=lambda: _TRAINED_AT,
                station_id=station_id,
                station_store=PgStationStore(station_conn),
                audited_writer=writer,
            )

        assert _artifact_row_count(pg_engine, model_id) == 0
        assert _provenance_row_count(pg_engine, model_id) == 0
        # No file survives outside the model_id subdirectory either.
        model_dir = tmp_path / str(model_id)
        leftover = list(model_dir.glob("*.bin")) if model_dir.exists() else []
        assert leftover == [], f"orphaned artifact file(s): {leftover}"

    def test_store_artifact_fk_violation_leaves_no_orphan_file(
        self,
        pg_engine: sa.Engine,
        tmp_path: Path,
    ) -> None:
        """G8: `store_artifact` writes the file BEFORE the INSERT. If that
        INSERT itself fails (here: model_id has no row in `models` at all —
        an FK violation), the file must not be left behind even though the
        caller never learns the generated artifact id."""
        unregistered_model_id = ModelId(f"no_such_model_{uuid4().hex[:8]}")

        with (
            pg_engine.begin() as conn,
            pytest.raises(sa.exc.IntegrityError),
        ):
            PgModelArtifactStore(conn, tmp_path).store_artifact(
                model_id=unregistered_model_id,
                artifact_bytes=b"orphan-candidate-bytes",
                training_period_start=_TRAINED_AT,
                training_period_end=_TRAINED_AT,
                trained_at=_TRAINED_AT,
                station_id=StationId(uuid4()),
            )

        model_dir = tmp_path / str(unregistered_model_id)
        leftover = list(model_dir.glob("*.bin")) if model_dir.exists() else []
        assert leftover == [], f"orphaned artifact file(s): {leftover}"

    def test_provenance_failure_persists_orphan_on_autocommit_wiring(
        self,
        pg_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Characterization: the SAME failure with NO AuditedWriter (a plain
        # AUTOCOMMIT-style connection, no shared transaction) leaves the
        # artifact row and file behind — exactly what the atomic wiring
        # above closes. Uses the module's provenance_recorder fallback path
        # (audited_writer=None), which requires an explicit recorder.
        from tests.fakes.fake_stores import FakeArtifactProvenanceStore

        model_id, station_id = _seed_model_and_station(pg_engine)

        class _RaisingRecorder(FakeArtifactProvenanceStore):
            def record(self, provenance: object) -> None:  # type: ignore[override]
                raise RuntimeError("provenance boom")

        conn = pg_engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(RuntimeError, match="provenance boom"):
            import_external_artifact(
                model=_FakeModel(),  # type: ignore[arg-type]
                model_id=model_id,
                artifact_bytes=b"real-checkpoint-bytes",
                artifact_store=PgModelArtifactStore(conn, tmp_path),
                trained_at=_TRAINED_AT,
                clock=lambda: _TRAINED_AT,
                station_id=station_id,
                station_store=PgStationStore(conn),
                provenance_recorder=_RaisingRecorder(),
                model_store=PgModelStore(conn),
            )
        conn.close()

        # No AuditedWriter -> no shared txn -> the TRAINING row from
        # store_artifact already committed and cannot be rolled back.
        assert _artifact_row_count(pg_engine, model_id) == 1


class TestFreshExternalModelAutoRegisters:
    """G2: `model_artifacts.model_id` is an FK into `models`. The first
    import of a genuinely new model must succeed by REGISTERING it, not by
    depending on some earlier flow having already done so — the (now fixed)
    bug this locks against previously surfaced as an opaque FK-violation
    IntegrityError with an empty `models` table."""

    def test_import_against_an_empty_models_table_registers_and_promotes(
        self,
        pg_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "sapphire_flow.config.paths.resolve_artifact_dir", lambda: tmp_path
        )
        model_id, station_id = _seed_station_only(pg_engine)
        assert _model_row(pg_engine, model_id) is None  # precondition

        writer = AuditedWriter(begin=pg_engine.begin)
        with pg_engine.connect() as station_conn:
            artifact_id = import_external_artifact(
                model=_FakeModel(),  # type: ignore[arg-type]
                model_id=model_id,
                artifact_bytes=b"real-checkpoint-bytes",
                trained_at=_TRAINED_AT,
                clock=lambda: _TRAINED_AT,
                station_id=station_id,
                station_store=PgStationStore(station_conn),
                audited_writer=writer,
            )

        row = _model_row(pg_engine, model_id)
        assert row is not None
        assert row["artifact_scope"] == "station"

        with pg_engine.connect() as conn:
            status = conn.execute(
                sa.text("SELECT status FROM model_artifacts WHERE id = :id"),
                {"id": str(artifact_id)},
            ).scalar_one()
        assert status == ModelArtifactStatus.ACTIVE.value

    def test_registered_model_with_a_different_scope_is_rejected(
        self,
        pg_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The model is already registered — but as GROUP-scoped — while the
        discovered model (`_FakeModel`) declares STATION. Registry drift
        must fail loudly, before any artifact write, not silently keep the
        stale row."""
        monkeypatch.setattr(
            "sapphire_flow.config.paths.resolve_artifact_dir", lambda: tmp_path
        )
        model_id = ModelId(f"import_drift_{uuid4().hex[:8]}")
        station = make_station_config(
            station_id=StationId(uuid4()), code=f"IMPORT-DRIFT-{uuid4().hex[:8]}"
        )
        with pg_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO models (id, display_name, artifact_scope, "
                    "description) VALUES (:id, :dn, 'group', :d)"
                ),
                {"id": str(model_id), "dn": "Drift test", "d": "test model"},
            )
            PgStationStore(conn).store_station(station)

        writer = AuditedWriter(begin=pg_engine.begin)
        with (
            pg_engine.connect() as station_conn,
            pytest.raises(ConfigurationError, match="registry drift"),
        ):
            import_external_artifact(
                model=_FakeModel(),  # type: ignore[arg-type]
                model_id=model_id,
                artifact_bytes=b"real-checkpoint-bytes",
                trained_at=_TRAINED_AT,
                clock=lambda: _TRAINED_AT,
                station_id=station.id,
                station_store=PgStationStore(station_conn),
                audited_writer=writer,
            )

        assert _artifact_row_count(pg_engine, model_id) == 0
