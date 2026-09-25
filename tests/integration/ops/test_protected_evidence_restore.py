from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from alembic import command
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.forecast_evidence import EvidenceStatus
from tests.integration.store.test_forecast_evidence_store import _evidence
from tests.integration.store.test_forecast_store import (
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
