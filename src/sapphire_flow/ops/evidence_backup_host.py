from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel

from sapphire_flow.config.deployment import load_config
from sapphire_flow.ops.protected_evidence_backup import (
    ImageArchive,
    ProtectedBackupManifest,
    attestation_backup_health,
    file_sha256,
    latest_backup_health,
    verify_image_archive,
    verify_image_directory,
    verify_separate_target,
)
from sapphire_flow.services.forecast_preservation import assess_effective_preservation
from sapphire_flow.types.forecast_evidence import (
    EvidenceStatus,
    PersistedForecastEvidence,
)
from sapphire_flow.types.forecast_preservation import (
    BackupProof,
    BackupProofStatus,
    PreservationAttestation,
    PreservationStatus,
)
from sapphire_flow.types.ids import ForecastId

log = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


class _Sample(BaseModel):
    forecast_id: UUID
    capture_manifest_sha256: str
    snapshot_sha256: str
    artifact_sha256: str
    forecast_values_sha256: str
    runtime_image_digest: str


class _Description(BaseModel):
    sample: _Sample | None
    image_digests: list[str]


class _AssessmentEvidence(BaseModel):
    status: EvidenceStatus
    manifest_json: str
    snapshot_sha256: str | None
    artifact_sha256: str | None
    thresholds_json: str | None
    reason: str | None


class _AssessmentAttestation(BaseModel):
    id: UUID
    forecast_id: UUID
    backup_id: UUID
    capture_manifest_sha256: str
    snapshot_sha256: str
    forecast_values_sha256: str
    artifact_sha256: str | None
    runtime_image_digest: str
    backup_manifest_sha256: str
    database_dump_sha256: str
    image_archive_sha256: str
    restored_at: datetime


class _AssessmentInput(BaseModel):
    evidence: _AssessmentEvidence | None
    attestations: list[_AssessmentAttestation]


def _compose_command(compose_file: Path, service: str, action: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "exec",
        "-T",
        service,
        "/entrypoint.sh",
        "python",
        "-m",
        "sapphire_flow.ops.evidence_backup_worker",
        action,
    ]


def _describe(compose_file: Path) -> _Description:
    result = subprocess.run(  # noqa: S603 — fixed command shape
        _compose_command(compose_file, "prefect-worker-backup", "describe"),
        capture_output=True,
        check=True,
        timeout=120,
    )
    return _Description.model_validate_json(result.stdout)


