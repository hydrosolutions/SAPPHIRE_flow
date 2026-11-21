"""Private retained state and credential-change checks for fresh installations."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

try:
    from tools.deployment_rehearsal import durable_json, private_directory
except ModuleNotFoundError:
    from deployment_rehearsal import durable_json, private_directory

ROLES = (
    "db_password",
    "sapphire_api_db_password",
    "sapphire_worker_db_password",
    "sapphire_backup_db_password",
    "access_token_pepper",
)
LOCK = Path("/run/lock/sapphire-flow-install.lock")
SERVICES = (
    "postgres",
    "prefect-server",
    "init",
    "api",
    "caddy",
    "prefect-worker",
    "prefect-worker-ingest",
    "prefect-worker-backup",
)
VOLUMES = (
    "pgdata",
    "prefect_data",
    "caddy_data",
    "caddy_config",
    "model_artifacts",
    "backups",
    "nwp_grids",
    "bafu_forecast_archive",
    "bafu_observation_archive",
)
ResourceKind = Literal["container", "network", "volume"]


class InstallError(RuntimeError):
    pass


@contextlib.contextmanager
def installation_lock(path: Path = LOCK) -> Iterator[None]:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
        ):
            raise InstallError("lock_permissions_invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError("installation_busy") from None
        yield
    finally:
        os.close(descriptor)


def private_json(path: Path) -> Any:
    private_directory(path.parent)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or metadata.st_size > 1024 * 1024
        ):
            raise InstallError("private_record_invalid")
        return json.loads(stream.read(1024 * 1024 + 1))


@dataclass(frozen=True, kw_only=True, slots=True)
class Delivery:
    name: str
    path: Path
    effective: bytes = field(repr=False)
    raw_digest: str = field(repr=False)
    metadata: tuple[int, ...]


def read_deliveries(bindings: Mapping[str, Path]) -> tuple[Delivery, ...]:
    if set(bindings) != set(ROLES):
        raise InstallError("credential_bindings_invalid")
    deliveries: list[Delivery] = []
    for name in ROLES:
        path = bindings[name]
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > 4096:
                raise InstallError("credential_format_invalid")
            raw = stream.read(4097)
            after = os.fstat(stream.fileno())
        metadata = (
            before.st_dev,
            before.st_ino,
            before.st_uid,
            before.st_gid,
            before.st_mode,
            before.st_mtime_ns,
            before.st_size,
        )
        if metadata != (
            after.st_dev,
            after.st_ino,
            after.st_uid,
            after.st_gid,
            after.st_mode,
            after.st_mtime_ns,
            after.st_size,
        ):
            raise InstallError("credential_delivery_changed")
        if name == "access_token_pepper":
            try:
                effective = raw.decode().strip().encode()
            except UnicodeError:
                raise InstallError("credential_format_invalid") from None
        else:
            if not re.fullmatch(rb"[A-Za-z0-9._~-]+\n?", raw):
                raise InstallError("credential_format_invalid")
            effective = raw.removesuffix(b"\n")
        if not effective:
            raise InstallError("credential_format_invalid")
        deliveries.append(
            Delivery(
                name=name,
                path=path,
                effective=effective,
                raw_digest=hashlib.sha256(raw).hexdigest(),
                metadata=metadata,
            )
        )
    passwords = [d.effective for d in deliveries if d.name != "access_token_pepper"]
    if len(set(passwords)) != 4:
        raise InstallError("database_credentials_not_distinct")
    return tuple(deliveries)


def unchanged(deliveries: tuple[Delivery, ...]) -> None:
    current = read_deliveries({d.name: d.path for d in deliveries})
    for before, after in zip(deliveries, current, strict=True):
        if (before.name, before.path, before.effective, before.metadata[2:5]) != (
            after.name,
            after.path,
            after.effective,
            after.metadata[2:5],
        ):
            raise InstallError("credential_delivery_changed")


def save_fingerprints(
    directory: Path,
    deliveries: tuple[Delivery, ...],
    random_bytes: Callable[[int], bytes],
) -> None:
    entries: list[dict[str, str]] = []
    for delivery in deliveries:
        salt = random_bytes(32)
        if len(salt) != 32:
            raise InstallError("random_source_invalid")
        entries.append(
            {
                "name": delivery.name,
                "path": str(delivery.path),
                "salt": salt.hex(),
                "digest": hashlib.sha256(salt + delivery.effective).hexdigest(),
            }
        )
    durable_json(
        directory / "credentials.json", {"schema_version": 1, "credentials": entries}
    )


def check_fingerprints(directory: Path) -> tuple[Delivery, ...]:
    try:
        raw = private_json(directory / "credentials.json")
        if (
            set(raw) != {"schema_version", "credentials"}
            or raw["schema_version"] != 1
            or len(raw["credentials"]) != 5
        ):
            raise InstallError("credential_record_invalid")
        entries = {item["name"]: item for item in raw["credentials"]}
        if set(entries) != set(ROLES):
            raise InstallError("credential_record_invalid")
        for item in entries.values():
            if (
                set(item) != {"name", "path", "salt", "digest"}
                or not Path(item["path"]).is_absolute()
                or not all(
                    re.fullmatch(r"[0-9a-f]{64}", item[key])
                    for key in ("salt", "digest")
                )
            ):
                raise InstallError("credential_record_invalid")
        try:
            deliveries = read_deliveries(
                {name: Path(item["path"]) for name, item in entries.items()}
            )
        except OSError:
            raise InstallError("credential_delivery_unavailable") from None
        for delivery in deliveries:
            entry = entries[delivery.name]
            actual = hashlib.sha256(
                bytes.fromhex(entry["salt"]) + delivery.effective
            ).hexdigest()
            if not hmac.compare_digest(actual, entry["digest"]):
                raise InstallError("credential_changed")
        return deliveries
    except (OSError, ValueError, TypeError, KeyError):
        raise InstallError("credential_record_unavailable") from None


@dataclass(frozen=True, kw_only=True, slots=True)
class InstallationRecord:
    installation_id: str
    project: str
    daemon: str
    http_port: int
    revision: str
    identity_kind: Literal["release-target", "ci-development"]
    state_directory: str
    phase: str = "intent"
    stage: str = "preflight"
    containment: Literal["incomplete", "stopped_data_retained"] | None = None
    known: tuple[tuple[ResourceKind, str], ...] = ()
    recipe_digest: str = ""
    inputs: tuple[tuple[str, str, int], ...] = ()
    init_identity: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[0-9a-f]{32}", self.installation_id)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", self.project)
            or not re.fullmatch(r"[A-Za-z0-9:._-]{1,256}", self.daemon)
            or not re.fullmatch(r"[0-9a-f]{40}", self.revision)
            or not 1 <= self.http_port <= 65535
            or not Path(self.state_directory).is_absolute()
        ):
            raise InstallError("installation_record_invalid")

    def names(self, kind: ResourceKind) -> tuple[str, ...]:
        if kind == "container":
            return tuple(self.project + "-" + name + "-1" for name in SERVICES)
        return tuple(
            self.project + "_" + name
            for name in (VOLUMES if kind == "volume" else ("backend", "frontend"))
        )


def save_record(directory: Path, record: InstallationRecord) -> None:
    durable_json(
        directory / "installation.json", {"schema_version": 1, **asdict(record)}
    )


def load_record(directory: Path) -> InstallationRecord:
    try:
        raw = private_json(directory / "installation.json")
        if raw.pop("schema_version") != 1:
            raise InstallError("installation_record_invalid")
        raw["known"] = tuple(tuple(item) for item in raw["known"])
        raw["inputs"] = tuple(tuple(item) for item in raw["inputs"])
        raw["init_identity"] = tuple(raw["init_identity"])
        record = InstallationRecord(**raw)
        if Path(record.state_directory) != directory.resolve():
            raise InstallError("installation_state_path_changed")
        if len(record.known) > 32 or any(
            kind not in {"container", "network", "volume"}
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", identifier)
            for kind, identifier in record.known
        ):
            raise InstallError("installation_record_invalid")
        if record.identity_kind not in {"release-target", "ci-development"}:
            raise InstallError("installation_record_invalid")
        return record
    except (OSError, ValueError, TypeError, KeyError):
        raise InstallError("installation_record_unavailable") from None


def load_recipe(directory: Path, record: InstallationRecord) -> dict[str, Any]:
    recipe = private_json(directory / "recipe.json")
    if (
        hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
        != record.recipe_digest
    ):
        raise InstallError("frozen_recipe_changed")
    for alias, digest, mode in record.inputs:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", alias):
            raise InstallError("installation_record_invalid")
        path = directory / "inputs" / alias
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > 8 * 1024 * 1024
            or path.stat().st_mode & 0o777 != mode
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise InstallError("frozen_input_changed")
    return recipe
