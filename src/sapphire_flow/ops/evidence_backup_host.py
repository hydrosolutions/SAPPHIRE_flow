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
    BackupHealth,
    ImageArchive,
    ProtectedBackupManifest,
    PublishedForecastProof,
    attestation_backup_health,
    file_sha256,
    latest_backup_health,
    verify_image_archive,
    verify_image_directory,
    verify_protected_backup,
    verify_separate_target,
)
from sapphire_flow.ops.publication_backup_health import (
    PublicationBacklog,
    read_health_database_url,
    write_health_projection,
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


def _dump(compose_file: Path, path: Path) -> list[PublishedForecastProof]:
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
    marker = "SAPPHIRE_PUBLICATION_SNAPSHOT_V1 "
    lines = result.stderr.decode("utf-8", errors="replace").splitlines()
    snapshots = [line.removeprefix(marker) for line in lines if line.startswith(marker)]
    if len(snapshots) != 1:
        raise RuntimeError("protected dump lacks its publication snapshot")
    raw: object = json.loads(snapshots[0])
    if not isinstance(raw, list):
        raise RuntimeError("publication snapshot is not a list")
    return [
        PublishedForecastProof.model_validate(item)
        for item in cast("list[object]", raw)
    ]


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
    published: list[PublishedForecastProof] | None = None,
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
    expectations: Path | None = None
    try:
        if published is not None:
            fd, name = tempfile.mkstemp(prefix="published-proof-", dir=dump.parent)
            expectations = Path(name)
            with os.fdopen(fd, "w") as stream:
                for item in published:
                    stream.write(
                        "|".join(
                            (
                                str(item.forecast_id),
                                item.capture_manifest_sha256,
                                item.snapshot_sha256,
                                item.artifact_sha256 or "-",
                                item.forecast_values_sha256,
                                item.runtime_image_digest,
                                ",".join(
                                    str(decision_id)
                                    for decision_id in item.publication_decision_ids
                                ),
                            )
                        )
                        + "\n"
                    )
                stream.flush()
                os.fsync(stream.fileno())
            environment["SAPPHIRE_PUBLICATION_EXPECTATIONS_FILE"] = str(expectations)
        subprocess.run(  # noqa: S603 — operator-selected checked-in script
            ["bash", str(rehearsal_script), str(dump)],
            env=environment,
            check=True,
            timeout=1200,
        )
    finally:
        if expectations is not None:
            expectations.unlink(missing_ok=True)
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


def _attest_published(
    compose_file: Path,
    manifest: ProtectedBackupManifest,
    manifest_sha256: str,
    item: PublishedForecastProof,
) -> None:
    image = next(
        archive
        for archive in manifest.image_archives
        if archive.image_digest == item.runtime_image_digest
    )
    payload = {
        "forecast_id": str(item.forecast_id),
        "backup_id": str(manifest.backup_id),
        "capture_manifest_sha256": item.capture_manifest_sha256,
        "snapshot_sha256": item.snapshot_sha256,
        "forecast_values_sha256": item.forecast_values_sha256,
        "artifact_sha256": item.artifact_sha256,
        "runtime_image_digest": item.runtime_image_digest,
        "backup_manifest_sha256": manifest_sha256,
        "database_dump_sha256": manifest.database_dump_sha256,
        "image_archive_sha256": image.archive_sha256,
        "restored_at": manifest.restored_at.isoformat(),
    }
    subprocess.run(  # noqa: S603 — fixed command shape
        _compose_command(compose_file, "prefect-worker", "attest"),
        input=json.dumps(payload).encode(),
        check=True,
        timeout=120,
    )


def _add_backlog_result(result: dict[str, object], backlog: PublicationBacklog) -> None:
    result.update(
        pending_count=backlog.pending_count,
        overdue_count=backlog.overdue_count,
        oldest_pending_at=(
            backlog.oldest_pending_at.isoformat()
            if backlog.oldest_pending_at is not None
            else None
        ),
        next_retry_at=(
            backlog.next_retry_at.isoformat()
            if backlog.next_retry_at is not None
            else None
        ),
        last_attempt_at=(
            backlog.last_attempt_at.isoformat()
            if backlog.last_attempt_at is not None
            else None
        ),
        failed_forecasts=[str(item) for item in backlog.failed_forecasts],
    )


def _write_pending_manifest(
    target: Path, manifest: ProtectedBackupManifest
) -> tuple[Path, str]:
    pending = target / f"backup-{manifest.backup_id}.pending.json"
    fd, name = tempfile.mkstemp(prefix="manifest-", suffix=".tmp", dir=target)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(manifest.model_dump_json().encode())
            stream.flush()
            os.fsync(stream.fileno())
        digest = file_sha256(temporary)
        os.replace(temporary, pending)
        _fsync_directory(target)
        return pending, digest
    finally:
        temporary.unlink(missing_ok=True)


def _project_failed_run(
    credential_file: Path | None,
    *,
    checked_at: datetime,
    retry_hours: int,
    proof_window_hours: int,
    reason: str,
) -> None:
    if credential_file is None:
        return
    write_health_projection(
        database_url=read_health_database_url(credential_file),
        health=BackupHealth(
            status=BackupProofStatus.INVALID,
            manifest=None,
            manifest_sha256=None,
            reason=reason,
        ),
        checked_at=checked_at,
        retention_ready=False,
        retry_hours=retry_hours,
        proof_window_hours=proof_window_hours,
        attempted_at=checked_at,
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
        ).proof_for(forecast_id)
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
    pending_path = target / f"backup-{backup_id}.pending.json"
    health = (
        verify_protected_backup(
            pending_path,
            target=target,
            database_volume=database_volume,
            now=now,
            max_age_hours=None,
            allow_pending=True,
        )
        if pending_path.exists()
        else attestation_backup_health(
            backup_id, target=target, database_volume=database_volume, now=now
        )
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
        published=(
            manifest.published_forecasts if manifest.schema_version == 2 else None
        ),
    )
    _attest(compose_file, manifest, health.manifest_sha256)
    for item in manifest.published_forecasts:
        if item.forecast_id != manifest.sample_forecast_id:
            _attest_published(compose_file, manifest, health.manifest_sha256, item)
    if pending_path.exists():
        os.replace(pending_path, target / f"backup-{backup_id}.json")
        _fsync_directory(target)
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
        for pending in sorted(target.glob("backup-*.pending.json")):
            pending_id = UUID(
                pending.name.removeprefix("backup-").removesuffix(".pending.json")
            )
            reconcile_restored_backup(
                backup_id=pending_id,
                target=target,
                database_volume=database_volume,
                compose_file=compose_file,
                rehearsal_script=rehearsal_script,
                now=now,
            )
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
        published = _dump(compose_file, temp)
        after = _describe(compose_file)
        image_digests = sorted(
            set(after.image_digests)
            | {before.sample.runtime_image_digest}
            | {item.runtime_image_digest for item in published}
        )
        images = [_archive_image(target, digest) for digest in image_digests]
        _verify_restored_chain(
            dump=temp,
            sample=before.sample,
            images=images,
            rehearsal_script=rehearsal_script,
            published=published,
        )
        os.replace(temp, dump)
        _fsync_directory(target)
        restored_at = clock()
        manifest = ProtectedBackupManifest(
            schema_version=2,
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
            published_forecasts=published,
        )
        manifest_path = target / f"backup-{backup_id}.json"
        pending_manifest = _write_pending_manifest(target, manifest)
        try:
            pending_path, manifest_sha256 = pending_manifest
            _attest(compose_file, manifest, manifest_sha256)
            for item in published:
                if item.forecast_id != manifest.sample_forecast_id:
                    _attest_published(compose_file, manifest, manifest_sha256, item)
            os.replace(pending_path, manifest_path)
            _fsync_directory(target)
        except Exception:
            log.exception(
                "evidence_backup.attestation_pending", backup_id=str(backup_id)
            )
            raise
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
    parser.add_argument("--health-database-url-file", type=Path)
    parser.add_argument("--retention-ready", action="store_true")
    parser.add_argument(
        "--rehearsal-script", type=Path, default=Path("scripts/restore-rehearsal.sh")
    )
    args = parser.parse_args(argv)
    config = load_config(args.config)
    now = datetime.now(UTC)
    if args.action == "backup":
        try:
            result = create_protected_backup(
                target=args.target,
                database_volume=args.database_volume,
                compose_file=args.compose_file,
                rehearsal_script=args.rehearsal_script,
                now=now,
                backup_id=uuid4(),
                clock=lambda: datetime.now(UTC),
            )
        except Exception:
            _project_failed_run(
                args.health_database_url_file,
                checked_at=datetime.now(UTC),
                retry_hours=config.publication_proof_retry_hours,
                proof_window_hours=config.publication_proof_window_hours,
                reason="protected_backup_run_failed",
            )
            raise
        sys_result: dict[str, object] = {
            "backup_id": str(result.backup_id),
            "restored_at": result.restored_at.isoformat(),
        }
        if args.health_database_url_file is not None:
            health = latest_backup_health(
                args.target,
                database_volume=args.database_volume,
                now=datetime.now(UTC),
                max_age_hours=config.protected_backup_max_age_hours,
            )
            backlog = write_health_projection(
                database_url=read_health_database_url(args.health_database_url_file),
                health=health,
                checked_at=datetime.now(UTC),
                retention_ready=args.retention_ready,
                retry_hours=config.publication_proof_retry_hours,
                proof_window_hours=config.publication_proof_window_hours,
                attempted_at=datetime.now(UTC),
            )
            _add_backlog_result(sys_result, backlog)
    elif args.action == "health":
        health = latest_backup_health(
            args.target,
            database_volume=args.database_volume,
            now=now,
            max_age_hours=config.protected_backup_max_age_hours,
        )
        sys_result = {"status": health.status.value, "reason": health.reason}
        if args.health_database_url_file is not None:
            backlog = write_health_projection(
                database_url=read_health_database_url(args.health_database_url_file),
                health=health,
                checked_at=now,
                retention_ready=args.retention_ready,
                retry_hours=config.publication_proof_retry_hours,
                proof_window_hours=config.publication_proof_window_hours,
            )
            _add_backlog_result(sys_result, backlog)
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
        try:
            manifest = reconcile_restored_backup(
                backup_id=args.backup_id,
                target=args.target,
                database_volume=args.database_volume,
                compose_file=args.compose_file,
                rehearsal_script=args.rehearsal_script,
                now=now,
            )
        except Exception:
            _project_failed_run(
                args.health_database_url_file,
                checked_at=datetime.now(UTC),
                retry_hours=config.publication_proof_retry_hours,
                proof_window_hours=config.publication_proof_window_hours,
                reason="protected_reconciliation_failed",
            )
            raise
        sys_result = {
            "backup_id": str(manifest.backup_id),
            "forecast_id": str(manifest.sample_forecast_id),
            "reconciled": True,
        }
        if args.health_database_url_file is not None:
            credential = read_health_database_url(args.health_database_url_file)
            recovered = attestation_backup_health(
                manifest.backup_id,
                target=args.target,
                database_volume=args.database_volume,
                now=now,
            )
            write_health_projection(
                database_url=credential,
                health=recovered,
                checked_at=now,
                retention_ready=args.retention_ready,
                retry_hours=config.publication_proof_retry_hours,
                proof_window_hours=config.publication_proof_window_hours,
                attempted_at=now,
            )
            current = latest_backup_health(
                args.target,
                database_volume=args.database_volume,
                now=now,
                max_age_hours=config.protected_backup_max_age_hours,
            )
            backlog = write_health_projection(
                database_url=credential,
                health=current,
                checked_at=now,
                retention_ready=args.retention_ready,
                retry_hours=config.publication_proof_retry_hours,
                proof_window_hours=config.publication_proof_window_hours,
            )
            _add_backlog_result(sys_result, backlog)
    sys.stdout.write(json.dumps(sys_result) + "\n")
    return 1 if sys_result.get("overdue_count", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
