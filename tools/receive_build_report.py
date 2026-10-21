from __future__ import annotations

import errno
import fcntl
import json
import os
import selectors
import signal
import stat
import sys
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

CONFIG_PATH = "/etc/sapphire-flow/build-report-upload.json"
COMMAND = "upload-build-report"
MAX_CONFIG_BYTES = 4096
MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_SECONDS = 120
CHUNK_BYTES = 64 * 1024
PARTIAL = "report.partial"
FINAL = "report.bin"
INTERRUPT_SIGNALS = frozenset((signal.SIGHUP, signal.SIGINT, signal.SIGTERM))
_interrupt_raised = False
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class ReportRejectedError(ValueError):
    pass


class ReportInterruptedError(Exception):
    pass


class ReportStatus(Enum):
    RECEIVED = (0, b"REPORT_RECEIVED\n")
    REJECTED = (1, b"REPORT_REJECTED\n")
    BUSY = (2, b"REPORT_BUSY\n")


@dataclass(frozen=True, kw_only=True, slots=True)
class UploadConfig:
    inbox: str
    upload_uid: int
    max_bytes: int
    timeout_seconds: int

    def __post_init__(self) -> None:
        path_components(self.inbox)
        for value, limit in (
            (self.upload_uid, 2**32 - 2),
            (self.max_bytes, MAX_REPORT_BYTES),
            (self.timeout_seconds, MAX_SECONDS),
        ):
            if type(value) is not int or not 1 <= value <= limit:
                raise ReportRejectedError("invalid configured bound")


def path_components(path: object) -> list[str]:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ReportRejectedError("path must be canonical and absolute")
    parts = path[1:].split("/")
    if any(part in ("", ".", "..") or "\x00" in part for part in parts):
        raise ReportRejectedError("path must be canonical and absolute")
    return parts


def _trusted_directory(fd: int, trusted_owner_uid: int) -> None:
    info = os.fstat(fd)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in (0, trusted_owner_uid)
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise ReportRejectedError("untrusted ancestor")


def open_parent(path: str, *, trusted_owner_uid: int) -> tuple[int, str]:
    parts = path_components(path)
    fd = os.open("/", _DIRECTORY_FLAGS)
    try:
        _trusted_directory(fd, trusted_owner_uid)
        for part in parts[:-1]:
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
            _trusted_directory(fd, trusted_owner_uid)
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReportRejectedError("duplicate config key")
        result[key] = value
    return result


def parse_config(raw: bytes) -> UploadConfig:
    if len(raw) > MAX_CONFIG_BYTES:
        raise ReportRejectedError("config too large")
    raw_data: object = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(raw_data, dict):
        raise ReportRejectedError("invalid config fields")
    data = cast("dict[str, object]", raw_data)
    if set(data) != {"inbox", "upload_uid", "max_bytes", "timeout_seconds"}:
        raise ReportRejectedError("invalid config fields")
    inbox = data["inbox"]
    if not isinstance(inbox, str):
        raise ReportRejectedError("invalid inbox path")
    return UploadConfig(
        inbox=inbox,
        upload_uid=_config_integer(data["upload_uid"]),
        max_bytes=_config_integer(data["max_bytes"]),
        timeout_seconds=_config_integer(data["timeout_seconds"]),
    )


def _config_integer(value: object) -> int:
    if type(value) is not int:
        raise ReportRejectedError("invalid configured integer")
    return value


def load_config(path: str, *, trusted_owner_uid: int) -> UploadConfig:
    parent, name = open_parent(path, trusted_owner_uid=trusted_owner_uid)
    fd = -1
    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=parent,
        )
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != trusted_owner_uid
            or stat.S_IMODE(info.st_mode) & 0o022
            or info.st_nlink != 1
            or info.st_size > MAX_CONFIG_BYTES
        ):
            raise ReportRejectedError("untrusted config")
        chunks = bytearray()
        while len(chunks) <= MAX_CONFIG_BYTES:
            chunk = os.read(fd, MAX_CONFIG_BYTES + 1 - len(chunks))
            if not chunk:
                return parse_config(bytes(chunks))
            chunks.extend(chunk)
        raise ReportRejectedError("config too large")
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(parent)


def wait_ready(fd: int, event: int, seconds: float) -> bool:
    with selectors.DefaultSelector() as selector:
        selector.register(fd, event)
        return bool(selector.select(seconds))