def _verify_running_worker_image(compose_file: Path) -> None:
    container = subprocess.run(  # noqa: S603 — fixed docker argv
        ["docker", "compose", "-f", str(compose_file), "ps", "-q", "prefect-worker"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    if not container:
        raise RuntimeError("forecast worker is not running")
    actual_image = subprocess.run(  # noqa: S603 — fixed docker argv
        ["docker", "inspect", "--format", "{{.Image}}", container],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    raw_env = subprocess.run(  # noqa: S603 — fixed docker argv
        ["docker", "inspect", "--format", "{{json .Config.Env}}", container],
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    env_items: object = json.loads(raw_env)
    if not isinstance(env_items, list):
        raise RuntimeError("forecast worker environment is unreadable")
    environment = cast("list[object]", env_items)
    configured = next(
        (
            item.removeprefix("SAPPHIRE_IMAGE_DIGEST=")
            for item in environment
            if isinstance(item, str) and item.startswith("SAPPHIRE_IMAGE_DIGEST=")
        ),
        None,
    )
    if configured != actual_image:
        raise RuntimeError("forecast worker image digest is not bound to its image")


def _dump(compose_file: Path, path: Path) -> None:
    with path.open("wb") as output:
        result = subprocess.run(  # noqa: S603 — fixed command shape
            _compose_command(compose_file, "prefect-worker-backup", "dump"),
            stdout=output,
            stderr=subprocess.PIPE,
            check=False,
            timeout=1900,
        )
        output.flush()
        os.fsync(output.fileno())
    if result.returncode != 0 or path.stat().st_size == 0:
        raise RuntimeError(
            "protected pg_dump failed: "
            + result.stderr.decode("utf-8", errors="replace")[:1000]
        )


def _archive_image(target: Path, image_digest: str) -> ImageArchive:
    images = target / "images"
    images.mkdir(mode=0o700, exist_ok=True)
    verify_image_directory(target)
    path = images / f"{image_digest[7:]}.tar"
    if path.exists():
        if path.is_symlink() or path.stat().st_dev != target.stat().st_dev:
            raise ValueError("protected image archive leaves the backup volume")
        verify_image_archive(path, image_digest)
    else:
        fd, temp_name = tempfile.mkstemp(prefix="image-", suffix=".tmp", dir=images)
        os.close(fd)
        temp = Path(temp_name)
        try:
            subprocess.run(  # noqa: S603 — fixed docker argv
                ["docker", "image", "save", "--output", str(temp), image_digest],
                check=True,
                timeout=1800,
            )
            verify_image_archive(temp, image_digest)
            with temp.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temp, path)
            _fsync_directory(images)
        finally:
            temp.unlink(missing_ok=True)
    return ImageArchive(
        image_digest=image_digest,
        archive_sha256=file_sha256(path),
        byte_length=path.stat().st_size,
    )


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _target_lock(target: Path) -> Iterator[None]:
    fd = os.open(target / ".evidence-backup.lock", os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + 30
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "protected evidence backup already running"
                    ) from None
                time.sleep(0.1)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _check_headroom(target: Path) -> None:
    dumps = sorted(target.glob("backup-*.dump"), key=lambda path: path.stat().st_mtime)
    previous_size = dumps[-1].stat().st_size if dumps else 0
    required = max(200 * 1024 * 1024, previous_size * 2)
    if shutil.disk_usage(target).free < required:
        raise RuntimeError("protected target lacks headroom for a new full dump")


def _verify_restored_chain(
    *,
    dump: Path,
    sample: _Sample,
    images: list[ImageArchive],
    rehearsal_script: Path,
) -> None:
    environment = {
        **os.environ,
        "SAPPHIRE_EVIDENCE_FORECAST_ID": str(sample.forecast_id),
        "SAPPHIRE_EVIDENCE_CAPTURE_MANIFEST_SHA256": sample.capture_manifest_sha256,
        "SAPPHIRE_EVIDENCE_SNAPSHOT_SHA256": sample.snapshot_sha256,
        "SAPPHIRE_EVIDENCE_ARTIFACT_SHA256": sample.artifact_sha256,
        "SAPPHIRE_EVIDENCE_FORECAST_VALUES_SHA256": sample.forecast_values_sha256,
        "SAPPHIRE_EVIDENCE_RUNTIME_IMAGE_DIGEST": sample.runtime_image_digest,
    }
    subprocess.run(  # noqa: S603 — operator-selected checked-in script
        ["bash", str(rehearsal_script), str(dump)],
        env=environment,
        check=True,
        timeout=1200,
    )
    for image in images:
        archive = dump.parent / "images" / f"{image.image_digest[7:]}.tar"
        subprocess.run(  # noqa: S603 — fixed docker argv
            ["docker", "image", "load", "--input", str(archive)],
            stdout=subprocess.DEVNULL,
            check=True,
            timeout=1800,
        )
        result = subprocess.run(  # noqa: S603 — fixed docker argv
            ["docker", "image", "inspect", "--format", "{{.Id}}", image.image_digest],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        if result.stdout.strip() != image.image_digest:
            raise ValueError("loaded runtime image digest does not match capture")


def _attest(
    compose_file: Path,
    manifest: ProtectedBackupManifest,
    manifest_sha256: str,
) -> None:
    sample_image = next(
        item
        for item in manifest.image_archives
        if item.image_digest == manifest.runtime_image_digest
    )
    payload = {
        "forecast_id": str(manifest.sample_forecast_id),
        "backup_id": str(manifest.backup_id),
        "capture_manifest_sha256": manifest.capture_manifest_sha256,
        "snapshot_sha256": manifest.snapshot_sha256,
        "forecast_values_sha256": manifest.forecast_values_sha256,
        "artifact_sha256": manifest.artifact_sha256,
        "runtime_image_digest": manifest.runtime_image_digest,
        "backup_manifest_sha256": manifest_sha256,
        "database_dump_sha256": manifest.database_dump_sha256,
        "image_archive_sha256": sample_image.archive_sha256,
        "restored_at": manifest.restored_at.isoformat(),
    }
    subprocess.run(  # noqa: S603 — fixed command shape
        _compose_command(compose_file, "prefect-worker", "attest"),
        input=json.dumps(payload).encode(),
        check=True,
        timeout=120,
    )


def assess_forecast_preservation(
    *,
    forecast_id: ForecastId,
    target: Path,
    database_volume: Path,
    compose_file: Path,
    now: datetime,
) -> dict[str, object]:
    result = subprocess.run(  # noqa: S603 — fixed command shape
        [
            *_compose_command(
                compose_file, "prefect-worker-backup", "assessment-input"
            ),
            "--forecast-id",
            str(forecast_id),
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )
    state = _AssessmentInput.model_validate_json(result.stdout)
    if state.evidence is None:
        return {
            "capture_status": "evidence_incomplete",
            "effective_preservation_status": "evidence_incomplete",
            "attestation_id": None,
            "remaining_reasons": ["capture_not_available"],
        }
    captured = state.evidence
    evidence = PersistedForecastEvidence(
        status=captured.status,
        manifest_json=captured.manifest_json,
        snapshot=None,
        snapshot_sha256=captured.snapshot_sha256,
        artifact=None,
        artifact_sha256=captured.artifact_sha256,
        thresholds_json=captured.thresholds_json,
        reason=captured.reason,
    )
    assessment = assess_effective_preservation(
        forecast_id, evidence, None, BackupProof(status=BackupProofStatus.MISSING)
    )
    for record in state.attestations:
        attestation = PreservationAttestation(
            id=record.id,
            forecast_id=ForecastId(record.forecast_id),
            backup_id=record.backup_id,
            capture_manifest_sha256=record.capture_manifest_sha256,
            snapshot_sha256=record.snapshot_sha256,
            forecast_values_sha256=record.forecast_values_sha256,
            artifact_sha256=record.artifact_sha256,
            runtime_image_digest=record.runtime_image_digest,
            backup_manifest_sha256=record.backup_manifest_sha256,
            database_dump_sha256=record.database_dump_sha256,
            image_archive_sha256=record.image_archive_sha256,
            restored_at=record.restored_at,
        )
        proof = attestation_backup_health(
            record.backup_id,
            target=target,
            database_volume=database_volume,
            now=now,
        ).proof()
        candidate = assess_effective_preservation(
            forecast_id, evidence, attestation, proof
        )
        if assessment.attestation_id is None or (
            candidate.effective_status is PreservationStatus.COMPLETE
        ):
            assessment = candidate
        if candidate.effective_status is PreservationStatus.COMPLETE:
            break
    return {
        "capture_status": assessment.capture_status.value,
        "effective_preservation_status": assessment.effective_status.value,
        "attestation_id": str(assessment.attestation_id)
        if assessment.attestation_id
        else None,
        "remaining_reasons": list(assessment.remaining_reasons),
    }


def reconcile_restored_backup(
    *,
    backup_id: UUID,
    target: Path,
    database_volume: Path,
    compose_file: Path,
    rehearsal_script: Path,
    now: datetime,
) -> ProtectedBackupManifest:
    health = attestation_backup_health(
        backup_id, target=target, database_volume=database_volume, now=now
    )
    manifest = health.manifest
    if (
        health.status is not BackupProofStatus.VERIFIED
        or manifest is None
        or health.manifest_sha256 is None
        or manifest.artifact_sha256 is None
    ):
        raise RuntimeError(f"restored backup cannot be reconciled: {health.reason}")
    sample = _Sample(
        forecast_id=manifest.sample_forecast_id,
        capture_manifest_sha256=manifest.capture_manifest_sha256,
        snapshot_sha256=manifest.snapshot_sha256,
        artifact_sha256=manifest.artifact_sha256,
        forecast_values_sha256=manifest.forecast_values_sha256,
        runtime_image_digest=manifest.runtime_image_digest,
    )
    _verify_restored_chain(
        dump=target / f"backup-{backup_id}.dump",
        sample=sample,
        images=manifest.image_archives,
        rehearsal_script=rehearsal_script,
    )
    _attest(compose_file, manifest, health.manifest_sha256)
    return manifest


def create_protected_backup(
    *,
    target: Path,
    database_volume: Path,
    compose_file: Path,
    rehearsal_script: Path,
    now: datetime,
    backup_id: UUID,
    clock: Callable[[], datetime],
) -> ProtectedBackupManifest:
    verify_separate_target(target, database_volume)
    with _target_lock(target):
        return _create_protected_backup_locked(
            target=target,
            compose_file=compose_file,
            rehearsal_script=rehearsal_script,
            now=now,
            backup_id=backup_id,
            clock=clock,
        )


def _create_protected_backup_locked(
    *,
    target: Path,
    compose_file: Path,
    rehearsal_script: Path,
    now: datetime,
    backup_id: UUID,
    clock: Callable[[], datetime],
) -> ProtectedBackupManifest:
    _check_headroom(target)
    _verify_running_worker_image(compose_file)
    before = _describe(compose_file)
    if before.sample is None:
        raise RuntimeError("no forecast with an attributable runtime image to restore")
    dump = target / f"backup-{backup_id}.dump"
    fd, temp_name = tempfile.mkstemp(prefix="backup-", suffix=".tmp", dir=target)
    os.close(fd)
    temp = Path(temp_name)
    try:
        _dump(compose_file, temp)
        after = _describe(compose_file)
        image_digests = sorted(
            set(after.image_digests) | {before.sample.runtime_image_digest}
        )
        images = [_archive_image(target, digest) for digest in image_digests]
        _verify_restored_chain(
            dump=temp,
            sample=before.sample,
            images=images,
            rehearsal_script=rehearsal_script,
        )
        os.replace(temp, dump)
        _fsync_directory(target)
        restored_at = clock()
        manifest = ProtectedBackupManifest(
            backup_id=backup_id,
            created_at=now,
            restored_at=restored_at,
            database_dump_sha256=file_sha256(dump),
            database_dump_bytes=dump.stat().st_size,
            image_archives=images,
            sample_forecast_id=before.sample.forecast_id,
            capture_manifest_sha256=before.sample.capture_manifest_sha256,
            snapshot_sha256=before.sample.snapshot_sha256,
            forecast_values_sha256=before.sample.forecast_values_sha256,
            artifact_sha256=before.sample.artifact_sha256,
            runtime_image_digest=before.sample.runtime_image_digest,
        )
        manifest_path = target / f"backup-{backup_id}.json"
        fd, temp_name = tempfile.mkstemp(prefix="manifest-", suffix=".tmp", dir=target)
        os.close(fd)
        temp_manifest = Path(temp_name)
        try:
            temp_manifest.write_bytes(manifest.model_dump_json().encode())
            with temp_manifest.open("rb") as stream:
                os.fsync(stream.fileno())
            _attest(compose_file, manifest, file_sha256(temp_manifest))
            os.replace(temp_manifest, manifest_path)
            _fsync_directory(target)
        finally:
            temp_manifest.unlink(missing_ok=True)
        log.info("evidence_backup.protected", backup_id=str(backup_id))
        return manifest
    finally:
        temp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("backup", "health", "assess", "reconcile"))
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--database-volume", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--forecast-id", type=UUID)
    parser.add_argument("--backup-id", type=UUID)
    parser.add_argument("--compose-file", type=Path, default=Path("docker-compose.yml"))
    parser.add_argument(
        "--rehearsal-script", type=Path, default=Path("scripts/restore-rehearsal.sh")
    )
    args = parser.parse_args(argv)
    config = load_config(args.config)
    now = datetime.now(UTC)
    if args.action == "backup":
        result = create_protected_backup(
            target=args.target,
            database_volume=args.database_volume,
            compose_file=args.compose_file,
            rehearsal_script=args.rehearsal_script,
            now=now,
            backup_id=uuid4(),
            clock=lambda: datetime.now(UTC),
        )
        sys_result = {
            "backup_id": str(result.backup_id),
            "restored_at": result.restored_at.isoformat(),
        }
    elif args.action == "health":
        health = latest_backup_health(
            args.target,
            database_volume=args.database_volume,
            now=now,
            max_age_hours=config.protected_backup_max_age_hours,
        )
        sys_result = {"status": health.status.value, "reason": health.reason}
        if health.status is not BackupProofStatus.VERIFIED:
            sys.stdout.write(json.dumps(sys_result) + "\n")
            return 1
    elif args.action == "assess":
        if args.forecast_id is None:
            parser.error("assess requires --forecast-id")
        sys_result = assess_forecast_preservation(
            forecast_id=ForecastId(args.forecast_id),
            target=args.target,
            database_volume=args.database_volume,
            compose_file=args.compose_file,
            now=now,
        )
    else:
        if args.backup_id is None:
            parser.error("reconcile requires --backup-id")
        manifest = reconcile_restored_backup(
            backup_id=args.backup_id,
            target=args.target,
            database_volume=args.database_volume,
            compose_file=args.compose_file,
            rehearsal_script=args.rehearsal_script,
            now=now,
        )
        sys_result = {
            "backup_id": str(manifest.backup_id),
            "forecast_id": str(manifest.sample_forecast_id),
            "reconciled": True,
        }
    sys.stdout.write(json.dumps(sys_result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
