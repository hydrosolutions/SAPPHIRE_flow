from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime  # noqa: TC003 — Pydantic resolves this at runtime
from uuid import UUID, uuid4

import psycopg
from pydantic import BaseModel

from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.flows.backup import build_pg_child_env
from sapphire_flow.store.forecast_preservation_store import (
    PgForecastPreservationStore,
)
from sapphire_flow.types.forecast_preservation import PreservationAttestation
from sapphire_flow.types.ids import ForecastId

_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class AttestationInput(BaseModel):
    forecast_id: UUID
    backup_id: UUID
    capture_manifest_sha256: str
    snapshot_sha256: str
    artifact_sha256: str | None
    forecast_values_sha256: str
    runtime_image_digest: str
    backup_manifest_sha256: str
    database_dump_sha256: str
    image_archive_sha256: str
    restored_at: datetime


def _describe() -> int:
    env = build_pg_child_env()
    with (
        psycopg.connect(
            host=env["PGHOST"],
            port=env["PGPORT"],
            user=env["PGUSER"],
            dbname=env["PGDATABASE"],
            password=env["PGPASSWORD"],
        ) as conn,
        conn.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT forecast_id, manifest_json, snapshot_sha256, "
            "artifact_sha256 FROM forecast_evidence "
            "WHERE snapshot_sha256 IS NOT NULL AND artifact_sha256 IS NOT NULL "
            "AND reason = 'runtime_image_bytes_unpinned' "
            "AND manifest_json::jsonb ->> 'runtime_image_digest' "
            "~ '^sha256:[0-9a-f]{64}$' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        rows = cursor.fetchall()
        cursor.execute(
            "SELECT DISTINCT manifest_json::jsonb ->> 'runtime_image_digest' "
            "FROM forecast_evidence WHERE manifest_json::jsonb ->> "
            "'runtime_image_digest' ~ '^sha256:[0-9a-f]{64}$'"
        )
        image_rows = cursor.fetchall()
    sample: dict[str, str] | None = None
    for forecast_id, raw, snapshot_sha, artifact_sha in rows:
        manifest = json.loads(raw)
        image_digest = manifest.get("runtime_image_digest")
        if isinstance(image_digest, str) and _IMAGE_DIGEST.fullmatch(image_digest):
            with psycopg.connect(
                host=env["PGHOST"],
                port=env["PGPORT"],
                user=env["PGUSER"],
                dbname=env["PGDATABASE"],
                password=env["PGPASSWORD"],
            ) as value_conn:
                values_row = value_conn.execute(
                    "SELECT count(*), encode(sha256(convert_to("
                    "COALESCE(jsonb_agg(jsonb_build_array(id, issued_at, "
                    "valid_time, lead_time_hours, member_id, quantile, value) "
                    "ORDER BY id)::text, '[]'), 'UTF8')), 'hex') "
                    "FROM forecast_values WHERE forecast_id = %s",
                    (forecast_id,),
                ).fetchone()
            if values_row is None or values_row[0] == 0:
                continue
            sample = {
                "forecast_id": str(forecast_id),
                "capture_manifest_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "snapshot_sha256": snapshot_sha,
                "artifact_sha256": artifact_sha,
                "forecast_values_sha256": values_row[1],
                "runtime_image_digest": image_digest,
            }
            break
    digests = sorted(digest for (digest,) in image_rows if digest is not None)
    sys.stdout.write(json.dumps({"sample": sample, "image_digests": digests}))
    return 0


def _dump() -> int:
    result = subprocess.run(  # noqa: S603 — fixed pg_dump argv
        ["pg_dump", "--format=custom"],
        env=build_pg_child_env(),
        stdout=sys.stdout.buffer,
        stderr=subprocess.PIPE,
        check=False,
        timeout=1800,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr.decode("utf-8", errors="replace"))
    return result.returncode


def _has_evidence() -> int:
    env = build_pg_child_env()
    with psycopg.connect(
        host=env["PGHOST"],
        port=env["PGPORT"],
        user=env["PGUSER"],
        dbname=env["PGDATABASE"],
        password=env["PGPASSWORD"],
    ) as conn:
        present = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM forecast_evidence)"
        ).fetchone()
    sys.stdout.write("yes\n" if present and present[0] else "no\n")
    return 0


