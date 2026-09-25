from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.ops import protected_evidence_backup as backup
from sapphire_flow.ops.protected_evidence_backup import (
    ImageArchive,
    ProtectedBackupManifest,
    estimate_six_year_bytes,
    verify_protected_backup,
)
from sapphire_flow.types.forecast_preservation import BackupProofStatus

if TYPE_CHECKING:
    from pathlib import Path


def _image_archive(path: Path) -> str:
    layer = b"layer bytes"
    layer_digest = "sha256:" + hashlib.sha256(layer).hexdigest()
    config = json.dumps(
        {"architecture": "amd64", "rootfs": {"diff_ids": [layer_digest]}}
    ).encode()
    digest = "sha256:" + hashlib.sha256(config).hexdigest()
    manifest = json.dumps(
        [{"Config": f"{digest[7:]}.json", "Layers": ["layer/layer.tar"]}]
    ).encode()
    with tarfile.open(path, "w") as archive:
        for name, payload in (
            (f"{digest[7:]}.json", config),
            ("manifest.json", manifest),
            ("layer/layer.tar", layer),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return digest


def _oci_image_archive(path: Path, *, corrupt_layer: bool = False) -> str:
    layer = b"layer bytes"
    diff_id = "sha256:" + hashlib.sha256(layer).hexdigest()
    compressed = gzip.compress(b"wrong bytes" if corrupt_layer else layer)
    layer_hash = hashlib.sha256(compressed).hexdigest()
    config = json.dumps({"rootfs": {"diff_ids": [diff_id]}}).encode()
    config_hash = hashlib.sha256(config).hexdigest()
    leaf = json.dumps({"config": {"digest": f"sha256:{config_hash}"}}).encode()
    leaf_hash = hashlib.sha256(leaf).hexdigest()
    nested = json.dumps({"manifests": [{"digest": f"sha256:{leaf_hash}"}]}).encode()
    image_digest = "sha256:" + hashlib.sha256(nested).hexdigest()
    index = json.dumps({"manifests": [{"digest": image_digest}]}).encode()
    manifest = json.dumps(
        [
            {
                "Config": f"blobs/sha256/{config_hash}",
                "Layers": [f"blobs/sha256/{layer_hash}"],
            }
        ]
    ).encode()
    with tarfile.open(path, "w") as archive:
        for name, payload in (
            ("index.json", index),
            ("manifest.json", manifest),
            (f"blobs/sha256/{image_digest[7:]}", nested),
            (f"blobs/sha256/{leaf_hash}", leaf),
            (f"blobs/sha256/{config_hash}", config),
            (f"blobs/sha256/{layer_hash}", compressed),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return image_digest


def _manifest(tmp_path: Path, restored_at: datetime) -> Path:
    (tmp_path / "images").mkdir()
    image_path = tmp_path / "images" / "placeholder.tar"
    image_digest = _image_archive(image_path)
    archive = tmp_path / "images" / f"{image_digest[7:]}.tar"
    image_path.rename(archive)
    backup_id = uuid4()
    dump = tmp_path / f"backup-{backup_id}.dump"
    dump.write_bytes(b"database dump")
    manifest = ProtectedBackupManifest(
        backup_id=backup_id,
        created_at=restored_at,
        restored_at=restored_at,
        database_dump_sha256=backup.file_sha256(dump),
        database_dump_bytes=dump.stat().st_size,
        image_archives=[
            ImageArchive(
                image_digest=image_digest,
                archive_sha256=backup.file_sha256(archive),
                byte_length=archive.stat().st_size,
            )
        ],
        sample_forecast_id=uuid4(),
        capture_manifest_sha256="a" * 64,
        snapshot_sha256="b" * 64,
        forecast_values_sha256="d" * 64,
        artifact_sha256="c" * 64,
        runtime_image_digest=image_digest,
    )
    path = tmp_path / f"backup-{backup_id}.json"
    path.write_text(manifest.model_dump_json())
    return path


class TestProtectedBackupHealth:
    def test_oci_image_archive_checks_uncompressed_layer(self, tmp_path: Path) -> None:
        good = tmp_path / "good.tar"
        digest = _oci_image_archive(good)
        backup.verify_image_archive(good, digest)

        bad = tmp_path / "bad.tar"
        bad_digest = _oci_image_archive(bad, corrupt_layer=True)
        with pytest.raises(ValueError, match="layer digest mismatch"):
            backup.verify_image_archive(bad, bad_digest)

    def test_restored_chain_is_verified_and_corruption_closes_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup, "verify_separate_target", lambda *_: None)
        now = datetime(2026, 9, 25, tzinfo=UTC)
        path = _manifest(tmp_path, now)

        healthy = verify_protected_backup(
            path,
            target=tmp_path,
            database_volume=tmp_path,
            now=now,
            max_age_hours=36,
        )
        assert healthy.status is BackupProofStatus.VERIFIED

        archive = next((tmp_path / "images").glob("*.tar"))
        archive.write_bytes(b"corrupt")
        damaged = verify_protected_backup(
            path,
            target=tmp_path,
            database_volume=tmp_path,
            now=now,
            max_age_hours=36,
        )
        assert damaged.status is BackupProofStatus.INVALID

    def test_stale_restore_closes_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup, "verify_separate_target", lambda *_: None)
        now = datetime(2026, 9, 25, tzinfo=UTC)
        path = _manifest(tmp_path, now - timedelta(hours=37))

        health = verify_protected_backup(
            path,
            target=tmp_path,
            database_volume=tmp_path,
            now=now,
            max_age_hours=36,
        )

        assert health.status is BackupProofStatus.STALE

    def test_same_device_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="shares the database volume"):
            backup.verify_separate_target(tmp_path, tmp_path)

    def test_symlinked_image_directory_closes_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(backup, "verify_separate_target", lambda *_: None)
        now = datetime(2026, 9, 25, tzinfo=UTC)
        path = _manifest(tmp_path, now)
        relocated = tmp_path / "relocated_images"
        (tmp_path / "images").rename(relocated)
        (tmp_path / "images").symlink_to(relocated, target_is_directory=True)

        health = verify_protected_backup(
            path,
            target=tmp_path,
            database_volume=tmp_path,
            now=now,
            max_age_hours=36,
        )
        assert health.status is BackupProofStatus.INVALID
        assert "image directory leaves" in (health.reason or "")


def test_six_year_capacity_counts_live_data_and_unpruned_dumps() -> None:
    assert (
        estimate_six_year_bytes(
            daily_evidence_bytes=100,
            baseline_database_bytes=1000,
            image_bytes=500,
            interim_backup_days=7,
        )
        == (1000 + 100 * 2192) + (1000 * 7 + 100 * 28) + 500
    )