@dataclass(frozen=True, kw_only=True, slots=True)
class InputStream:
    fd: int
    deadline: float
    clock: Callable[[], float]
    wait: Callable[[int, int, float], bool]

    def check_deadline(self) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise ReportRejectedError("upload deadline exceeded")
        return remaining

    def read(self, size: int) -> bytes:
        while True:
            remaining = self.check_deadline()
            if not self.wait(self.fd, selectors.EVENT_READ, remaining):
                raise ReportRejectedError("upload deadline exceeded")
            self.check_deadline()
            try:
                return os.read(self.fd, size)
            except BlockingIOError:
                continue

    def exact(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            chunk = self.read(size - len(result))
            if not chunk:
                raise ReportRejectedError("truncated report")
            result.extend(chunk)
        return bytes(result)


def _exists(directory: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _write_all(fd: int, data: bytes, stream: InputStream) -> None:
    remaining = memoryview(data)
    while remaining:
        stream.check_deadline()
        written = os.write(fd, remaining)
        if written <= 0:
            raise ReportRejectedError("report write failed")
        remaining = remaining[written:]


@contextmanager
def _masked_interrupts() -> Iterator[None]:
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, INTERRUPT_SIGNALS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def _receive_locked(directory: int, config: UploadConfig, stream: InputStream) -> None:
    if _exists(directory, FINAL) or _exists(directory, PARTIAL):
        raise ReportRejectedError("inbox requires operator clearance")
    length = int.from_bytes(stream.exact(8), "big")
    if not 1 <= length <= config.max_bytes:
        raise ReportRejectedError("invalid report length")
    fd = -1
    created = False
    try:
        with _masked_interrupts():
            fd = os.open(
                PARTIAL,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory,
            )
            created = True
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ReportRejectedError("invalid partial file")
        remaining = length
        while remaining:
            chunk = stream.read(min(remaining, CHUNK_BYTES))
            if not chunk:
                raise ReportRejectedError("truncated report")
            _write_all(fd, chunk, stream)
            remaining -= len(chunk)
        if stream.read(1):
            raise ReportRejectedError("trailing bytes")
        stream.check_deadline()
        os.fchmod(fd, 0o400)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        stream.check_deadline()
        os.link(
            PARTIAL,
            FINAL,
            src_dir_fd=directory,
            dst_dir_fd=directory,
            follow_symlinks=False,
        )
        with _masked_interrupts():
            os.unlink(PARTIAL, dir_fd=directory)
            created = False
        os.fsync(directory)
    finally:
        previous_mask = None
        try:
            previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, INTERRUPT_SIGNALS)
        finally:
            try:
                try:
                    if fd >= 0:
                        os.close(fd)
                finally:
                    if created:
                        os.unlink(PARTIAL, dir_fd=directory)
            finally:
                if previous_mask is not None:
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def receive_report(
    config: UploadConfig,
    input_fd: int,
    *,
    trusted_owner_uid: int,
    clock: Callable[[], float],
    wait: Callable[[int, int, float], bool] = wait_ready,
) -> ReportStatus:
    if os.geteuid() != config.upload_uid:
        raise ReportRejectedError("wrong upload uid")
    stream = InputStream(
        fd=input_fd, deadline=clock() + config.timeout_seconds, clock=clock, wait=wait
    )
    original_blocking = os.get_blocking(input_fd)
    parent, name = open_parent(config.inbox, trusted_owner_uid=trusted_owner_uid)
    directory = -1
    try:
        directory = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
        info = os.fstat(directory)
        if info.st_uid != config.upload_uid or stat.S_IMODE(info.st_mode) != 0o700:
            raise ReportRejectedError("inbox must be private")
        try:
            fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return ReportStatus.BUSY
            raise
        os.set_blocking(input_fd, False)
        _receive_locked(directory, config, stream)
        return ReportStatus.RECEIVED
    finally:
        try:
            if directory >= 0:
                os.close(directory)
        finally:
            os.close(parent)
            _restore_blocking(input_fd, original_blocking)


def _restore_blocking(fd: int, blocking: bool) -> None:
    with suppress(OSError):
        os.set_blocking(fd, blocking)


def emit_status(
    status: ReportStatus,
    output_fd: int,
    *,
    clock: Callable[[], float],
    wait: Callable[[int, int, float], bool] = wait_ready,
) -> None:
    deadline = clock() + 1.0
    remaining = memoryview(status.value[1])
    original_blocking: bool | None = None
    try:
        original_blocking = os.get_blocking(output_fd)
        os.set_blocking(output_fd, False)
        while remaining:
            seconds = deadline - clock()
            if seconds <= 0:
                return
            try:
                count = os.write(output_fd, remaining)
            except BlockingIOError:
                if not wait(output_fd, selectors.EVENT_WRITE, seconds):
                    return
                continue
            if count <= 0:
                return
            remaining = remaining[count:]
    except (OSError, ValueError, ReportInterruptedError):
        return
    finally:
        if original_blocking is not None:
            _restore_blocking(output_fd, original_blocking)


def _interrupt(signum: int, frame: object) -> None:
    global _interrupt_raised
    if _interrupt_raised:
        return
    _interrupt_raised = True
    signal.pthread_sigmask(signal.SIG_BLOCK, INTERRUPT_SIGNALS)
    raise ReportInterruptedError("upload interrupted")


def _silence_interrupts() -> None:
    signal.pthread_sigmask(signal.SIG_BLOCK, INTERRUPT_SIGNALS)
    for signum in INTERRUPT_SIGNALS:
        signal.signal(signum, signal.SIG_IGN)


def main() -> int:
    global _interrupt_raised
    status = ReportStatus.REJECTED
    try:
        try:
            with _masked_interrupts():
                _interrupt_raised = False
                for signum in INTERRUPT_SIGNALS:
                    signal.signal(signum, _interrupt)
            if len(sys.argv) != 1 or os.environ.get("SSH_ORIGINAL_COMMAND") != COMMAND:
                raise ReportRejectedError("unsupported invocation")
            config = load_config(CONFIG_PATH, trusted_owner_uid=0)
            status = receive_report(
                config, 0, trusted_owner_uid=0, clock=time.monotonic
            )
        except (Exception, KeyboardInterrupt):
            status = ReportStatus.REJECTED
        finally:
            _silence_interrupts()
    except (Exception, KeyboardInterrupt):
        # A previously trapped handler can run during exception/finally teardown.
        status = ReportStatus.REJECTED
        _silence_interrupts()
    emit_status(status, 1, clock=time.monotonic)
    return status.value[0]


if __name__ == "__main__":
    sys.exit(main())
