from __future__ import annotations

import hashlib
import json
import re
import stat
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Self
from uuid import UUID  # noqa: TC003 — Pydantic resolves this at runtime

from pydantic import BaseModel, Field, model_validator

from sapphire_flow.ops.image_archive import verify_image_archive as verify_image_archive
from sapphire_flow.types.forecast_preservation import BackupProof, BackupProofStatus
from sapphire_flow.types.ids import ForecastId

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.config.deployment import DeploymentConfig

_SHA256 = re.compile(r"[0-9a-f]{64}")
_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_MANIFEST_NAME = re.compile(r"backup-[0-9a-f-]{36}\.json")
_PENDING_MANIFEST_NAME = re.compile(r"backup-[0-9a-f-]{36}\.pending\.json")


class ImageArchive(BaseModel):
    image_digest: str
    archive_sha256: str
    byte_length: int = Field(ge=1)


class PublishedForecastProof(BaseModel):
    forecast_id: UUID
    publication_decision_ids: list[UUID] = Field(min_length=1)
    published_at: datetime
    capture_manifest_sha256: str
    snapshot_sha256: str
    forecast_values_sha256: str
    artifact_sha256: str | None
    runtime_image_digest: str

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if len(set(self.publication_decision_ids)) != len(
            self.publication_decision_ids
        ):
            raise ValueError("duplicate publication decision in protected manifest")
        hashes = (
            self.capture_manifest_sha256,
            self.snapshot_sha256,
            self.forecast_values_sha256,
        )
        if self.artifact_sha256 is not None:
            hashes += (self.artifact_sha256,)
        if any(_SHA256.fullmatch(value) is None for value in hashes):
            raise ValueError("invalid published forecast proof SHA256")
        if _IMAGE_DIGEST.fullmatch(self.runtime_image_digest) is None:
            raise ValueError("invalid published forecast runtime image digest")
        if self.published_at.tzinfo is None:
            raise ValueError("published forecast time needs a UTC offset")
        return self


def _empty_published_forecasts() -> list[PublishedForecastProof]:
    return []


class ProtectedBackupManifest(BaseModel):
    schema_version: int = 1
    backup_id: UUID
    created_at: datetime
    restored_at: datetime
    database_dump_sha256: str
    database_dump_bytes: int = Field(ge=1)
    image_archives: list[ImageArchive]
    sample_forecast_id: UUID
    capture_manifest_sha256: str
    snapshot_sha256: str
    forecast_values_sha256: str
    artifact_sha256: str | None
    runtime_image_digest: str
    published_forecasts: list[PublishedForecastProof] = Field(
        default_factory=_empty_published_forecasts
    )

    @model_validator(mode="after")
    def validate_digests(self) -> Self:
        hashes = [
            self.database_dump_sha256,
            self.capture_manifest_sha256,
            self.snapshot_sha256,
            self.forecast_values_sha256,
            *(item.archive_sha256 for item in self.image_archives),
        ]
        if self.artifact_sha256 is not None:
            hashes.append(self.artifact_sha256)
        for forecast in self.published_forecasts:
            hashes.extend(
                (
                    forecast.capture_manifest_sha256,
                    forecast.snapshot_sha256,
                    forecast.forecast_values_sha256,
                )
            )
            if forecast.artifact_sha256 is not None:
                hashes.append(forecast.artifact_sha256)
        if any(_SHA256.fullmatch(value) is None for value in hashes):
            raise ValueError("invalid protected backup SHA256")
        digests = [
            self.runtime_image_digest,
            *(forecast.runtime_image_digest for forecast in self.published_forecasts),
        ]
        digests.extend(item.image_digest for item in self.image_archives)
        if any(_IMAGE_DIGEST.fullmatch(value) is None for value in digests):
            raise ValueError("invalid protected backup image digest")
        archived = {item.image_digest for item in self.image_archives}
        if any(digest not in archived for digest in digests):
            raise ValueError("published runtime image is not archived")
        if len({item.forecast_id for item in self.published_forecasts}) != len(
            self.published_forecasts
        ):
            raise ValueError("duplicate published forecast in protected manifest")
        if self.schema_version == 1 and self.published_forecasts:
            raise ValueError("version 1 protected manifest cannot list publications")
        return self


