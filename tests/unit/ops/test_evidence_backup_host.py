from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.ops import evidence_backup_host as host
from sapphire_flow.ops.protected_evidence_backup import (
    BackupHealth,
    ImageArchive,
    ProtectedBackupManifest,
)
from sapphire_flow.types.forecast_preservation import BackupProof, BackupProofStatus
from sapphire_flow.types.ids import ForecastId

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


def test_failed_attestation_never_publishes_healthy_manifest(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    image = "sha256:" + "a" * 64
    sample = host._Sample(
        forecast_id=uuid4(),
        capture_manifest_sha256="b" * 64,
        snapshot_sha256="c" * 64,
        artifact_sha256="d" * 64,
        forecast_values_sha256="e" * 64,
        runtime_image_digest=image,
    )
    monkeypatch.setattr(host, "_check_headroom", lambda *_: None)
    monkeypatch.setattr(host, "verify_separate_target", lambda *_: None)
    monkeypatch.setattr(host, "_verify_running_worker_image", lambda *_: None)
    monkeypatch.setattr(
        host,
        "_describe",
        lambda *_: host._Description(sample=sample, image_digests=[image]),
    )
    monkeypatch.setattr(host, "_dump", lambda _, path: path.write_bytes(b"dump"))
    monkeypatch.setattr(
        host,
        "_archive_image",
        lambda *_: ImageArchive(
            image_digest=image, archive_sha256="f" * 64, byte_length=1
        ),
    )
    monkeypatch.setattr(host, "_verify_restored_chain", lambda **_: None)

    def reject_attestation(*_: object) -> None:
        raise RuntimeError("attestation refused")

    monkeypatch.setattr(host, "_attest", reject_attestation)
    now = datetime(2026, 9, 25, tzinfo=UTC)
    with pytest.raises(RuntimeError, match="attestation refused"):
        host.create_protected_backup(
            target=tmp_path,
            database_volume=tmp_path,
            compose_file=Path("docker-compose.yml"),
            rehearsal_script=Path("scripts/restore-rehearsal.sh"),
            now=now,
            backup_id=uuid4(),
            clock=lambda: now,
        )
    assert list(tmp_path.glob("backup-*.json")) == []


def test_assess_command_reports_missing_capture(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(
        host.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"evidence": None, "attestations": []}).encode(),
        ),
    )
    result = host.assess_forecast_preservation(
        forecast_id=ForecastId(uuid4()),
        target=tmp_path,
        database_volume=tmp_path,
        compose_file=Path("docker-compose.yml"),
        now=datetime(2026, 9, 25, tzinfo=UTC),
    )
    assert result["effective_preservation_status"] == "evidence_incomplete"
    assert result["remaining_reasons"] == ["capture_not_available"]


def test_assess_uses_older_verified_attestation_when_latest_is_damaged(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    forecast_id = uuid4()
    image = "sha256:" + "a" * 64
    manifest_json = json.dumps({"runtime_image_digest": image})
    captured_hash = hashlib.sha256(manifest_json.encode()).hexdigest()
    now = datetime(2026, 9, 25, tzinfo=UTC)
    older_backup_id = uuid4()

    def record(backup_id: object) -> dict[str, str]:
        return {
            "id": str(uuid4()),
            "forecast_id": str(forecast_id),
            "backup_id": str(backup_id),
            "capture_manifest_sha256": captured_hash,
            "snapshot_sha256": "b" * 64,
            "forecast_values_sha256": "c" * 64,
            "artifact_sha256": "d" * 64,
            "runtime_image_digest": image,
            "backup_manifest_sha256": "e" * 64,
            "database_dump_sha256": "f" * 64,
            "image_archive_sha256": "1" * 64,
            "restored_at": now.isoformat(),
        }

    latest = record(uuid4())
    older = record(older_backup_id)
    payload = {
        "evidence": {
            "status": "evidence_incomplete",
            "manifest_json": manifest_json,
            "snapshot_sha256": "b" * 64,
            "artifact_sha256": "d" * 64,
            "thresholds_json": "[]",
            "reason": "runtime_image_bytes_unpinned",
        },
        "attestations": [latest, older],
    }
    monkeypatch.setattr(
        host.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload).encode()
        ),
    )

    class Health:
        def __init__(self, proof: BackupProof) -> None:
            self._proof = proof

        def proof(self) -> BackupProof:
            return self._proof

    def health(backup_id: object, **_kwargs: object) -> Health:
        if backup_id != older_backup_id:
            return Health(BackupProof(status=BackupProofStatus.INVALID))
        return Health(
            BackupProof(
                status=BackupProofStatus.VERIFIED,
                backup_id=older_backup_id,
                manifest_sha256="e" * 64,
                database_dump_sha256="f" * 64,
                image_archives=((image, "1" * 64),),
                sample_forecast_id=ForecastId(forecast_id),
                capture_manifest_sha256=captured_hash,
                snapshot_sha256="b" * 64,
                forecast_values_sha256="c" * 64,
                artifact_sha256="d" * 64,
                runtime_image_digest=image,
                restored_at=now,
            )
        )

    monkeypatch.setattr(host, "attestation_backup_health", health)
    result = host.assess_forecast_preservation(
        forecast_id=ForecastId(forecast_id),
        target=tmp_path,
        database_volume=tmp_path,
        compose_file=Path("docker-compose.yml"),
        now=now,
    )
    assert result["effective_preservation_status"] == "complete"
    assert result["attestation_id"] == older["id"]


def test_reconcile_restored_dump_rechecks_chain_before_attesting(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    now = datetime(2026, 9, 25, tzinfo=UTC)
    image = "sha256:" + "a" * 64
    manifest = ProtectedBackupManifest(
        backup_id=uuid4(),
        created_at=now,
        restored_at=now,
        database_dump_sha256="b" * 64,
        database_dump_bytes=1,
        image_archives=[
            ImageArchive(image_digest=image, archive_sha256="c" * 64, byte_length=1)
        ],
        sample_forecast_id=uuid4(),
        capture_manifest_sha256="d" * 64,
        snapshot_sha256="e" * 64,
        forecast_values_sha256="f" * 64,
        artifact_sha256="1" * 64,
        runtime_image_digest=image,
    )
    monkeypatch.setattr(
        host,
        "attestation_backup_health",
        lambda *_args, **_kwargs: BackupHealth(
            status=BackupProofStatus.VERIFIED,
            manifest=manifest,
            manifest_sha256="2" * 64,
            reason=None,
        ),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        host, "_verify_restored_chain", lambda **_kwargs: calls.append("restore")
    )
    monkeypatch.setattr(host, "_attest", lambda *_args: calls.append("attest"))

    result = host.reconcile_restored_backup(
        backup_id=manifest.backup_id,
        target=tmp_path,
        database_volume=tmp_path,
        compose_file=Path("docker-compose.yml"),
        rehearsal_script=Path("scripts/restore-rehearsal.sh"),
        now=now,
    )
    assert result.backup_id == manifest.backup_id
    assert calls == ["restore", "attest"]
