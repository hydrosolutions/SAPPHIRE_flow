from __future__ import annotations

import gzip
import hashlib
import json
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


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


@dataclass(frozen=True, kw_only=True, slots=True)
class ArchiveImageDescription:
    config_digest: str
    platform: str
    labels: dict[str, str]


def describe_image_archive(path: Path, image_digest: str) -> ArchiveImageDescription:
    # This local check does not bound earlier tar member enumeration by callers.
    with tarfile.open(path, "r:") as archive:
        member_list: list[tarfile.TarInfo] = []
        for member in archive:
            member_list.append(member)
            if len(member_list) > 100_000:
                raise ValueError("too many image archive members")
        members = {member.name: member for member in member_list}
        if len(members) != len(member_list) or any(
            not (m.isfile() or m.isdir())
            or m.name.startswith("/")
            or ".." in Path(m.name).parts
            for m in member_list
        ):
            raise ValueError("unsafe image archive member")

        def read_json(name: str) -> object:
            member = members.get(name)
            if (
                member is None
                or not member.isfile()
                or not 0 < member.size <= 8 * 1024 * 1024
            ):
                raise ValueError("invalid image metadata size")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("missing image metadata")
            raw = stream.read()
            if name.startswith("blobs/sha256/") and hashlib.sha256(
                raw
            ).hexdigest() != name.removeprefix("blobs/sha256/"):
                raise ValueError("OCI metadata digest mismatch")
            return json.loads(raw)

        visited: set[str] = set()
        oci_configs: set[str] = set()
        manifest_configs: set[str] = set()

        def inspect_descriptors(value: object, depth: int) -> None:
            if not isinstance(value, dict) or depth > 32 or len(visited) > 256:
                raise ValueError("invalid OCI descriptor graph")
            obj = cast("dict[str, object]", value)
            children = obj.get("manifests", [])
            if not isinstance(children, list):
                raise ValueError("invalid OCI manifest list")
            for child in cast("list[object]", children):
                if not isinstance(child, dict):
                    raise ValueError("invalid OCI descriptor")
                child_digest = cast("dict[str, object]", child).get("digest")
                if not isinstance(child_digest, str) or not _IMAGE_DIGEST.fullmatch(
                    child_digest
                ):
                    raise ValueError("invalid OCI descriptor digest")
                if child_digest not in visited:
                    visited.add(child_digest)
                    inspect_descriptors(
                        read_json("blobs/sha256/" + child_digest[7:]), depth + 1
                    )
            config = obj.get("config")
            if isinstance(config, dict):
                cfg_digest = cast("dict[str, object]", config).get("digest")
                if not isinstance(cfg_digest, str) or not _IMAGE_DIGEST.fullmatch(
                    cfg_digest
                ):
                    raise ValueError("invalid OCI config digest")
                read_json("blobs/sha256/" + cfg_digest[7:])
                oci_configs.add(cfg_digest)

        if "index.json" in members:
            inspect_descriptors(read_json("index.json"), 0)
        entries = read_json("manifest.json")
        if not isinstance(entries, list):
            raise ValueError("invalid image manifest")
        matches: list[ArchiveImageDescription] = []
        for raw in cast("list[object]", entries):
            if not isinstance(raw, dict):
                raise ValueError("invalid image manifest item")
            entry = cast("dict[str, object]", raw)
            config_name = entry.get("Config")
            if not isinstance(config_name, str):
                raise ValueError("missing image config")
            config = read_json(config_name)
            stream = archive.extractfile(config_name)
            if stream is None or not isinstance(config, dict):
                raise ValueError("invalid image config")
            config_digest = "sha256:" + hashlib.sha256(stream.read()).hexdigest()
            manifest_configs.add(config_digest)
            if config_digest != image_digest and not _oci_image_reaches_config(
                archive, members, image_digest, config_digest
            ):
                continue
            metadata = cast("dict[str, object]", config)
            runtime = metadata.get("config")
            if not isinstance(runtime, dict):
                raise ValueError("missing image labels")
            labels = cast("dict[str, object]", runtime).get("Labels")
            if not isinstance(labels, dict) or any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in cast("dict[object, object]", labels).items()
            ):
                raise ValueError("invalid image labels")
            matches.append(
                ArchiveImageDescription(
                    config_digest=config_digest,
                    platform=f"{metadata.get('os')}/{metadata.get('architecture')}",
                    labels=cast("dict[str, str]", labels),
                )
            )
        if "index.json" in members and oci_configs != manifest_configs:
            raise ValueError("OCI and Docker manifest configs differ")
        if len(matches) != 1:
            raise ValueError("ambiguous or missing image config")
    verify_image_archive(path, image_digest)
    return matches[0]


def unpacked_layer_bytes(path: Path, limit: int) -> int:
    total = 0
    with tarfile.open(path, "r:") as archive:
        manifest_file = archive.extractfile("manifest.json")
        if manifest_file is None:
            raise ValueError("missing image manifest")
        entries = json.loads(manifest_file.read(8 * 1024 * 1024 + 1))
        names = {name for entry in entries for name in entry["Layers"]}
        for name in sorted(names):
            stream = archive.extractfile(name)
            if stream is None:
                raise ValueError("missing image layer")
            prefix = stream.read(2)
            stream.seek(0)
            content = gzip.GzipFile(fileobj=stream) if prefix == b"\x1f\x8b" else stream
            with content:
                while chunk := content.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise ValueError("unpacked image layers exceed capacity")
    return total