@dataclass(frozen=True, kw_only=True, slots=True)
class BackupHealth:
    status: BackupProofStatus
    manifest: ProtectedBackupManifest | None
    manifest_sha256: str | None
    reason: str | None

    def proof(self) -> BackupProof:
        if self.manifest is None:
            return BackupProof(status=self.status)
        return self.proof_for(ForecastId(self.manifest.sample_forecast_id))

    def proof_for(self, forecast_id: ForecastId) -> BackupProof:
        if self.manifest is None:
            return BackupProof(status=self.status)
        candidate = next(
            (
                item
                for item in self.manifest.published_forecasts
                if item.forecast_id == forecast_id
            ),
            None,
        )
        if candidate is None and self.manifest.sample_forecast_id != forecast_id:
            return BackupProof(status=BackupProofStatus.MISSING)
        capture_manifest_sha256 = (
            candidate.capture_manifest_sha256
            if candidate is not None
            else self.manifest.capture_manifest_sha256
        )
        snapshot_sha256 = (
            candidate.snapshot_sha256
            if candidate is not None
            else self.manifest.snapshot_sha256
        )
        forecast_values_sha256 = (
            candidate.forecast_values_sha256
            if candidate is not None
            else self.manifest.forecast_values_sha256
        )
        artifact_sha256 = (
            candidate.artifact_sha256
            if candidate is not None
            else self.manifest.artifact_sha256
        )
        runtime_image_digest = (
            candidate.runtime_image_digest
            if candidate is not None
            else self.manifest.runtime_image_digest
        )
        return BackupProof(
            status=self.status,
            backup_id=self.manifest.backup_id,
            manifest_sha256=self.manifest_sha256,
            database_dump_sha256=self.manifest.database_dump_sha256,
            image_archives=tuple(
                (item.image_digest, item.archive_sha256)
                for item in self.manifest.image_archives
            ),
            sample_forecast_id=forecast_id,
            capture_manifest_sha256=capture_manifest_sha256,
            snapshot_sha256=snapshot_sha256,
            forecast_values_sha256=forecast_values_sha256,
            artifact_sha256=artifact_sha256,
            runtime_image_digest=runtime_image_digest,
            restored_at=self.manifest.restored_at,
        )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_separate_target(target: Path, database_volume: Path) -> None:
    if not target.is_dir() or not database_volume.is_dir():
        raise ValueError("protected target and database volume must exist")
    if target.is_symlink() or database_volume.is_symlink():
        raise ValueError("protected target and database volume cannot be symlinks")
    if target.stat().st_dev == database_volume.stat().st_dev:
        raise ValueError("protected target shares the database volume device")


