# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import threading
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
import sqlalchemy as sa
import structlog
from alembic.config import Config
from sqlalchemy.exc import DBAPIError
from testcontainers.postgres import PostgresContainer

from alembic import command
from sapphire_flow.db import metadata as db
from sapphire_flow.ops import evidence_backup_worker
from sapphire_flow.ops.protected_evidence_backup import PublishedForecastProof
from sapphire_flow.store.forecast_publication_store import PgForecastPublicationStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.types.enums import ForecastDataUse
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness
from tests.integration.store.test_forecast_data_use import _pair, _stores
from tests.integration.store.test_forecast_evidence_store import _evidence
from tests.integration.store.test_forecast_publication_store import (
    _NOW,
    _add_candidate,
    _publish,
    _seed,
    _transaction,
)
from tests.integration.store.test_rejected_forecast_store import (
    _entry,  # pyright: ignore[reportUnknownVariableType] — existing fixture defaults
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from docker.models.containers import Container

    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.ids import ForecastId, PublicationDecisionId, StationId

IMAGE_DIGEST = "sha256:" + "a" * 64
_TABLES = (
    "forecasts",
    "forecast_input_stations",
    "forecast_values",
    "forecast_evidence",
    "forecast_evidence_blobs",
    "rejected_forecasts",
    "observations",
    "rating_curves",
    "measurement_feed_evidence",
    "rating_reference_proofs",
    "provisional_discharges",
    "provisional_discharge_permissions",
    "forecast_publication_decisions",
    "forecast_publication_selections",
    "forecast_publication_events",
)
_GUARDS = {
    "forecasts": "trg_forecast_test_write_refused",
    "rejected_forecasts": "trg_rejected_forecast_test_write_refused",
}


def manifest(conn: sa.Connection) -> dict[str, list[str]]:
    return {
        table: sorted(
            conn.execute(sa.text(f"SELECT row_to_json(t)::text FROM public.{table} t"))
            .scalars()
            .all()
        )
        for table in _TABLES
    }


def assert_manifest(
    actual: dict[str, list[str]], expected: dict[str, list[str]]
) -> None:
    for table in _TABLES:
        assert actual[table] == expected[table], f"restored content differs: {table}"


def guard_definitions(conn: sa.Connection) -> dict[str, str]:
    return {
        table: conn.execute(
            sa.text(
                "SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE tgname=:name "
                "AND tgrelid=to_regclass(:table)"
            ),
            {"name": name, "table": "public." + table},
        ).scalar_one()
        for table, name in _GUARDS.items()
    }


def assert_refusals(conn: sa.Connection) -> None:
    for table, message in (
        ("forecasts", "test forecast writes are disabled"),
        ("rejected_forecasts", "test rejection writes are disabled"),
    ):
        with pytest.raises(DBAPIError, match=message), conn.begin_nested():
            conn.execute(
                sa.text(
                    f"INSERT INTO {table} SELECT * FROM {table} "
                    "WHERE data_use='expired_rating_test' LIMIT 1"
                )
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class Restored:
    harness: _RoleBootstrapHarness
    expected: dict[str, list[str]]
    standard: OperationalForecast
    test: OperationalForecast
    publication_id: ForecastId
    publication_station_id: StationId
    decision_id: PublicationDecisionId
    marker: tuple[PublishedForecastProof, ...]
    dump_hash: str
    dump_size: int
    guards: dict[str, str]


def start_owned(
    owned: list[PostgresContainer], container: PostgresContainer
) -> PostgresContainer:
    # Register before start: a readiness failure can leave a created Docker handle.
    owned.append(container)
    container.start()
    return container


def assert_container_absent(
    result: subprocess.CompletedProcess[bytes], container_id: str
) -> None:
    expected = f"error: no such object: {container_id}".encode()
    assert result.returncode == 1 and result.stderr.strip().lower() == expected, (
        f"owned container absence not verified: {container_id}: {result.stderr!r}"
    )


def record_resources(
    path: Path, owned_ids: list[str], state: str, errors: list[Exception]
) -> None:
    try:
        path.write_text(json.dumps({"owned_ids": owned_ids, "state": state}))
    except Exception as exc:
        errors.append(exc)


def cleanup_owned(
    owned: list[PostgresContainer],
    engines: list[sa.Engine],
    owned_ids: list[str],
    resource_path: Path,
    original_error: BaseException | None,
    cleanup_errors: list[Exception],
) -> None:
    for container in owned:
        try:
            wrapped = cast("Container | None", container.get_wrapped_container())
            if (
                wrapped is not None
                and isinstance(wrapped.id, str)
                and wrapped.id not in owned_ids
            ):
                owned_ids.append(wrapped.id)
        except Exception as exc:
            cleanup_errors.append(exc)
    record_resources(resource_path, owned_ids, "cleanup-started", cleanup_errors)
    for engine in reversed(engines):
        try:
            engine.dispose()
        except Exception as exc:
            cleanup_errors.append(exc)
    for container in reversed(owned):
        try:
            container.stop()
        except Exception as exc:
            cleanup_errors.append(exc)
    for container_id in owned_ids:
        try:
            check = subprocess.run(
                ["docker", "inspect", container_id],
                capture_output=True,
                check=False,
                timeout=15,
            )
            assert_container_absent(check, container_id)
        except Exception as exc:
            cleanup_errors.append(exc)
    record_resources(
        resource_path,
        owned_ids,
        "cleanup-failed" if cleanup_errors else "cleanup-verified",
        cleanup_errors,
    )
    try:
        structlog.get_logger().info(
            "test.mixed_restore_cleanup",
            container_ids=owned_ids,
            resource_record=str(resource_path),
        )
    except Exception as exc:
        cleanup_errors.append(exc)

    if cleanup_errors:
        if original_error is not None:
            original_error.add_note(
                f"Disposable cleanup also failed: {cleanup_errors!r}"
            )
        else:
            raise ExceptionGroup("owned disposable cleanup failed", cleanup_errors)


@pytest.fixture(scope="module")
def restored(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Restored]:
    directory = tmp_path_factory.mktemp("mixed-restore-owned")
    resource_path = directory / "resources.json"
    owned_ids: list[str] = []
    engines: list[sa.Engine] = []
    owned: list[PostgresContainer] = []
    evidence_errors: list[Exception] = []
    try:
        source = start_owned(
            owned,
            PostgresContainer(
                "postgis/postgis:16-3.4",
                username="test",
                password="test",
                dbname="sapphire",
            ),
        )
        source_id = source.get_wrapped_container().id
        assert isinstance(source_id, str)
        owned_ids.append(source_id)
        record_resources(resource_path, owned_ids, "created", evidence_errors)
        target = start_owned(
            owned,
            PostgresContainer(
                "postgis/postgis:16-3.4",
                username="test",
                password="test",
                dbname="sapphire",
            ),
        )
        target_id = target.get_wrapped_container().id
        assert isinstance(target_id, str)
        owned_ids.append(target_id)
        assert owned_ids[0] != owned_ids[1]
        record_resources(
            resource_path, owned_ids, "created-before-dump-or-recreate", evidence_errors
        )
        structlog.get_logger().info(
            "test.mixed_restore_owned",
            container_ids=owned_ids,
            resource_record=str(resource_path),
        )
        source_url = source.get_connection_url().replace("+psycopg2", "+psycopg")
        target_url = target.get_connection_url().replace("+psycopg2", "+psycopg")
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("DATABASE_URL", source_url)
            command.upgrade(Config("alembic.ini"), "head")
        source_engine = sa.create_engine(source_url)
        engines.append(source_engine)
        with source_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            conn.execute(sa.text("CREATE DATABASE prefect"))
        source_harness = _RoleBootstrapHarness(source, source_engine)
        assert (
            source_harness.run_bootstrap(
                "api-mixed", "worker-mixed", "backup-mixed"
            ).returncode
            == 0
        )
        with source_engine.begin() as conn:
            publication, principal, publication_id, publication_station = _seed(conn)
            decision = _publish(publication, principal, publication_id)
            conn.execute(
                sa.update(db.forecasts)
                .where(db.forecasts.c.id == publication_id)
                .values(status="superseded", version=2)
            )
            late_id = _add_candidate(conn, publication_station)
            ordinary, test = _pair(conn)
            evidence = replace(
                _evidence(),
                manifest_json=json.dumps({"runtime_image_digest": IMAGE_DIGEST}),
            )
            ordinary = replace(ordinary, evidence=evidence)
            test_snapshot = b"synthetic-test-only-input-snapshot"
            test = replace(
                test,
                evidence=replace(
                    evidence,
                    snapshot=test_snapshot,
                    snapshot_sha256=hashlib.sha256(test_snapshot).hexdigest(),
                ),
            )
            guards = guard_definitions(conn)
            for table, name in _GUARDS.items():
                conn.execute(sa.text(f"DROP TRIGGER {name} ON public.{table}"))
            standard_store, test_store = _stores(conn)
            standard_store.store_forecast(ordinary)
            test_store.store_forecast(test)
            entry = _entry(station_id=ordinary.station_id, model_id=ordinary.model_id)
            rejected = PgRejectedForecastStore(
                conn, transaction_factory=lambda: _transaction(conn)
            )
            rejected.write_batch([entry], abandon=threading.Event())
            test_entry = replace(
                entry,
                payload=replace(
                    entry.payload,
                    data_use=ForecastDataUse.EXPIRED_RATING_TEST,
                    input_lineage=test.input_lineage,
                ),
            )
            PgRejectedForecastStore(
                conn,
                transaction_factory=lambda: _transaction(conn),
                data_use=ForecastDataUse.EXPIRED_RATING_TEST,
            ).write_batch([test_entry], abandon=threading.Event())
            for definition in guards.values():
                conn.execute(sa.text(definition))
            assert guard_definitions(conn) == guards
            assert_refusals(conn)
            conn.execute(
                sa.update(db.provisional_discharge_permissions).values(state="disabled")
            )
            assert (
                conn.scalar(sa.select(db.provisional_discharge_permissions.c.state))
                == "disabled"
            )
            associations = conn.execute(sa.select(db.forecast_input_stations)).all()
            assert associations == [(test.id, test.station_id, DEFAULT_TENANT_ID)]
            expected = manifest(conn)
            assert all(expected[table] for table in _TABLES)
        backup_url = source_harness.role_url("sapphire_backup", "backup-mixed")
        backup_engine = sa.create_engine(backup_url)
        engines.append(backup_engine)
        with backup_engine.connect() as conn:
            assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_backup"
            assert_manifest(manifest(conn), expected)
        dump = directory / "mixed.dump"
        marker = io.StringIO()
        real_run = subprocess.run

        def run_dump(
            argv: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[bytes]:
            assert argv[:2] == ["pg_dump", "--format=custom"]
            assert len(argv) == 3 and argv[2].startswith("--snapshot=")
            assert kwargs["env"]["PGUSER"] == "sapphire_backup"
            with source_engine.begin() as conn:
                store = PgForecastPublicationStore(
                    conn, transaction_factory=lambda: _transaction(conn)
                )
                _publish(
                    store,
                    principal,
                    late_id,
                    expected_selection_version=1,
                    idempotency_key="after-exported-snapshot",
                    now=_NOW,
                )
            return real_run(
                [
                    "docker",
                    "exec",
                    "-e",
                    "PGPASSWORD=backup-mixed",
                    owned_ids[0],
                    "pg_dump",
                    "--format=custom",
                    argv[2],
                    "-h",
                    "localhost",
                    "-U",
                    "sapphire_backup",
                    "-d",
                    "sapphire",
                ],
                stdout=kwargs["stdout"],
                stderr=kwargs["stderr"],
                check=False,
                timeout=120,
            )

        with dump.open("wb") as output, pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                evidence_backup_worker,
                "build_pg_child_env",
                lambda: {
                    "PGHOST": source.get_container_host_ip(),
                    "PGPORT": source.get_exposed_port(5432),
                    "PGUSER": "sapphire_backup",
                    "PGDATABASE": "sapphire",
                    "PGPASSWORD": "backup-mixed",
                },
            )
            patch.setattr(evidence_backup_worker.subprocess, "run", run_dump)
            patch.setattr(
                evidence_backup_worker,
                "sys",
                SimpleNamespace(stdout=SimpleNamespace(buffer=output), stderr=marker),
            )
            assert evidence_backup_worker._dump() == 0
        lines = marker.getvalue().splitlines()
        assert len(lines) == 1 and lines[0].startswith(
            "SAPPHIRE_PUBLICATION_SNAPSHOT_V1 "
        )
        captured = json.loads(
            lines[0].removeprefix("SAPPHIRE_PUBLICATION_SNAPSHOT_V1 ")
        )
        assert [item["forecast_id"] for item in captured] == [str(publication_id)]
        assert captured[0]["publication_decision_ids"] == [str(decision.id)]
        with source_engine.connect() as conn:
            assert (
                conn.scalar(
                    sa.select(sa.func.count()).select_from(
                        db.forecast_publication_decisions
                    )
                )
                == 2
            )
        # Recreate only the freshly returned target handle, never an existing DB.
        for argv in (
            ["dropdb", "-U", "test", "sapphire"],
            ["createdb", "-U", "test", "--template=template0", "sapphire"],
        ):
            real_run(
                ["docker", "exec", owned_ids[1], *argv],
                check=True,
                capture_output=True,
                timeout=30,
            )
        with dump.open("rb") as source_bytes:
            real_run(
                [
                    "docker",
                    "exec",
                    "-i",
                    owned_ids[1],
                    "pg_restore",
                    "--single-transaction",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-acl",
                    "-U",
                    "test",
                    "-d",
                    "sapphire",
                ],
                stdin=source_bytes,
                check=True,
                capture_output=True,
                timeout=120,
            )
        target_engine = sa.create_engine(target_url)
        engines.append(target_engine)
        with target_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            conn.execute(sa.text("CREATE DATABASE prefect"))
        target_harness = _RoleBootstrapHarness(target, target_engine)
        result = target_harness.run_bootstrap(
            "api-mixed", "worker-mixed", "backup-mixed"
        )
        assert result.returncode == 0, result.stderr
        with target_engine.connect() as conn:
            assert_manifest(manifest(conn), expected)
            assert guard_definitions(conn) == guards
            assert_refusals(conn)
        yield Restored(
            harness=target_harness,
            expected=expected,
            standard=ordinary,
            test=test,
            publication_id=publication_id,
            publication_station_id=publication_station,
            decision_id=decision.id,
            marker=tuple(
                PublishedForecastProof.model_validate(item) for item in captured
            ),
            dump_hash=hashlib.sha256(dump.read_bytes()).hexdigest(),
            dump_size=dump.stat().st_size,
            guards=guards,
        )
    finally:
        cleanup_owned(
            owned, engines, owned_ids, resource_path, sys.exc_info()[1], evidence_errors
        )
