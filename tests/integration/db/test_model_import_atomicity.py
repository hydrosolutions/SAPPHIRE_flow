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

from sapphire_flow.services.model_import import import_external_artifact
from sapphire_flow.store.audited_writer import AuditedWriter
from sapphire_flow.store.model_artifact_provenance import PgArtifactProvenanceStore
from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import ModelId, StationId
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_TRAINED_AT = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))


class _FakeModel:
    config_hash = None

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
            )
        conn.close()

        # No AuditedWriter -> no shared txn -> the TRAINING row from
        # store_artifact already committed and cannot be rolled back.
        assert _artifact_row_count(pg_engine, model_id) == 1