def verify_image_directory(target: Path) -> Path:
    images = target / "images"
    if (
        not images.is_dir()
        or images.is_symlink()
        or images.stat().st_dev != target.stat().st_dev
        or not images.resolve().is_relative_to(target.resolve())
    ):
        raise ValueError("protected image directory leaves the backup volume")
    return images


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def verify_protected_backup(
    manifest_path: Path,
    *,
    target: Path,
    database_volume: Path,
    now: datetime,
    max_age_hours: int | None,
    allow_pending: bool = False,
) -> BackupHealth:
    try:
        verify_separate_target(target, database_volume)
        if (
            manifest_path.parent.resolve() != target.resolve()
            or not (
                _MANIFEST_NAME.fullmatch(manifest_path.name)
                or (
                    allow_pending
                    and _PENDING_MANIFEST_NAME.fullmatch(manifest_path.name)
                )
            )
            or not _regular_file(manifest_path)
        ):
            raise ValueError("protected backup manifest is missing or misplaced")
        raw = manifest_path.read_bytes()
        manifest = ProtectedBackupManifest.model_validate_json(raw)
        if manifest.schema_version not in (1, 2):
            raise ValueError("unsupported protected backup manifest version")
        expected_name = f"backup-{manifest.backup_id}"
        if manifest_path.name not in (
            f"{expected_name}.json",
            f"{expected_name}.pending.json" if allow_pending else "",
        ):
            raise ValueError("protected backup ID does not match filename")
        if manifest.restored_at.tzinfo is None or manifest.created_at.tzinfo is None:
            raise ValueError("protected backup times need UTC offsets")
        age = now.astimezone(UTC) - manifest.restored_at.astimezone(UTC)
        if age < timedelta(0):
            raise ValueError("protected backup restore time is in the future")
        if max_age_hours is not None and age > timedelta(hours=max_age_hours):
            return BackupHealth(
                status=BackupProofStatus.STALE,
                manifest=manifest,
                manifest_sha256=hashlib.sha256(raw).hexdigest(),
                reason="protected_backup_stale",
            )
        dump = target / f"backup-{manifest.backup_id}.dump"
        if (
            not _regular_file(dump)
            or dump.stat().st_size != manifest.database_dump_bytes
            or file_sha256(dump) != manifest.database_dump_sha256
        ):
            raise ValueError("protected database dump is missing or corrupt")
        images = verify_image_directory(target)
        for item in manifest.image_archives:
            archive = images / f"{item.image_digest[7:]}.tar"
            if (
                not _regular_file(archive)
                or archive.stat().st_size != item.byte_length
                or file_sha256(archive) != item.archive_sha256
            ):
                raise ValueError("protected runtime image is missing or corrupt")
            verify_image_archive(archive, item.image_digest)
        return BackupHealth(
            status=BackupProofStatus.VERIFIED,
            manifest=manifest,
            manifest_sha256=hashlib.sha256(raw).hexdigest(),
            reason=None,
        )
    except (OSError, ValueError, tarfile.TarError, json.JSONDecodeError) as exc:
        return BackupHealth(
            status=BackupProofStatus.INVALID,
            manifest=None,
            manifest_sha256=None,
            reason=str(exc),
        )


def latest_backup_health(
    target: Path,
    *,
    database_volume: Path,
    now: datetime,
    max_age_hours: int,
) -> BackupHealth:
    if not target.is_dir():
        return BackupHealth(
            status=BackupProofStatus.MISSING,
            manifest=None,
            manifest_sha256=None,
            reason="protected_backup_target_missing",
        )
    manifests = sorted(
        (
            path
            for path in target.glob("backup-*.json")
            if _MANIFEST_NAME.fullmatch(path.name)
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not manifests:
        return BackupHealth(
            status=BackupProofStatus.MISSING,
            manifest=None,
            manifest_sha256=None,
            reason="protected_backup_missing",
        )
    return verify_protected_backup(
        manifests[0],
        target=target,
        database_volume=database_volume,
        now=now,
        max_age_hours=max_age_hours,
    )


def attestation_backup_health(
    backup_id: UUID,
    *,
    target: Path,
    database_volume: Path,
    now: datetime,
) -> BackupHealth:
    return verify_protected_backup(
        target / f"backup-{backup_id}.json",
        target=target,
        database_volume=database_volume,
        now=now,
        max_age_hours=None,
    )


def require_publication_backup(
    config: DeploymentConfig,
    *,
    target: Path,
    database_volume: Path,
    now: datetime,
) -> BackupHealth:
    health = latest_backup_health(
        target,
        database_volume=database_volume,
        now=now,
        max_age_hours=config.protected_backup_max_age_hours,
    )
    if health.status is not BackupProofStatus.VERIFIED:
        raise RuntimeError(f"CHWRR publication backup gate closed: {health.reason}")
    return health


def estimate_six_year_bytes(
    *,
    daily_evidence_bytes: int,
    baseline_database_bytes: int,
    image_bytes: int,
    retention_days: int = 2192,
    interim_backup_days: int = 2192,
) -> int:
    if min(daily_evidence_bytes, baseline_database_bytes, image_bytes) < 0:
        raise ValueError("capacity inputs cannot be negative")
    if retention_days < 2192 or interim_backup_days < 1:
        raise ValueError("capacity estimate needs six years and at least one backup")
    projected_database = baseline_database_bytes + daily_evidence_bytes * retention_days
    retained_dumps = (
        baseline_database_bytes * interim_backup_days
        + daily_evidence_bytes * interim_backup_days * (interim_backup_days + 1) // 2
    )
    return projected_database + retained_dumps + image_bytes
