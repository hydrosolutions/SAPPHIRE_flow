from __future__ import annotations

import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
import posixpath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from types import ModuleType

REPOSITORY = "hydrosolutions/SAPPHIRE_flow"
ENVIRONMENT = "staging-release"
TARGET = "ubuntu@84.234.29.144"
REMOTE_COMMAND = (
    "/usr/bin/sudo -n /usr/bin/python3 -I "
    "/opt/sapphire-flow/staging-release/tools/staging_release_transfer.py receive"
)
CONFIG_PATH = Path("/etc/sapphire-flow/staging-release.json")
STORE_ROOT = Path("/srv/sapphire-staging-releases")
MAX_METADATA = 8 * 1024 * 1024
MAX_REPORT = 64 * 1024 * 1024
TRANSFER_STEP_SECONDS = 1800
TRANSFER_WORK_SECONDS = 1740
TRANSFER_CLEANUP_SECONDS = 60
MAX_RECEIVER_SECONDS = 1200
RECEIVER_CLEANUP_SECONDS = 60
ACK_MARGIN_SECONDS = 60
MAX_SENDER_SECONDS = 1500
RUNTIME_FILES = (
    "docker-compose.yml",
    "docker-compose.nepal-cloud.yml",
    "Caddyfile.nepal",
    "docker/entrypoint.sh",
    "docker/init-db.sh",
    "docker/bootstrap-roles.sh",
    "docker/bootstrap-roles.sql",
    "config/overlays/nepal-cloud.toml",
)
GATE_NAMES = (
    "lint",
    "unit-scripts",
    "unit-services",
    "unit-adapters-flows",
    "unit-rest",
    "unit-coverage",
    "wheel-only-guard",
    "integration-heavy",
    "integration-rest",
    "integration",
    "build-image-and-scan",
    "aquacast-release-image-proof",
)


class DeliveryMode(StrEnum):
    DELIVER_ONLY = "deliver-only"
    LOAD_IMAGES = "load-images"


class PayloadKind(StrEnum):
    RELEASE = "release"
    DIAGNOSTIC = "diagnostic"


class TransferError(ValueError):
    pass


