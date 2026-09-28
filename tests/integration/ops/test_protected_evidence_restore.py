from __future__ import annotations

import hashlib
import io
import json
import os
import random
import subprocess
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from alembic import command
from sapphire_flow.db.metadata import human_station_grants
from sapphire_flow.ops import evidence_backup_worker
from sapphire_flow.store.forecast_publication_store import PgForecastPublicationStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.forecast_values_integrity import forecast_values_integrity
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.forecast_evidence import EvidenceStatus
from sapphire_flow.types.ids import ArtifactId, ModelId
from tests.conftest import make_station_config
from tests.integration.store.test_forecast_evidence_store import _evidence
from tests.integration.store.test_forecast_publication_store import (
    _NOW,
    _add_candidate,
    _publish,
    _seed,
    _transaction,
)
from tests.integration.store.test_forecast_store import (
    _ISSUED_A,
    _make_forecast,
    _seed_artifact,
    _seed_model,
    _seed_station,
    savepoint_factory,
)

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch


def test_clean_volume_restores_forecast_evidence_chain(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    image_digest = "sha256:" + "a" * 64
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_preservation_restore_test",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
        engine = sa.create_engine(url)
        try:
            with engine.begin() as conn:
                sid = _seed_station(conn)
                mid = _seed_model(conn)
                aid = _seed_artifact(conn, sid, mid)
                source = replace(
                    _evidence(),
                    status=EvidenceStatus.INCOMPLETE,
                    manifest_json=('{"runtime_image_digest":"' + image_digest + '"}'),
                    reason="runtime_image_bytes_unpinned",
                )
                forecast = replace(_make_forecast(sid, mid, aid), evidence=source)
                store = PgForecastStore(
                    conn, transaction_factory=savepoint_factory(conn)
                )
                store.store_forecast(forecast)
                saved = store.fetch_evidence(forecast.id)
                assert saved is not None
                values_digest = conn.execute(
                    sa.text(
                        "SELECT encode(sha256(convert_to("
                        "COALESCE(jsonb_agg(jsonb_build_array(id, issued_at, "
                        "valid_time, lead_time_hours, member_id, quantile, value) "
                        "ORDER BY id)::text, '[]'), 'UTF8')), 'hex') "
                        "FROM forecast_values WHERE forecast_id = :forecast_id"
                    ),
                    {"forecast_id": forecast.id},
                ).scalar_one()
        finally:
            engine.dispose()

        dump = tmp_path / "evidence.dump"
        with dump.open("wb") as output:
            subprocess.run(  # noqa: S603 — disposable test container
                [
                    "docker",
                    "exec",
                    postgres.get_wrapped_container().id,
                    "pg_dump",
                    "--format=custom",
                    "-U",
                    "test",
                    "-d",
                    "sapphire_preservation_restore_test",
                ],
                stdout=output,
                check=True,
                timeout=120,
            )
        environment = {
            **os.environ,
            "SAPPHIRE_EVIDENCE_FORECAST_ID": str(forecast.id),
            "SAPPHIRE_EVIDENCE_CAPTURE_MANIFEST_SHA256": hashlib.sha256(
                saved.manifest_json.encode()
            ).hexdigest(),
            "SAPPHIRE_EVIDENCE_SNAPSHOT_SHA256": saved.snapshot_sha256 or "",
            "SAPPHIRE_EVIDENCE_ARTIFACT_SHA256": saved.artifact_sha256 or "",
            "SAPPHIRE_EVIDENCE_FORECAST_VALUES_SHA256": values_digest,
            "SAPPHIRE_EVIDENCE_RUNTIME_IMAGE_DIGEST": image_digest,
        }
        subprocess.run(  # noqa: S603 — checked-in restore rehearsal
            ["bash", "scripts/restore-rehearsal.sh", str(dump)],
            env=environment,
            check=True,
            timeout=300,
        )
        with pytest.raises(subprocess.CalledProcessError):
            subprocess.run(  # noqa: S603 — corrupted expected output hash
                ["bash", "scripts/restore-rehearsal.sh", str(dump)],
                env={
                    **environment,
                    "SAPPHIRE_EVIDENCE_FORECAST_VALUES_SHA256": "0" * 64,
                },
                check=True,
                timeout=300,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def test_restore_verifies_every_pending_publication_at_dump_cutoff(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    image_digest = "sha256:" + "a" * 64
    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_publication_restore_test",
    ) as postgres:
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")
        engine = sa.create_engine(url)
        try:
            with engine.begin() as conn:
                store, principal, first_id, station_id = _seed(conn)
                second_id = _add_candidate(conn, station_id)
                first_decision = _publish(store, principal, first_id)
                second_decision = _publish(
                    store,
                    principal,
                    second_id,
                    expected_selection_version=1,
                    idempotency_key="replacement",
                )
                other_station = make_station_config(
                    code="TEST-002", rng=random.Random(78)
                )
                PgStationStore(conn).store_station(other_station)
                other_model = _seed_model(conn, model_id="linreg_station_two")
                other_artifact = _seed_artifact(conn, other_station.id, other_model)
                other_forecast = replace(
                    _make_forecast(other_station.id, other_model, other_artifact),
                    evidence=replace(
                        _evidence(),
                        manifest_json=json.dumps(
                            {"runtime_image_digest": image_digest}
                        ),
                    ),
                )
                PgForecastStore(
                    conn, transaction_factory=savepoint_factory(conn)
                ).store_forecast(other_forecast)
                for permission in ("review", "publish"):
                    conn.execute(
                        sa.insert(human_station_grants).values(
                            user_id=principal.user_id,
                            tenant_id=principal.tenant_id,
                            station_id=other_station.id,
                            permission=permission,
                        )
                    )
                other_decision = _publish(
                    store,
                    principal,
                    other_forecast.id,
                    idempotency_key="other-station",
                )
                artifact_id = conn.scalar(
                    sa.text(
                        "SELECT id FROM model_artifacts WHERE station_id = :station_id "
                        "AND model_id = 'linreg_v1'"
                    ),
                    {"station_id": station_id},
                )
                later_forecast = replace(
                    _make_forecast(
                        station_id,
                        ModelId("linreg_v1"),
                        ArtifactId(artifact_id),
                        issued_at=_ISSUED_A + timedelta(days=1),
                    ),
                    evidence=replace(
                        _evidence(),
                        manifest_json=json.dumps(
                            {"runtime_image_digest": image_digest}
                        ),
                    ),
                )
                PgForecastStore(
                    conn, transaction_factory=savepoint_factory(conn)
                ).store_forecast(later_forecast)
                expectations: dict[str, str] = {}
                for forecast_id, decision_id in (
                    (first_id, first_decision.id),
                    (second_id, second_decision.id),
                    (other_forecast.id, other_decision.id),
                ):
                    row = conn.execute(
                        sa.text(
                            "SELECT manifest_json, snapshot_sha256, artifact_sha256 "
                            "FROM forecast_evidence WHERE forecast_id = :forecast_id"
                        ),
                        {"forecast_id": forecast_id},
                    ).one()
                    _, values_hash = forecast_values_integrity(conn, forecast_id)
                    expectations[str(forecast_id)] = "|".join(
                        (
                            str(forecast_id),
                            hashlib.sha256(row.manifest_json.encode()).hexdigest(),
                            row.snapshot_sha256,
                            row.artifact_sha256 or "-",
                            values_hash,
                            image_digest,
                            str(decision_id),
                        )
                    )
            dump = tmp_path / "publication.dump"
            marker = io.StringIO()
            real_run = subprocess.run

            def run_dump(
                argv: list[str], **kwargs: object
            ) -> subprocess.CompletedProcess[bytes]:
                assert argv[:2] == ["pg_dump", "--format=custom"]
                assert argv[2].startswith("--snapshot=")
                with engine.begin() as conn:
                    later_store = PgForecastPublicationStore(
                        conn, transaction_factory=lambda: _transaction(conn)
                    )
                    _publish(
                        later_store,
                        principal,
                        later_forecast.id,
                        idempotency_key="after-snapshot-before-dump",
                        now=_NOW,
                    )
                return real_run(  # noqa: S603 — disposable test container
                    [
                        "docker",
                        "exec",
                        postgres.get_wrapped_container().id,
                        "pg_dump",
                        "--format=custom",
                        "-U",
                        "test",
                        "-d",
                        "sapphire_publication_restore_test",
                        argv[2],
                    ],
                    stdout=kwargs["stdout"],
                    stderr=kwargs["stderr"],
                    check=False,
                    timeout=120,
                )

            with (
                dump.open("wb") as output,
                monkeypatch.context() as patch_worker,
            ):
                patch_worker.setattr(
                    evidence_backup_worker,
                    "build_pg_child_env",
                    lambda: {
                        "PGHOST": postgres.get_container_host_ip(),
                        "PGPORT": postgres.get_exposed_port(5432),
                        "PGUSER": "test",
                        "PGDATABASE": "sapphire_publication_restore_test",
                        "PGPASSWORD": "test",
                    },
                )
                patch_worker.setattr(evidence_backup_worker.subprocess, "run", run_dump)
                patch_worker.setattr(
                    evidence_backup_worker,
                    "sys",
                    SimpleNamespace(
                        stdout=SimpleNamespace(buffer=output), stderr=marker
                    ),
                )
                assert evidence_backup_worker._dump() == 0
            marker_lines = marker.getvalue().splitlines()
            assert len(marker_lines) == 1
            marker_prefix = "SAPPHIRE_PUBLICATION_SNAPSHOT_V1 "
            assert marker_lines[0].startswith(marker_prefix)
            captured = json.loads(marker_lines[0].removeprefix(marker_prefix))
            assert {item["forecast_id"] for item in captured} == {
                str(first_id),
                str(second_id),
                str(other_forecast.id),
            }
            for item in captured:
                expected = expectations[item["forecast_id"]].split("|")
                assert item["publication_decision_ids"] == [expected[6]]
                assert item["forecast_values_sha256"] == expected[4]
        finally:
            engine.dispose()

        expected_file = tmp_path / "expected-publications.txt"
        expected_file.write_text(
            "\n".join(expectations[key] for key in sorted(expectations)) + "\n"
        )
        environment = {
            **os.environ,
            "SAPPHIRE_EVIDENCE_FORECAST_ID": str(first_id),
            "SAPPHIRE_EVIDENCE_CAPTURE_MANIFEST_SHA256": expectations[
                str(first_id)
            ].split("|")[1],
            "SAPPHIRE_EVIDENCE_SNAPSHOT_SHA256": expectations[str(first_id)].split("|")[
                2
            ],
            "SAPPHIRE_EVIDENCE_ARTIFACT_SHA256": expectations[str(first_id)].split("|")[
                3
            ],
            "SAPPHIRE_EVIDENCE_FORECAST_VALUES_SHA256": expectations[
                str(first_id)
            ].split("|")[4],
            "SAPPHIRE_EVIDENCE_RUNTIME_IMAGE_DIGEST": image_digest,
            "SAPPHIRE_PUBLICATION_EXPECTATIONS_FILE": str(expected_file),
        }
        subprocess.run(  # noqa: S603 — checked-in restore rehearsal
            ["bash", "scripts/restore-rehearsal.sh", str(dump)],
            env=environment,
            check=True,
            timeout=300,
        )
        corrupt = expectations[str(second_id)].split("|")
        corrupt[4] = "0" * 64
        expected_file.write_text(
            "\n".join(
                "|".join(corrupt) if key == str(second_id) else expectations[key]
                for key in sorted(expectations)
            )
            + "\n"
        )
        with pytest.raises(subprocess.CalledProcessError):
            subprocess.run(  # noqa: S603 — corrupted second published forecast
                ["bash", "scripts/restore-rehearsal.sh", str(dump)],
                env=environment,
                check=True,
                timeout=300,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
