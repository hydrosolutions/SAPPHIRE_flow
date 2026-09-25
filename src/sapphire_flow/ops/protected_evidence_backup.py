from __future__ import annotations

import gzip
import hashlib
import json
import re
import stat
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Self, cast
from uuid import UUID  # noqa: TC003 — Pydantic resolves this at runtime

from pydantic import BaseModel, Field, model_validator

from sapphire_flow.types.forecast_preservation import BackupProof, BackupProofStatus
from sapphire_flow.types.ids import ForecastId

if TYPE_CHECKING:
    from sapphire_flow.config.deployment import DeploymentConfig

_SHA256 = re.compile(r"[0-9a-f]{64}")
_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_MANIFEST_NAME = re.compile(r"backup-[0-9a-f-]{36}\.json")


class ImageArchive(BaseModel):
    image_digest: str
    archive_sha256: str
    byte_length: int = Field(ge=1)


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
        if any(_SHA256.fullmatch(value) is None for value in hashes):
            raise ValueError("invalid protected backup SHA256")
        digests = [self.runtime_image_digest]
        digests.extend(item.image_digest for item in self.image_archives)
        if any(_IMAGE_DIGEST.fullmatch(value) is None for value in digests):
            raise ValueError("invalid protected backup image digest")
        if self.runtime_image_digest not in {
            item.image_digest for item in self.image_archives
        }:
            raise ValueError("sample runtime image is not archived")
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
        return BackupProof(
            status=self.status,
            backup_id=self.manifest.backup_id,
            manifest_sha256=self.manifest_sha256,
            database_dump_sha256=self.manifest.database_dump_sha256,
            image_archives=tuple(
                (item.image_digest, item.archive_sha256)
                for item in self.manifest.image_archives
            ),
            sample_forecast_id=ForecastId(self.manifest.sample_forecast_id),
            capture_manifest_sha256=self.manifest.capture_manifest_sha256,
            snapshot_sha256=self.manifest.snapshot_sha256,
            forecast_values_sha256=self.manifest.forecast_values_sha256,
            artifact_sha256=self.manifest.artifact_sha256,
            runtime_image_digest=self.manifest.runtime_image_digest,
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


def verify_image_archive(path: Path, image_digest: str) -> None:
    if _IMAGE_DIGEST.fullmatch(image_digest) is None:
        raise ValueError("invalid runtime image digest")
    with tarfile.open(path, "r") as archive:
        member_list = archive.getmembers()
        members = {member.name: member for member in member_list}
        if len(members) != len(member_list) or "manifest.json" not in members:
            raise ValueError("runtime image archive has duplicate or missing members")
        if any(
            member.issym()
            or member.islnk()
            or member.name.startswith("/")
            or ".." in Path(member.name).parts
            for member in members.values()
        ):
            raise ValueError("runtime image archive contains an unsafe member")
        manifest_stream = archive.extractfile("manifest.json")
        if manifest_stream is None:
            raise ValueError("runtime image archive manifest is unreadable")
        raw_entries: object = json.loads(manifest_stream.read())
        if not isinstance(raw_entries, list):
            raise ValueError("runtime image archive manifest does not name config")
        image_entries = cast("list[object]", raw_entries)
        matching: list[tuple[dict[str, object], dict[str, object]]] = []
        for raw_entry in image_entries:
            if not isinstance(raw_entry, dict):
                continue
            entry = cast("dict[str, object]", raw_entry)
            config_name = entry.get("Config")
            if not isinstance(config_name, str) or config_name not in members:
                continue
            config_stream = archive.extractfile(config_name)
            if config_stream is None:
                continue
            config_bytes = config_stream.read()
            config_hash = hashlib.sha256(config_bytes).hexdigest()
            if not (
                config_hash == image_digest[7:]
                or _oci_image_reaches_config(
                    archive, members, image_digest, f"sha256:{config_hash}"
                )
            ):
                continue
            config_obj: object = json.loads(config_bytes)
            if not isinstance(config_obj, dict):
                raise ValueError("runtime image config is invalid")
            matching.append((entry, cast("dict[str, object]", config_obj)))
        if not matching:
            raise ValueError("runtime image archive manifest does not name config")
        for entry, config in matching:
            layers = entry.get("Layers")
            rootfs = config.get("rootfs")
            if not isinstance(rootfs, dict):
                raise ValueError("runtime image config has no rootfs")
            diff_ids = cast("dict[str, object]", rootfs).get("diff_ids")
            if not isinstance(layers, list) or not isinstance(diff_ids, list):
                raise ValueError("runtime image layers are missing")
            layer_list = cast("list[object]", layers)
            diff_id_list = cast("list[object]", diff_ids)
            if len(layer_list) != len(diff_id_list):
                raise ValueError("runtime image layer count does not match config")
            for layer, diff_id in zip(layer_list, diff_id_list, strict=True):
                if not isinstance(layer, str) or layer not in members:
                    raise ValueError("runtime image archive layer is missing")
                if (
                    not isinstance(diff_id, str)
                    or _IMAGE_DIGEST.fullmatch(diff_id) is None
                ):
                    raise ValueError("runtime image layer digest is invalid")
                layer_stream = archive.extractfile(layer)
                if layer_stream is None:
                    raise ValueError("runtime image archive layer is unreadable")
                compressed_digest = hashlib.sha256()
                while chunk := layer_stream.read(1024 * 1024):
                    compressed_digest.update(chunk)
                if layer.startswith("blobs/sha256/") and (
                    compressed_digest.hexdigest() != layer.removeprefix("blobs/sha256/")
                ):
                    raise ValueError("runtime image compressed layer digest mismatch")
                layer_stream = archive.extractfile(layer)
                if layer_stream is None:
                    raise ValueError("runtime image archive layer is unreadable")
                prefix = layer_stream.read(2)
                layer_stream.seek(0)
                content = (
                    gzip.GzipFile(fileobj=layer_stream)
                    if prefix == b"\x1f\x8b"
                    else layer_stream
                )
                actual = hashlib.sha256()
                while chunk := content.read(1024 * 1024):
                    actual.update(chunk)
                if actual.hexdigest() != diff_id[7:]:
                    raise ValueError("runtime image layer digest mismatch")


def _oci_image_reaches_config(
    archive: tarfile.TarFile,
    members: dict[str, tarfile.TarInfo],
    image_digest: str,
    config_digest: str,
) -> bool:
    if "index.json" not in members:
        return False
    root = archive.extractfile("index.json")
    if root is None:
        return False
    index: object = json.loads(root.read())
    if not isinstance(index, dict):
        return False
    descriptors = cast("dict[str, object]", index).get("manifests")
    if not isinstance(descriptors, list):
        return False

    def reaches(descriptor: object, target_seen: bool, visited: frozenset[str]) -> bool:
        if not isinstance(descriptor, dict):
            return False
        digest = cast("dict[str, object]", descriptor).get("digest")
        if not isinstance(digest, str) or _IMAGE_DIGEST.fullmatch(digest) is None:
            return False
        name = f"blobs/sha256/{digest[7:]}"
        if name not in members or digest in visited:
            return False
        stream = archive.extractfile(name)
        if stream is None:
            return False
        raw = stream.read()
        if hashlib.sha256(raw).hexdigest() != digest[7:]:
            raise ValueError("OCI image descriptor digest mismatch")
        obj: object = json.loads(raw)
        if not isinstance(obj, dict):
            return False
        metadata = cast("dict[str, object]", obj)
        seen = target_seen or digest == image_digest
        config = metadata.get("config")
        if isinstance(config, dict):
            cfg_digest = cast("dict[str, object]", config).get("digest")
            if seen and cfg_digest == config_digest:
                return True
        children = metadata.get("manifests")
        return isinstance(children, list) and any(
            reaches(child, seen, visited | {digest})
            for child in cast("list[object]", children)
        )

    return any(
        reaches(item, False, frozenset()) for item in cast("list[object]", descriptors)
    )


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
) -> BackupHealth:
    try:
        verify_separate_target(target, database_volume)
        if (
            manifest_path.parent.resolve() != target.resolve()
            or _MANIFEST_NAME.fullmatch(manifest_path.name) is None
            or not _regular_file(manifest_path)
        ):
            raise ValueError("protected backup manifest is missing or misplaced")
        raw = manifest_path.read_bytes()
        manifest = ProtectedBackupManifest.model_validate_json(raw)
        if manifest.schema_version != 1:
            raise ValueError("unsupported protected backup manifest version")
        if manifest_path.name != f"backup-{manifest.backup_id}.json":
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
        target.glob("backup-*.json"),
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