def tool(name: str) -> ModuleType:
    path = Path(__file__).with_name(name + ".py")
    if name == "image_archive":
        path = (
            Path(__file__).resolve().parents[1]
            / "src/sapphire_flow/ops/image_archive.py"
        )
    module_name = "staging_" + name
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise TransferError("tool unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def parse_json(raw: bytes) -> dict:
    def pairs(items: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise TransferError("duplicate JSON field")
            result[key] = value
        return result

    if not 0 < len(raw) <= MAX_METADATA:
        raise TransferError("metadata size invalid")
    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise TransferError("expected JSON object")
    return value


def metadata_bytes(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or not 0 < info.st_size <= MAX_METADATA
        ):
            raise TransferError("unsafe metadata file")
        return stream.read(MAX_METADATA + 1)


def digest(value: object, length: int = 64) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is None
    ):
        raise TransferError("invalid digest")
    return value


def image_id(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise TransferError("invalid image ID")
    digest(value[7:])
    return value


def positive(value: object, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise TransferError("invalid numeric bound")
    return value


@dataclass(frozen=True, kw_only=True, slots=True)
class ReleaseBinding:
    version: str
    source_sha: str
    source_tree: str
    tag_object: str
    release_state: str
    workflow_sha: str
    workflow_ref: str
    run_id: str
    run_attempt: str
    actor: str
    delivery_mode: DeliveryMode
    repository: str = REPOSITORY
    environment: str = ENVIRONMENT
    target: str = TARGET

    def __post_init__(self) -> None:
        if (
            re.fullmatch(
                r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", self.version
            )
            is None
        ):
            raise TransferError("invalid canonical version")
        for value in (
            self.source_sha,
            self.source_tree,
            self.tag_object,
            self.release_state,
            self.workflow_sha,
        ):
            digest(value, 40)
        if self.source_sha != self.workflow_sha:
            raise TransferError("workflow and release source differ")
        if self.workflow_ref != (
            f"{REPOSITORY}/.github/workflows/private-verification.yml"
            f"@refs/tags/v{self.version}"
        ):
            raise TransferError("invalid workflow ref")
        if (self.repository, self.environment, self.target) != (
            REPOSITORY,
            ENVIRONMENT,
            TARGET,
        ):
            raise TransferError("unapproved destination")
        if not isinstance(self.delivery_mode, DeliveryMode):
            raise TransferError("invalid delivery mode")
        if not all(
            isinstance(v, str) and re.fullmatch(r"[1-9][0-9]*", v)
            for v in (self.run_id, self.run_attempt)
        ):
            raise TransferError("invalid run identity")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", self.actor):
            raise TransferError("invalid actor")

    @classmethod
    def parse(cls, value: dict) -> ReleaseBinding:
        if set(value) != set(cls.__dataclass_fields__):
            raise TransferError("incomplete release binding")
        data = dict(value)
        data["delivery_mode"] = DeliveryMode(data["delivery_mode"])
        return cls(**data)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json_bytes(asdict(self))).hexdigest()


@dataclass(frozen=True, kw_only=True, slots=True)
class FileDigest:
    sha256: str
    length: int

    def __post_init__(self) -> None:
        digest(self.sha256)
        positive(self.length, 2**40)

    @classmethod
    def of(cls, path: Path) -> FileDigest:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise TransferError("payload must be a regular unlinked file")
        with path.open("rb") as stream:
            hashed = hashlib.file_digest(stream, "sha256").hexdigest()
        return cls(sha256=hashed, length=info.st_size)


@dataclass(frozen=True, kw_only=True, slots=True)
class ImageBinding:
    variant: Literal["default", "aquacast"]
    raw_id: str
    config_digest: str
    id_kind: Literal["config", "oci-descriptor"]
    platform: Literal["linux/amd64"]
    store_mode: Literal["classic", "containerd"]

    def __post_init__(self) -> None:
        if (
            self.variant not in ("default", "aquacast")
            or self.platform != "linux/amd64"
        ):
            raise TransferError("invalid image variant/platform")
        image_id(self.raw_id)
        image_id(self.config_digest)
        if self.id_kind not in ("config", "oci-descriptor"):
            raise TransferError("unknown image ID kind")
        if self.id_kind != (
            "config" if self.raw_id == self.config_digest else "oci-descriptor"
        ):
            raise TransferError("image ID kind mismatch")
        if self.store_mode not in ("classic", "containerd"):
            raise TransferError("unsupported Docker store")


@dataclass(frozen=True, kw_only=True, slots=True)
class Envelope:
    binding: ReleaseBinding
    kind: PayloadKind
    files: dict[str, FileDigest]
    gates: dict[str, str]
    images: tuple[ImageBinding, ...]
    expanded_archive_bytes: int
    expanded_archive_sha256: str | None
    unpacked_layer_bytes: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or not isinstance(self.kind, PayloadKind)
        ):
            raise TransferError("unsupported envelope")
        if set(self.gates) != set(GATE_NAMES) or not set(self.gates.values()) <= {
            "passed",
            "failed",
            "blocked",
        }:
            raise TransferError("incomplete gate record")
        if self.kind == PayloadKind.DIAGNOSTIC:
            if (
                set(self.files) != {"report.bin"}
                or self.images
                or self.expanded_archive_bytes != 0
                or self.expanded_archive_sha256 is not None
                or self.unpacked_layer_bytes != 0
            ):
                raise TransferError("invalid diagnostic payload")
        else:
            if set(self.files) != {
                "report.bin",
                "receipt.json",
                "images.tar.gz",
                "runtime.tar",
            }:
                raise TransferError("incomplete release payload")
            if set(self.gates.values()) != {"passed"}:
                raise TransferError("release gates failed")
            if len(self.images) != 2 or {i.variant for i in self.images} != {
                "default",
                "aquacast",
            }:
                raise TransferError("both images required")
            if len({i.raw_id for i in self.images}) != 2:
                raise TransferError("image variants share identity")
            positive(self.expanded_archive_bytes, 2**40)
            digest(self.expanded_archive_sha256)
            positive(self.unpacked_layer_bytes, 2**40)
        if self.files["report.bin"].length > MAX_REPORT:
            raise TransferError("report exceeds existing bound")

    @classmethod
    def parse(cls, raw: bytes) -> Envelope:
        data = parse_json(raw)
        if set(data) != set(cls.__dataclass_fields__):
            raise TransferError("incomplete envelope")
        data["binding"] = ReleaseBinding.parse(data["binding"])
        data["kind"] = PayloadKind(data["kind"])
        data["files"] = {n: FileDigest(**v) for n, v in data["files"].items()}
        data["images"] = tuple(ImageBinding(**v) for v in data["images"])
        return cls(**data)

    def encode(self) -> bytes:
        return json_bytes(asdict(self))

    def owner_receipt(self, status: str) -> dict:
        allowed = (
            {"diagnostic-received"}
            if self.kind == PayloadKind.DIAGNOSTIC
            else {"produced", "delivered", "loaded"}
        )
        if status not in allowed or (
            status == "loaded"
            and self.binding.delivery_mode != DeliveryMode.LOAD_IMAGES
        ):
            raise TransferError("invalid receipt status")
        result = {
            "schema_version": 1,
            "status": status,
            "source": self.binding.source_sha,
            "tree": self.binding.source_tree,
            "tag_object": self.binding.tag_object,
            "release_state": self.binding.release_state,
            "run_id": self.binding.run_id,
            "run_attempt": self.binding.run_attempt,
            "envelope_sha256": hashlib.sha256(self.encode()).hexdigest(),
        }
        if self.kind == PayloadKind.RELEASE:
            result.update(
                receipt_sha256=self.files["receipt.json"].sha256,
                runtime_bundle_sha256=self.files["runtime.tar"].sha256,
                archive_sha256=self.files["images.tar.gz"].sha256,
                archive_bytes=self.files["images.tar.gz"].length,
                images={i.variant: i.raw_id for i in self.images},
            )
        return result


@dataclass(frozen=True, kw_only=True, slots=True)
class ReceiverConfig:
    binding: ReleaseBinding
    max_archive_bytes: int
    max_expanded_bytes: int
    max_bundle_bytes: int
    timeout_seconds: int
    reserve_bytes: int
    load_approval_binding_sha256: str | None = None
    load_approval_receipt_sha256: str | None = None

    def __post_init__(self) -> None:
        for value in (
            self.max_archive_bytes,
            self.max_expanded_bytes,
            self.reserve_bytes,
        ):
            positive(value, 2**40)
        positive(self.max_bundle_bytes, MAX_REPORT)
        positive(self.timeout_seconds, MAX_RECEIVER_SECONDS)
        if self.load_approval_binding_sha256 is not None:
            digest(self.load_approval_binding_sha256)
        if self.load_approval_receipt_sha256 is not None:
            digest(self.load_approval_receipt_sha256)

    @classmethod
    def parse(cls, raw: bytes) -> ReceiverConfig:
        data = parse_json(raw)
        data["binding"] = ReleaseBinding.parse(data["binding"])
        return cls(**data)

    def require_load(self, envelope: Envelope) -> None:
        if envelope.kind != PayloadKind.RELEASE or envelope.binding != self.binding:
            raise TransferError("load release binding mismatch")
        if self.load_approval_binding_sha256 != envelope.binding.sha256:
            raise TransferError("separate load approval missing")
        if (
            self.load_approval_receipt_sha256 is not None
            and self.load_approval_receipt_sha256
            != envelope.files["receipt.json"].sha256
        ):
            raise TransferError("load receipt approval mismatch")

    def admit(self, envelope: Envelope) -> None:
        if envelope.binding != self.binding:
            raise TransferError("receiver approval mismatch")
        if envelope.kind == PayloadKind.RELEASE:
            if (
                envelope.files["images.tar.gz"].length > self.max_archive_bytes
                or envelope.expanded_archive_bytes > self.max_expanded_bytes
                or envelope.unpacked_layer_bytes > self.max_expanded_bytes
            ):
                raise TransferError("archive exceeds admitted capacity")
            if (
                envelope.files["runtime.tar"].length > self.max_bundle_bytes
                or envelope.files["receipt.json"].length > MAX_METADATA
            ):
                raise TransferError("metadata exceeds admitted capacity")
            if self.binding.delivery_mode == DeliveryMode.LOAD_IMAGES:
                self.require_load(envelope)


def validate_runtime_bundle(path: Path) -> None:
    with tarfile.open(path, "r:") as archive:
        members = archive.getmembers()
        if len(members) != len(RUNTIME_FILES) or {m.name for m in members} != set(
            RUNTIME_FILES
        ):
            raise TransferError("runtime bundle allowlist mismatch")
        if any(not m.isfile() or m.size > MAX_METADATA for m in members):
            raise TransferError("unsafe runtime bundle member")


def verify_receipt(envelope: Envelope, path: Path) -> dict:
    receipt = parse_json(metadata_bytes(path))
    binding = envelope.binding
    expected = dict(
        package_version=binding.version,
        source_revision=binding.source_sha,
        git_tree=binding.source_tree,
        remote_tag="v" + binding.version,
        remote_tag_object=binding.tag_object,
        remote_tag_target=binding.source_sha,
        release_state=binding.release_state,
    )
    if set(receipt) != {*expected, "images"} or any(
        receipt[k] != v for k, v in expected.items()
    ):
        raise TransferError("release receipt binding mismatch")
    images = receipt["images"]
    if not isinstance(images, list) or len(images) != 2:
        raise TransferError("receipt image count mismatch")
    by_variant = {i["variant"]: i for i in images}
    if set(by_variant) != {"default", "aquacast"}:
        raise TransferError("receipt variant mismatch")
    for image in envelope.images:
        record = by_variant[image.variant]
        expected_tag = (
            "sapphire-flow-aquacast:"
            if image.variant == "aquacast"
            else "sapphire-flow:"
        ) + binding.version
        if (
            record["image_id"] != image.raw_id
            or record["mutable_tag"] != expected_tag
            or record["runtime_import_version"] != binding.version
        ):
            raise TransferError("receipt image identity mismatch")
        labels = record["labels"]
        if (
            labels.get("org.opencontainers.image.version") != binding.version
            or labels.get("org.opencontainers.image.revision") != binding.source_sha
        ):
            raise TransferError("receipt labels mismatch")
    return receipt


def expand_archive(compressed: Path, output: Path, expected_size: int) -> None:
    total = 0
    with gzip.open(compressed, "rb") as source, output.open("xb") as target:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > expected_size:
                raise TransferError("expanded archive exceeds bound")
            target.write(chunk)
        target.flush()
        os.fsync(target.fileno())
    if total != expected_size:
        raise TransferError("expanded archive size mismatch")


def verify_payload_files(envelope: Envelope, directory: Path) -> None:
    for name, expected in envelope.files.items():
        if FileDigest.of(directory / name) != expected:
            raise TransferError("payload digest mismatch")
    if envelope.kind == PayloadKind.RELEASE:
        verify_receipt(envelope, directory / "receipt.json")
        validate_runtime_bundle(directory / "runtime.tar")


def verify_payload(envelope: Envelope, directory: Path) -> None:
    verify_payload_files(envelope, directory)
    if envelope.kind == PayloadKind.RELEASE:
        expand_archive(
            directory / "images.tar.gz",
            directory / "images.tar",
            envelope.expanded_archive_bytes,
        )
        verify_expanded_images(envelope, directory)


def verify_sealed_payload(envelope: Envelope, directory: Path) -> None:
    if envelope.kind != PayloadKind.RELEASE:
        raise TransferError("sealed payload is not a release")
    verify_payload_files(envelope, directory)
    verify_expanded_images(envelope, directory)


def verify_expanded_images(envelope: Envelope, directory: Path) -> None:
    expanded = directory / "images.tar"
    if FileDigest.of(expanded) != FileDigest(
        sha256=envelope.expanded_archive_sha256, length=envelope.expanded_archive_bytes
    ):
        raise TransferError("expanded archive digest mismatch")
    with tarfile.open(expanded, "r:") as exported:
        manifest_file = exported.extractfile("manifest.json")
        if manifest_file is None:
            raise TransferError("missing image archive manifest")
        manifest = json.loads(manifest_file.read(MAX_METADATA + 1))
        if (
            not isinstance(manifest, list)
            or len(manifest) != 2
            or any(
                not isinstance(entry, dict) or entry.get("RepoTags") not in (None, [])
                for entry in manifest
            )
        ):
            raise TransferError("archive must contain exactly two untagged images")
    archive = tool("image_archive")
    if (
        archive.unpacked_layer_bytes(expanded, envelope.unpacked_layer_bytes)
        != envelope.unpacked_layer_bytes
    ):
        raise TransferError("unpacked image layer size mismatch")
    for image in envelope.images:
        observed = archive.describe_image_archive(expanded, image.raw_id)
        if (
            observed.config_digest != image.config_digest
            or observed.platform != image.platform
        ):
            raise TransferError("archive image mapping mismatch")
        labels = observed.labels
        if (
            labels.get("org.opencontainers.image.version") != envelope.binding.version
            or labels.get("org.opencontainers.image.revision")
            != envelope.binding.source_sha
        ):
            raise TransferError("archive source labels mismatch")


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_once(path: Path, payload: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())


def receive_stream(
    config: ReceiverConfig,
    root: Path,
    input_fd: int,
    *,
    clock: Callable[[], float] = time.monotonic,
    verify: Callable[[Envelope, Path], None] = verify_payload,
    load: Callable[[Envelope, Path], dict] | None = None,
) -> dict:
    receiver = tool("receive_build_report")
    stream = receiver.InputStream(
        fd=input_fd,
        deadline=clock() + config.timeout_seconds,
        clock=clock,
        wait=receiver.wait_ready,
    )
    original_blocking = os.get_blocking(input_fd)
    lock_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.set_blocking(input_fd, False)
        size = positive(int.from_bytes(stream.exact(8), "big"), MAX_METADATA)
        envelope = Envelope.parse(stream.exact(size))
        config.admit(envelope)
        encoded = envelope.encode()
        accepted = root / (
            f"diagnostic-{envelope.binding.run_id}-{envelope.binding.run_attempt}"
            if envelope.kind == PayloadKind.DIAGNOSTIC
            else "v" + envelope.binding.version
        )
        if (accepted.exists() or accepted.is_symlink()) and (
            accepted.is_symlink()
            or not accepted.is_dir()
            or metadata_bytes(accepted / "envelope.json") != encoded
        ):
            raise TransferError("accepted release conflict")
        required = (
            sum(f.length for f in envelope.files.values())
            + envelope.expanded_archive_bytes
            + config.reserve_bytes
        )
        if shutil.disk_usage(root).free < required:
            raise TransferError("insufficient receiver disk")
        incoming = (
            root / f".{envelope.binding.run_id}-{envelope.binding.run_attempt}.partial"
        )
        incoming.mkdir(mode=0o700)
        # Interrupted attempts remain named partials; never silently overwrite them.
        for name, expected in sorted(envelope.files.items()):
            remaining = expected.length
            hashed = hashlib.sha256()
            fd = os.open(
                incoming / name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(fd, "wb") as output:
                while remaining:
                    chunk = stream.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise TransferError("truncated release payload")
                    remaining -= len(chunk)
                    hashed.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if hashed.hexdigest() != expected.sha256:
                raise TransferError("release wire digest mismatch")
        if stream.read(1):
            raise TransferError("trailing release bytes")
        stream.check_deadline()
        verify(envelope, incoming)
        stream.check_deadline()
        encoded = envelope.encode()
        write_once(incoming / "envelope.json", encoded)
        status = (
            "diagnostic-received"
            if envelope.kind == PayloadKind.DIAGNOSTIC
            else "delivered"
        )
        sync_directory(incoming)
        if accepted.exists() or accepted.is_symlink():
            if (
                accepted.is_symlink()
                or not accepted.is_dir()
                or metadata_bytes(accepted / "envelope.json") != encoded
            ):
                raise TransferError("accepted release conflict")
            for name, expected in envelope.files.items():
                if FileDigest.of(accepted / name) != expected:
                    raise TransferError("accepted payload changed")
            shutil.rmtree(incoming)  # Only this invocation's freshly created files.
        else:
            os.rename(incoming, accepted)  # Serialized under the private store lock.
            sync_directory(root)
        if envelope.kind == PayloadKind.RELEASE:
            delivered = json_bytes(envelope.owner_receipt("delivered"))
            delivered_path = accepted / "delivered.json"
            if delivered_path.exists():
                if metadata_bytes(delivered_path) != delivered:
                    raise TransferError("delivery ledger conflict")
            else:
                write_once(delivered_path, delivered)
                sync_directory(accepted)
        if (
            envelope.kind == PayloadKind.RELEASE
            and envelope.binding.delivery_mode == DeliveryMode.LOAD_IMAGES
        ):
            if load is None:
                raise TransferError("image loader unavailable")
            proof = json_bytes(load(envelope, accepted))
            proof_path = accepted / "loaded-image-proof.json"
            if proof_path.exists():
                if metadata_bytes(proof_path) != proof:
                    raise TransferError("loaded identity proof conflict")
            else:
                write_once(proof_path, proof)
            status = "loaded"
        result = envelope.owner_receipt(status)
        ledger = accepted / (status + ".json")
        if ledger.exists():
            if metadata_bytes(ledger) != json_bytes(result):
                raise TransferError("acceptance ledger conflict")
        else:
            write_once(ledger, json_bytes(result))
            sync_directory(accepted)
        return result
    finally:
        os.set_blocking(input_fd, original_blocking)
        os.close(lock_fd)


def docker(
    args: list[str], *, stdin: str | None = None, deadline: float | None = None
) -> str:
    budget = 600.0 if deadline is None else min(600.0, deadline - time.monotonic())
    if budget <= 0:
        raise TransferError("receiver Docker deadline exceeded")
    result = subprocess.run(
        ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock", *args],
        input=stdin,
        text=True,
        capture_output=True,
        timeout=budget,
        check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": "/root", "LC_ALL": "C"},
    )
    if result.returncode:
        raise TransferError("target Docker operation failed")
    return result.stdout.strip()


def docker_store_mode(driver: str, raw_status: str) -> Literal["classic", "containerd"]:
    status = json.loads(raw_status)
    if status is not None and not isinstance(status, list):
        raise TransferError("invalid Docker store metadata")
    containerd = ["driver-type", "io.containerd.snapshotter.v1"] in (status or [])
    if containerd and driver in ("overlayfs", "overlay2"):
        return "containerd"
    if not containerd and driver == "overlay2":
        return "classic"
    raise TransferError("unsupported Docker store")


def target_image_id(
    image: ImageBinding, store_mode: Literal["classic", "containerd"]
) -> str:
    if store_mode == "classic":
        return image.config_digest
    if image.id_kind != "oci-descriptor":
        raise TransferError("config-only image unsupported on containerd target")
    return image.raw_id


def require_top_level_image_ids(envelope: Envelope, directory: Path) -> None:
    with tarfile.open(directory / "images.tar", "r:") as archive:
        indexes = []
        for count, member in enumerate(archive, start=1):
            if count > 100_000:
                raise TransferError("too many image archive members")
            if posixpath.normpath(member.name).lstrip("/") == "index.json":
                if member.name != "index.json":
                    raise TransferError("noncanonical image index name")
                indexes.append(member)
        if (
            len(indexes) != 1
            or not indexes[0].isfile()
            or not 0 < indexes[0].size <= MAX_METADATA
        ):
            raise TransferError("invalid top-level image index")
        stream = archive.extractfile(indexes[0])
        if stream is None:
            raise TransferError("missing top-level image index")
        index = parse_json(stream.read(MAX_METADATA + 1))
    manifests = index.get("manifests")
    if not isinstance(manifests, list) or len(manifests) != len(envelope.images):
        raise TransferError("top-level image descriptor count mismatch")
    ids = [item.get("digest") for item in manifests if isinstance(item, dict)]
    if len(ids) != len(manifests) or any(not isinstance(item, str) for item in ids):
        raise TransferError("invalid top-level image descriptor")
    if len(set(ids)) != len(ids) or set(ids) != {i.raw_id for i in envelope.images}:
        raise TransferError("raw image IDs must match top-level descriptors")


def require_target_tag(tag: str, expected_id: str, *, run: Callable[..., str]) -> None:
    existing = run(
        [
            "image",
            "ls",
            "--filter",
            "reference=" + tag,
            "--format",
            "{{.Repository}}:{{.Tag}}",
        ]
    ).split()
    if existing not in ([], [tag]):
        raise TransferError("unexpected target tag listing")
    if (
        tag in existing
        and run(["image", "inspect", "--format", "{{.Id}}", tag]) != expected_id
    ):
        raise TransferError("target tag conflict")


def load_images(
    envelope: Envelope,
    directory: Path,
    *,
    approval: ReceiverConfig,
    run: Callable[..., str] = docker,
) -> dict:
    approval.require_load(envelope)
    if run(["info", "--format", "{{.Architecture}}"]) not in ("x86_64", "amd64"):
        raise TransferError("target is not native amd64")
    docker_root = Path(run(["info", "--format", "{{.DockerRootDir}}"]))
    if not docker_root.is_absolute() or not docker_root.is_dir():
        raise TransferError("Docker storage location unavailable")
    required = (
        envelope.unpacked_layer_bytes
        + envelope.expanded_archive_bytes
        + approval.reserve_bytes
    )
    if shutil.disk_usage(docker_root).free < required:
        raise TransferError("insufficient target Docker capacity")
    if FileDigest.of(directory / "images.tar") != FileDigest(
        sha256=envelope.expanded_archive_sha256, length=envelope.expanded_archive_bytes
    ):
        raise TransferError("accepted expanded archive changed")
    store_mode = docker_store_mode(
        run(["info", "--format", "{{.Driver}}"]),
        run(["info", "--format", "{{json .DriverStatus}}"]),
    )
    selected_ids = {i.variant: target_image_id(i, store_mode) for i in envelope.images}
    if store_mode == "containerd":
        require_top_level_image_ids(envelope, directory)
    receipt = verify_receipt(envelope, directory / "receipt.json")
    records = {i["variant"]: i for i in receipt["images"]}
    for image in envelope.images:
        require_target_tag(
            records[image.variant]["mutable_tag"], selected_ids[image.variant], run=run
        )
    # Save-by-ID archives must not introduce a mutable tag during load.
    with tarfile.open(directory / "images.tar", "r:") as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
        if any(entry.get("RepoTags") for entry in manifest):
            raise TransferError("archive contains mutable tags")
    run(["image", "load", "--input", str(directory / "images.tar")])
    probe = tool("image_identity_probe")

    class Runner:
        def __init__(self) -> None:
            self.sequence = 0
            self.expected_image_id: str | None = None

        def run(self, args: list[str], *, stdin: str | None = None) -> str:
            if args[0] != "run":
                return run(args, stdin=stdin)
            identity_prefix = [
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "-i",
                "--user",
                "app",
                "--entrypoint",
                "/app/.venv/bin/python",
                "-e",
                "EXPECTED_VERSION=" + envelope.binding.version,
                "-e",
                "EXPECTED_REVISION=" + envelope.binding.source_sha,
            ]
            entrypoint_prefix = [
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "-e",
                "DB_PASSWORD=ci-smoke-only",
            ]
            prefixes = (identity_prefix, entrypoint_prefix)
            prefix = next((p for p in prefixes if args[: len(p)] == p), None)
            if prefix is None or len(args) <= len(prefix):
                raise TransferError("unsupported native probe arguments")
            if (
                self.expected_image_id is None
                or args[len(prefix)] != self.expected_image_id
            ):
                raise TransferError("native probe image identity changed")
            self.sequence += 1
            name = (
                f"sapphire-release-probe-{envelope.binding.run_id}-"
                f"{envelope.binding.run_attempt}-{self.sequence}"
            )
            query = [
                "container",
                "ls",
                "-a",
                "--filter",
                f"name=^/{name}$",
                "--format",
                "{{.Names}}",
            ]
            if run(query):
                raise TransferError("probe container name already exists")
            try:
                return run(
                    [
                        "run",
                        "--name",
                        name,
                        "--label",
                        "sapphire.release.binding=" + envelope.binding.sha256,
                        *args[1:],
                    ],
                    stdin=stdin,
                )
            finally:
                remaining = run(query)
                if remaining:
                    if (
                        remaining != name
                        or run(
                            [
                                "container",
                                "inspect",
                                "--format",
                                '{{index .Config.Labels "sapphire.release.binding"}}',
                                name,
                            ]
                        )
                        != envelope.binding.sha256
                    ):
                        raise TransferError("probe cleanup ownership mismatch")
                    run(["container", "rm", "-f", name])
                    if run(query):
                        raise TransferError("probe cleanup incomplete")

    runner = Runner()
    loaded = []
    for image in envelope.images:
        selected_id = selected_ids[image.variant]
        inspected = json.loads(run(["image", "inspect", selected_id]))[0]
        if inspected.get("Os") != "linux" or inspected.get("Architecture") != "amd64":
            raise TransferError("loaded platform mismatch")
        loaded_id = image_id(inspected["Id"])
        if loaded_id != selected_id:
            raise TransferError("unsupported target ID mapping")
        loaded.append(
            {
                "variant": image.variant,
                "source_raw_id": image.raw_id,
                "loaded_raw_id": loaded_id,
                "config_digest": image.config_digest,
            }
        )
        runner.expected_image_id = selected_id
        probed = probe.probe_image(
            loaded_id,
            envelope.binding.version,
            envelope.binding.source_sha,
            variant=image.variant,
            runner=runner,
        )
        if probed.image_id != selected_id:
            raise TransferError("native probe image identity changed")
        imports = "import sys, sapphire_flow, prefect" + (
            ", aquacast, torch" if image.variant == "aquacast" else ""
        )
        runner.run(
            [
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "-e",
                "DB_PASSWORD=ci-smoke-only",
                loaded_id,
                "python",
                "-I",
                "-c",
                imports
                + "; from pathlib import Path; assert sys.prefix == '/app/.venv'; "
                "assert 'include-system-site-packages = false' "
                "in Path('/app/.venv/pyvenv.cfg').read_text().lower()",
            ]
        )
        tag = records[image.variant]["mutable_tag"]
        # Docker has no compare-and-swap tag operation; exclude external tag writers.
        require_target_tag(tag, selected_id, run=run)
        run(["image", "tag", loaded_id, tag])
        if run(["image", "inspect", "--format", "{{.Id}}", tag]) != inspected["Id"]:
            raise TransferError("loaded tag identity changed")
    return {
        "binding_sha256": envelope.binding.sha256,
        "target_store_mode": store_mode,
        "images": loaded,
        "native_image_proofs": "passed",
        "runtime_activation": "held",
    }


def load_accepted(
    config: ReceiverConfig, root: Path, *, load: Callable[..., dict] = load_images
) -> dict:
    lock_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        accepted = root / ("v" + config.binding.version)
        info = accepted.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise TransferError("unsafe accepted release directory")
        envelope = Envelope.parse(metadata_bytes(accepted / "envelope.json"))
        config.admit(envelope)
        config.require_load(envelope)
        if config.load_approval_receipt_sha256 != envelope.files["receipt.json"].sha256:
            raise TransferError("accepted receipt requires separate load approval")
        # Reuse the exact validators without decompression or any accepted-file writes.
        verify_sealed_payload(envelope, accepted)
        expected_delivery = json_bytes(envelope.owner_receipt("delivered"))
        if metadata_bytes(accepted / "delivered.json") != expected_delivery:
            raise TransferError("accepted delivery ledger mismatch")
        proof = load(envelope, accepted, approval=config)
        result = {
            **envelope.owner_receipt("delivered"),
            "status": "accepted-images-loaded",
        }
        operation = root / ("load-v" + config.binding.version + ".json")
        record = json_bytes({"owner_receipt": result, "proof": proof})
        if operation.exists():
            if metadata_bytes(operation) != record:
                raise TransferError("accepted load record conflict")
        else:
            write_once(operation, record)
            sync_directory(root)
        return result
    finally:
        os.close(lock_fd)


def sender_timeout(value: int) -> int:
    positive(value, MAX_SENDER_SECONDS)
    if value <= MAX_RECEIVER_SECONDS + ACK_MARGIN_SECONDS:
        raise TransferError(
            "sender timeout must exceed receiver cap and acknowledgement margin"
        )
    return value


def remaining_ssh_budget(requested: int, deadline: float, now: float) -> int:
    available = min(
        sender_timeout(requested), int(deadline - now) - TRANSFER_CLEANUP_SECONDS
    )
    if available <= MAX_RECEIVER_SECONDS + ACK_MARGIN_SECONDS:
        raise TransferError("insufficient transfer-step budget before SSH")
    return available


def send(
    envelope: Envelope,
    directory: Path,
    work: Path,
    env: Mapping[str, str],
    *,
    timeout_seconds: int,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    sender_timeout(timeout_seconds)
    if deadline is None:
        deadline = clock() + TRANSFER_WORK_SECONDS
    reserve = positive(int(env["STAGING_RESERVE_BYTES"]), 2**40)
    if (
        shutil.disk_usage(work).free
        < sum(f.length for f in envelope.files.values()) + MAX_METADATA + reserve
    ):
        raise TransferError("insufficient transfer staging capacity")
    key, hosts = work / "staging-key", work / "staging-known-hosts"
    created: list[Path] = []
    process = None
    try:
        for path, value in (
            (key, env["STAGING_ADMIN_SSH_KEY"]),
            (hosts, env["STAGING_KNOWN_HOSTS"]),
        ):
            if not value.strip():
                raise TransferError("missing transfer credential/trust")
            with path.open("x") as output:
                created.append(path)
                path.chmod(0o600)
                output.write(value + "\n")
        command = [
            "ssh",
            "-F",
            "/dev/null",
            "-T",
            "-i",
            str(key),
            "-oBatchMode=yes",
            "-oIdentitiesOnly=yes",
            "-oStrictHostKeyChecking=yes",
            "-oUserKnownHostsFile=" + str(hosts),
            "-oGlobalKnownHostsFile=/dev/null",
            "-oClearAllForwardings=yes",
            "-oPermitLocalCommand=no",
            "-oProxyCommand=none",
            "-oProxyJump=none",
            "-oIdentityAgent=none",
            "-oPasswordAuthentication=no",
            "-oKbdInteractiveAuthentication=no",
            "-oConnectTimeout=10",
            "-oServerAliveInterval=10",
            "-oServerAliveCountMax=2",
            TARGET,
            REMOTE_COMMAND,
        ]
        # File-backed stdin bounds memory and avoids a blocking pipe writer.
        wire = work / "transfer.wire"
        with wire.open("xb") as output:
            metadata = envelope.encode()
            output.write(len(metadata).to_bytes(8, "big"))
            output.write(metadata)
            for name, expected in sorted(envelope.files.items()):
                if FileDigest.of(directory / name) != expected:
                    raise TransferError("payload changed before transfer")
                with (directory / name).open("rb") as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
        # Hashing and wire creation have already consumed the same step budget.
        ssh_budget = remaining_ssh_budget(timeout_seconds, deadline, clock())
        with wire.open("rb") as source:
            process = subprocess.run(
                command,
                stdin=source,
                capture_output=True,
                timeout=ssh_budget,
                check=False,
                env={
                    "PATH": env.get("PATH", os.defpath),
                    "HOME": str(work),
                    "LC_ALL": "C",
                },
            )
        status = (
            "diagnostic-received"
            if envelope.kind == PayloadKind.DIAGNOSTIC
            else (
                "loaded"
                if envelope.binding.delivery_mode == DeliveryMode.LOAD_IMAGES
                else "delivered"
            )
        )
        write_once(work / "ssh.stdout", process.stdout)
        write_once(work / "ssh.stderr", process.stderr)
        expected_ack = json_bytes(envelope.owner_receipt(status))
        if process.returncode or process.stdout != expected_ack:
            raise TransferError("staging transfer not acknowledged")
        return envelope.owner_receipt(status)
    finally:
        for path in created:
            path.unlink(missing_ok=True)


def main() -> int:
    try:
        if sys.argv[1:] not in (["receive"], ["load-accepted"]) or os.geteuid() != 0:
            raise TransferError("unsupported receiver invocation")
        receiver = tool("receive_build_report")
        parent, name = receiver.open_parent(str(CONFIG_PATH), trusted_owner_uid=0)
        try:
            fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != 0
                    or stat.S_IMODE(info.st_mode) != 0o600
                ):
                    raise TransferError("untrusted receiver config")
                config = ReceiverConfig.parse(stream.read(MAX_METADATA + 1))
        finally:
            os.close(parent)
        parent, name = receiver.open_parent(str(STORE_ROOT), trusted_owner_uid=0)
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise TransferError("untrusted receiver store")
        finally:
            os.close(parent)
        os.umask(0o077)

        hard_deadline = time.monotonic() + config.timeout_seconds

        def expired(signum: int, frame: object) -> None:
            signal.setitimer(
                signal.ITIMER_REAL, max(0.001, hard_deadline - time.monotonic())
            )
            raise TransferError("receiver deadline exceeded")

        def bounded_docker(args: list[str], *, stdin: str | None = None) -> str:
            return docker(args, stdin=stdin, deadline=hard_deadline)

        def approved_load(
            envelope: Envelope, path: Path, *, approval: ReceiverConfig
        ) -> dict:
            return load_images(envelope, path, approval=approval, run=bounded_docker)

        signal.signal(signal.SIGALRM, expired)
        signal.alarm(max(1, config.timeout_seconds - RECEIVER_CLEANUP_SECONDS))
        try:
            if sys.argv[1] == "load-accepted":
                result = load_accepted(config, STORE_ROOT, load=approved_load)
            else:
                result = receive_stream(
                    config,
                    STORE_ROOT,
                    0,
                    load=lambda envelope, path: approved_load(
                        envelope, path, approval=config
                    ),
                )
        finally:
            signal.alarm(0)
        os.write(1, json_bytes(result))
        return 0
    except (Exception, KeyboardInterrupt):
        with suppress(OSError):
            os.write(1, b"STAGING_RELEASE_REJECTED\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
