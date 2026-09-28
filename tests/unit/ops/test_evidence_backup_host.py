from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.ops import evidence_backup_host as host
from sapphire_flow.ops.protected_evidence_backup import (
    BackupHealth,
    ImageArchive,
    ProtectedBackupManifest,
)
from sapphire_flow.ops.publication_backup_health import PublicationBacklog
from sapphire_flow.types.forecast_preservation import BackupProof, BackupProofStatus
from sapphire_flow.types.ids import ForecastId

if TYPE_CHECKING:
    from _pytest.capture import CaptureFixture
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

    def dump(_compose: Path, path: Path) -> list[host.PublishedForecastProof]:
        path.write_bytes(b"dump")
        return []

    monkeypatch.setattr(host, "_dump", dump)
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
    assert not any(
        path.name.endswith(".json") and not path.name.endswith(".pending.json")
        for path in tmp_path.glob("backup-*.json")
    )
    assert len(list(tmp_path.glob("backup-*.pending.json"))) == 1


def test_backup_attests_every_pending_publication_and_reconciles_interruption(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    now = datetime(2026, 9, 27, tzinfo=UTC)
    backup_id = uuid4()
    first_image = "sha256:" + "a" * 64
    second_image = "sha256:" + "b" * 64
    first = host.PublishedForecastProof(
        forecast_id=uuid4(),
        publication_decision_ids=[uuid4()],
        published_at=now,
        capture_manifest_sha256="c" * 64,
        snapshot_sha256="d" * 64,
        forecast_values_sha256="e" * 64,
        artifact_sha256="f" * 64,
        runtime_image_digest=first_image,
    )
    second = first.model_copy(
        update={
            "forecast_id": uuid4(),
            "publication_decision_ids": [uuid4()],
            "runtime_image_digest": second_image,
        }
    )
    sample = host._Sample(
        forecast_id=first.forecast_id,
        capture_manifest_sha256=first.capture_manifest_sha256,
        snapshot_sha256=first.snapshot_sha256,
        artifact_sha256=first.artifact_sha256 or "",
        forecast_values_sha256=first.forecast_values_sha256,
        runtime_image_digest=first_image,
    )
    monkeypatch.setattr(host, "_check_headroom", lambda *_: None)
    monkeypatch.setattr(host, "verify_separate_target", lambda *_: None)
    monkeypatch.setattr(host, "_verify_running_worker_image", lambda *_: None)
    monkeypatch.setattr(
        host,
        "_describe",
        lambda *_: host._Description(sample=sample, image_digests=[first_image]),
    )

    def dump(_compose: Path, path: Path) -> list[host.PublishedForecastProof]:
        path.write_bytes(b"dump")
        return [first, second]

    monkeypatch.setattr(host, "_dump", dump)
    monkeypatch.setattr(
        host,
        "_archive_image",
        lambda _target, digest: ImageArchive(
            image_digest=digest, archive_sha256="1" * 64, byte_length=1
        ),
    )
    verified: list[list[object] | None] = []
    monkeypatch.setattr(
        host,
        "_verify_restored_chain",
        lambda **kwargs: verified.append(kwargs["published"]),
    )
    monkeypatch.setattr(host, "_attest", lambda *_: None)
    calls: list[object] = []

    def interrupt(_compose: Path, _manifest: object, _hash: str, item: object) -> None:
        calls.append(item)
        raise RuntimeError("second attestation interrupted")

    monkeypatch.setattr(host, "_attest_published", interrupt)
    with pytest.raises(RuntimeError, match="second attestation interrupted"):
        host.create_protected_backup(
            target=tmp_path,
            database_volume=tmp_path,
            compose_file=Path("docker-compose.yml"),
            rehearsal_script=Path("scripts/restore-rehearsal.sh"),
            now=now,
            backup_id=backup_id,
            clock=lambda: now,
        )
    assert verified == [[first, second]]
    assert calls == [second]
    pending_path = tmp_path / f"backup-{backup_id}.pending.json"
    assert pending_path.exists()
    manifest = ProtectedBackupManifest.model_validate_json(pending_path.read_bytes())
    assert {item.forecast_id for item in manifest.published_forecasts} == {
        first.forecast_id,
        second.forecast_id,
    }
    assert {item.image_digest for item in manifest.image_archives} == {
        first_image,
        second_image,
    }
    monkeypatch.setattr(
        host,
        "verify_protected_backup",
        lambda *_args, **_kwargs: BackupHealth(
            status=BackupProofStatus.VERIFIED,
            manifest=manifest,
            manifest_sha256=hashlib.sha256(pending_path.read_bytes()).hexdigest(),
            reason=None,
        ),
    )
    monkeypatch.setattr(
        host, "_attest_published", lambda *_args: calls.append("recovered")
    )
    host.reconcile_restored_backup(
        backup_id=backup_id,
        target=tmp_path,
        database_volume=tmp_path,
        compose_file=Path("docker-compose.yml"),
        rehearsal_script=Path("scripts/restore-rehearsal.sh"),
        now=now,
    )
    assert calls == [second, "recovered"]
    assert not pending_path.exists()
    assert (tmp_path / f"backup-{backup_id}.json").exists()


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

        def proof_for(self, _forecast_id: ForecastId) -> BackupProof:
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


def test_health_command_alerts_on_overdue_publication_proof(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    now = datetime(2026, 9, 28, tzinfo=UTC)
    overdue_id = uuid4()
    monkeypatch.setattr(
        host,
        "load_config",
        lambda _path: SimpleNamespace(
            protected_backup_max_age_hours=36,
            publication_proof_retry_hours=24,
            publication_proof_window_hours=36,
        ),
    )
    monkeypatch.setattr(
        host,
        "latest_backup_health",
        lambda *_args, **_kwargs: BackupHealth(
            status=BackupProofStatus.VERIFIED,
            manifest=None,
            manifest_sha256=None,
            reason=None,
        ),
    )
    monkeypatch.setattr(host, "read_health_database_url", lambda _path: "host-url")
    monkeypatch.setattr(
        host,
        "write_health_projection",
        lambda **_kwargs: PublicationBacklog(
            pending_count=1,
            overdue_count=1,
            oldest_pending_at=now,
            next_retry_at=now,
            last_attempt_at=now,
            failed_forecasts=(overdue_id,),
        ),
    )
    exit_code = host.main(
        [
            "health",
            "--target",
            str(tmp_path),
            "--database-volume",
            str(tmp_path),
            "--health-database-url-file",
            str(tmp_path / "health-url"),
            "--retention-ready",
        ]
    )
    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "verified"
    assert output["overdue_count"] == 1
    assert output["failed_forecasts"] == [str(overdue_id)]


def test_interrupted_manifest_write_is_not_discoverable(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    now = datetime(2026, 9, 28, tzinfo=UTC)
    image = "sha256:" + "a" * 64
    manifest = ProtectedBackupManifest(
        schema_version=2,
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
        artifact_sha256=None,
        runtime_image_digest=image,
    )
    with monkeypatch.context() as patch_write:
        patch_write.setattr(
            host.os,
            "fsync",
            lambda _fd: (_ for _ in ()).throw(OSError("disk full")),
        )
        with pytest.raises(OSError, match="disk full"):
            host._write_pending_manifest(tmp_path, manifest)
    assert not list(tmp_path.glob("backup-*.pending.json"))
    assert not list(tmp_path.glob("manifest-*.tmp"))
    pending, digest = host._write_pending_manifest(tmp_path, manifest)
    assert pending.exists()
    assert digest == hashlib.sha256(pending.read_bytes()).hexdigest()


def test_failed_explicit_reconciliation_invalidates_health(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    backup_id = uuid4()
    monkeypatch.setattr(
        host,
        "load_config",
        lambda _path: SimpleNamespace(
            publication_proof_retry_hours=24,
            publication_proof_window_hours=36,
        ),
    )
    monkeypatch.setattr(
        host,
        "reconcile_restored_backup",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("restore failed")),
    )
    monkeypatch.setattr(host, "read_health_database_url", lambda _path: "host-url")
    projected: list[dict[str, object]] = []
    monkeypatch.setattr(
        host,
        "write_health_projection",
        lambda **kwargs: projected.append(kwargs),
    )
    with pytest.raises(RuntimeError, match="restore failed"):
        host.main(
            [
                "reconcile",
                "--target",
                str(tmp_path),
                "--database-volume",
                str(tmp_path),
                "--backup-id",
                str(backup_id),
                "--health-database-url-file",
                str(tmp_path / "health-url"),
            ]
        )
    assert len(projected) == 1
    assert projected[0]["health"].status is BackupProofStatus.INVALID
    assert projected[0]["retention_ready"] is False
    assert projected[0]["attempted_at"] == projected[0]["checked_at"]
