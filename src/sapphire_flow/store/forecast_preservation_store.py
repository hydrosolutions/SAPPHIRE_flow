from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa

from sapphire_flow.db.metadata import (
    forecast_evidence,
    forecast_preservation_attestations,
)
from sapphire_flow.types.forecast_preservation import PreservationAttestation
from sapphire_flow.types.ids import ForecastId


class PgForecastPreservationStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def append(self, attestation: PreservationAttestation) -> None:
        row = (
            self._conn.execute(
                sa.select(forecast_evidence).where(
                    forecast_evidence.c.forecast_id == attestation.forecast_id
                )
            )
            .mappings()
            .one()
        )
        if (
            hashlib.sha256(row["manifest_json"].encode("utf-8")).hexdigest()
            != attestation.capture_manifest_sha256
            or row["snapshot_sha256"] != attestation.snapshot_sha256
            or row["artifact_sha256"] != attestation.artifact_sha256
            or json.loads(row["manifest_json"]).get("runtime_image_digest")
            != attestation.runtime_image_digest
            or row["reason"] != "runtime_image_bytes_unpinned"
        ):
            raise ValueError("preservation attestation does not match capture")
        restored_chain = (
            self._conn.execute(
                sa.text(
                    "SELECT encode(sha256(s.payload), 'hex') AS snapshot_hash, "
                    "encode(sha256(a.payload), 'hex') AS artifact_hash, "
                    "(SELECT count(*) FROM forecast_values v "
                    "WHERE v.forecast_id = e.forecast_id) AS value_count, "
                    "(SELECT encode(sha256(convert_to("
                    "COALESCE(jsonb_agg(jsonb_build_array(v.id, v.issued_at, "
                    "v.valid_time, v.lead_time_hours, v.member_id, v.quantile, "
                    "v.value) ORDER BY v.id)::text, '[]'), 'UTF8')), 'hex') "
                    "FROM forecast_values v WHERE v.forecast_id = e.forecast_id) "
                    "AS values_hash "
                    "FROM forecast_evidence e "
                    "JOIN forecast_evidence_blobs s ON s.sha256 = e.snapshot_sha256 "
                    "JOIN forecast_evidence_blobs a ON a.sha256 = e.artifact_sha256 "
                    "WHERE e.forecast_id = :forecast_id"
                ),
                {"forecast_id": attestation.forecast_id},
            )
            .mappings()
            .one_or_none()
        )
        if (
            restored_chain is None
            or restored_chain["snapshot_hash"] != attestation.snapshot_sha256
            or restored_chain["artifact_hash"] != attestation.artifact_sha256
            or restored_chain["value_count"] < 1
            or restored_chain["values_hash"] != attestation.forecast_values_sha256
        ):
            raise ValueError("live forecast chain does not match restored proof")
        existing = (
            self._conn.execute(
                sa.select(forecast_preservation_attestations).where(
                    forecast_preservation_attestations.c.forecast_id
                    == attestation.forecast_id,
                    forecast_preservation_attestations.c.backup_id
                    == attestation.backup_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        fields = (
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
        if existing is not None:
            if any(existing[field] != getattr(attestation, field) for field in fields):
                raise ValueError(
                    "preservation attestation conflicts with existing proof"
                )
            return
        self._conn.execute(
            sa.insert(forecast_preservation_attestations).values(
                id=attestation.id,
                forecast_id=attestation.forecast_id,
                backup_id=attestation.backup_id,
                capture_manifest_sha256=attestation.capture_manifest_sha256,
                snapshot_sha256=attestation.snapshot_sha256,
                forecast_values_sha256=attestation.forecast_values_sha256,
                artifact_sha256=attestation.artifact_sha256,
                runtime_image_digest=attestation.runtime_image_digest,
                backup_manifest_sha256=attestation.backup_manifest_sha256,
                database_dump_sha256=attestation.database_dump_sha256,
                image_archive_sha256=attestation.image_archive_sha256,
                restored_at=attestation.restored_at,
            )
        )

    def latest(self, forecast_id: ForecastId) -> PreservationAttestation | None:
        row = (
            self._conn.execute(
                sa.select(forecast_preservation_attestations)
                .where(forecast_preservation_attestations.c.forecast_id == forecast_id)
                .order_by(forecast_preservation_attestations.c.restored_at.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return PreservationAttestation(
            id=row["id"],
            forecast_id=ForecastId(row["forecast_id"]),
            backup_id=row["backup_id"],
            capture_manifest_sha256=row["capture_manifest_sha256"],
            snapshot_sha256=row["snapshot_sha256"],
            forecast_values_sha256=row["forecast_values_sha256"],
            artifact_sha256=row["artifact_sha256"],
            runtime_image_digest=row["runtime_image_digest"],
            backup_manifest_sha256=row["backup_manifest_sha256"],
            database_dump_sha256=row["database_dump_sha256"],
            image_archive_sha256=row["image_archive_sha256"],
            restored_at=row["restored_at"],
        )
