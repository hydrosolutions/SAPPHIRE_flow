from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from sapphire_flow.ops.image_archive import (
    describe_image_archive,
    unpacked_layer_bytes,
    verify_image_archive,
)


def oci_archive(path: Path, *, extra_config: bool = False) -> tuple[str, str]:
    layer = b"synthetic uncompressed image layer"
    compressed = gzip.compress(layer, mtime=0)
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {"Labels": {"org.opencontainers.image.version": "1.2.3"}},
            "rootfs": {
                "type": "layers",
                "diff_ids": ["sha256:" + hashlib.sha256(layer).hexdigest()],
            },
        }
    ).encode()
    blobs = {}

    def blob(content: bytes) -> str:
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        blobs["blobs/sha256/" + digest[7:]] = content
        return digest

    config_id, layer_id = blob(config), blob(compressed)
    manifest_id = blob(
        json.dumps(
            {"config": {"digest": config_id}, "layers": [{"digest": layer_id}]}
        ).encode()
    )
    index_id = blob(json.dumps({"manifests": [{"digest": manifest_id}]}).encode())
    root = {"manifests": [{"digest": index_id}]}
    if extra_config:
        other = blob(json.dumps({"config": {}, "rootfs": {"diff_ids": []}}).encode())
        extra = blob(json.dumps({"config": {"digest": other}}).encode())
        root["manifests"].append({"digest": extra})
    manifest = [
        {
            "Config": "blobs/sha256/" + config_id[7:],
            "RepoTags": None,
            "Layers": ["blobs/sha256/" + layer_id[7:]],
        }
    ]
    blobs["manifest.json"] = json.dumps(manifest).encode()
    blobs["index.json"] = json.dumps(root).encode()
    with tarfile.open(path, "w") as archive:
        for name, content in blobs.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return index_id, config_id


class TestDescribeImageArchive:
    def test_index_identity_is_preserved_and_reaches_config(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "image.tar"
        index_id, config_id = oci_archive(path)
        description = describe_image_archive(path, index_id)
        assert index_id != config_id
        assert description.config_digest == config_id
        assert description.platform == "linux/amd64"
        assert description.labels["org.opencontainers.image.version"] == "1.2.3"
        verify_image_archive(path, index_id)

    def test_undeclared_oci_image_is_not_accepted(self, tmp_path: Path) -> None:
        path = tmp_path / "image.tar"
        index_id, _ = oci_archive(path, extra_config=True)
        with pytest.raises(ValueError, match="configs differ"):
            describe_image_archive(path, index_id)

    def test_unknown_id_is_not_redefined_as_a_config(self, tmp_path: Path) -> None:
        path = tmp_path / "image.tar"
        oci_archive(path)
        with pytest.raises(ValueError, match="missing image config"):
            describe_image_archive(path, "sha256:" + "f" * 64)

    def test_links_are_rejected_without_extraction(self, tmp_path: Path) -> None:
        path = tmp_path / "image.tar"
        with tarfile.open(path, "w") as archive:
            link = tarfile.TarInfo("manifest.json")
            link.type = tarfile.SYMTYPE
            link.linkname = "outside"
            archive.addfile(link)
        with pytest.raises(ValueError, match="unsafe"):
            describe_image_archive(path, "sha256:" + "f" * 64)
        assert not (tmp_path / "outside").exists()


class TestUnpackedLayerBytes:
    def test_counts_uncompressed_layer_bytes_not_gzip_length(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "image.tar"
        oci_archive(path)
        assert unpacked_layer_bytes(path, 1000) == len(
            b"synthetic uncompressed image layer"
        )

    def test_stops_at_approved_layer_bound(self, tmp_path: Path) -> None:
        path = tmp_path / "image.tar"
        oci_archive(path)
        with pytest.raises(ValueError, match="exceed capacity"):
            unpacked_layer_bytes(path, 1)