def _assessment_input(forecast_id: UUID) -> int:
    env = build_pg_child_env()
    with psycopg.connect(
        host=env["PGHOST"],
        port=env["PGPORT"],
        user=env["PGUSER"],
        dbname=env["PGDATABASE"],
        password=env["PGPASSWORD"],
    ) as conn:
        evidence = conn.execute(
            "SELECT status, manifest_json, snapshot_sha256, artifact_sha256, "
            "thresholds_json, reason FROM forecast_evidence WHERE forecast_id = %s",
            (forecast_id,),
        ).fetchone()
        attestations = conn.execute(
            "SELECT id, forecast_id, backup_id, capture_manifest_sha256, "
            "snapshot_sha256, forecast_values_sha256, artifact_sha256, "
            "runtime_image_digest, backup_manifest_sha256, database_dump_sha256, "
            "image_archive_sha256, restored_at "
            "FROM forecast_preservation_attestations "
            "WHERE forecast_id = %s ORDER BY restored_at DESC",
            (forecast_id,),
        ).fetchall()
    evidence_keys = (
        "status",
        "manifest_json",
        "snapshot_sha256",
        "artifact_sha256",
        "thresholds_json",
        "reason",
    )
    attestation_keys = (
        "id",
        "forecast_id",
        "backup_id",
        "capture_manifest_sha256",
        "snapshot_sha256",
        "forecast_values_sha256",
        "artifact_sha256",
        "runtime_image_digest",
        "backup_manifest_sha256",
        "database_dump_sha256",
        "image_archive_sha256",
        "restored_at",
    )
    sys.stdout.write(
        json.dumps(
            {
                "evidence": dict(zip(evidence_keys, evidence, strict=True))
                if evidence
                else None,
                "attestations": [
                    dict(zip(attestation_keys, row, strict=True))
                    for row in attestations
                ],
            },
            default=str,
        )
    )
    return 0


def _attest() -> int:
    payload = AttestationInput.model_validate_json(sys.stdin.buffer.read())
    attestation = PreservationAttestation(
        id=uuid4(),
        forecast_id=ForecastId(payload.forecast_id),
        backup_id=payload.backup_id,
        capture_manifest_sha256=payload.capture_manifest_sha256,
        snapshot_sha256=payload.snapshot_sha256,
        forecast_values_sha256=payload.forecast_values_sha256,
        artifact_sha256=payload.artifact_sha256,
        runtime_image_digest=payload.runtime_image_digest,
        backup_manifest_sha256=payload.backup_manifest_sha256,
        database_dump_sha256=payload.database_dump_sha256,
        image_archive_sha256=payload.image_archive_sha256,
        restored_at=payload.restored_at,
    )
    engine = create_engine_from_env()
    try:
        with engine.begin() as conn:
            PgForecastPreservationStore(conn).append(attestation)
    finally:
        engine.dispose()
    sys.stdout.write(str(attestation.id))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("describe", "dump", "attest", "has-evidence", "assessment-input"),
    )
    parser.add_argument("--forecast-id", type=UUID)
    args = parser.parse_args(argv)
    action = args.action
    if action == "describe":
        return _describe()
    if action == "dump":
        return _dump()
    if action == "has-evidence":
        return _has_evidence()
    if action == "assessment-input":
        if args.forecast_id is None:
            parser.error("assessment-input requires --forecast-id")
        return _assessment_input(args.forecast_id)
    return _attest()


if __name__ == "__main__":
    raise SystemExit(main())
