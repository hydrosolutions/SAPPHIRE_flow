from __future__ import annotations

import importlib.util
import json
import os
import selectors
import signal
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tools.receive_build_report import ReportStatus, UploadConfig

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools/receive_build_report.py"
SPEC = importlib.util.spec_from_file_location("receive_build_report", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
receiver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = receiver
SPEC.loader.exec_module(receiver)


@pytest.fixture(autouse=True)
def signal_state() -> Iterator[None]:
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    previous_handlers = {
        sig: signal.getsignal(sig) for sig in receiver.INTERRUPT_SIGNALS
    }
    namespace = vars(receiver)
    previous_raised = namespace["_interrupt_raised"]
    namespace["_interrupt_raised"] = False
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, receiver.INTERRUPT_SIGNALS)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        namespace["_interrupt_raised"] = previous_raised
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


@pytest.fixture
def private_root() -> Iterator[Path]:
    # Ordinary pytest tmp directories have writable ancestors: never relax the walk.
    assert os.geteuid() != 0, "receiver tests require a non-root test account"
    with tempfile.TemporaryDirectory(prefix=".report-tests-", dir=ROOT) as name:
        Path(name).chmod(0o700)
        parent, _ = receiver.open_parent(
            name + "/inbox", trusted_owner_uid=os.geteuid()
        )
        os.close(parent)
        yield Path(name)


@pytest.fixture
def inbox(private_root: Path) -> Path:
    path = private_root / "inbox"
    path.mkdir(mode=0o700)
    return path


def configuration(inbox: Path, **changes: object) -> UploadConfig:
    fields: dict[str, object] = {
        "inbox": str(inbox),
        "upload_uid": os.geteuid(),
        "max_bytes": 1000,
        "timeout_seconds": 10,
    }
    fields.update(changes)
    return receiver.UploadConfig(**fields)


def frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(8, "big") + payload


def deliver(inbox: Path, data: bytes, **options: object) -> ReportStatus:
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, data)
        os.close(write_fd)
        write_fd = -1
        return receiver.receive_report(
            configuration(inbox),
            read_fd,
            trusted_owner_uid=os.geteuid(),
            clock=lambda: 0.0,
            **options,
        )
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


class TestConfig:
    def test_real_trusted_config(self, private_root: Path, inbox: Path) -> None:
        path = private_root / "config.json"
        raw = dict(
            inbox=str(inbox), upload_uid=os.geteuid(), max_bytes=100, timeout_seconds=10
        )
        path.write_text(json.dumps(raw))
        path.chmod(0o600)
        result = receiver.load_config(str(path), trusted_owner_uid=os.geteuid())
        assert result == configuration(inbox, max_bytes=100)

    @pytest.mark.parametrize(
        "path", ["relative", "//a", "/a//b", "/a/./b", "/a/../b", "/a/", "/", "/a\x00b"]
    )
    def test_noncanonical_path(self, path: str) -> None:
        with pytest.raises(receiver.ReportRejectedError, match="canonical"):
            receiver.path_components(path)

    @pytest.mark.parametrize(
        "fields",
        [
            {"max_bytes": True},
            {"max_bytes": 0},
            {"max_bytes": 67108865},
            {"timeout_seconds": False},
            {"timeout_seconds": 121},
            {"upload_uid": 0},
            {"upload_uid": -1},
            {"max_bytes": 1.5},
        ],
    )
    def test_invalid_bounds(self, inbox: Path, fields: dict[str, object]) -> None:
        with pytest.raises(receiver.ReportRejectedError, match="bound"):
            configuration(inbox, **fields)

    @pytest.mark.parametrize(
        "raw, message",
        [
            (b'{"inbox":"/a","inbox":"/b"}', "duplicate"),
            (b"{}", "fields"),
            (b"[]", "fields"),
            (b" " * 4097, "large"),
        ],
    )
    def test_invalid_json_contract(self, raw: bytes, message: str) -> None:
        with pytest.raises(receiver.ReportRejectedError, match=message):
            receiver.parse_config(raw)

    @pytest.mark.parametrize(
        "kind", ["symlink", "writable", "hardlink", "fifo", "large", "directory"]
    )
    def test_unsafe_config(self, private_root: Path, kind: str) -> None:
        path = private_root / "config"
        if kind == "symlink":
            path.symlink_to(private_root / "missing")
        elif kind == "fifo":
            os.mkfifo(path)
        elif kind == "directory":
            path.mkdir()
        else:
            path.write_bytes(b" " * (4097 if kind == "large" else 2))
            path.chmod(0o600)
            if kind == "writable":
                path.chmod(0o666)
            if kind == "hardlink":
                os.link(path, private_root / "another")
        with pytest.raises((receiver.ReportRejectedError, OSError)):
            receiver.load_config(str(path), trusted_owner_uid=os.geteuid())

    def test_wrong_config_owner(self, private_root: Path) -> None:
        path = private_root / "config"
        path.write_text("{}")
        path.chmod(0o600)
        # Ancestors still validate with the real owner; independently test config UID.
        original = receiver.open_parent
        from unittest.mock import patch

        def parent(path: str, *, trusted_owner_uid: int) -> tuple[int, str]:
            return original(path, trusted_owner_uid=os.geteuid())

        with (
            patch.object(receiver, "open_parent", parent),
            pytest.raises(receiver.ReportRejectedError, match="config"),
        ):
            receiver.load_config(str(path), trusted_owner_uid=os.geteuid() + 1)


class TestReceive:
    @pytest.mark.parametrize(
        "payload", [b"x", b"private synthetic report", b"z" * 1000]
    )
    def test_complete_report(self, inbox: Path, payload: bytes) -> None:
        assert deliver(inbox, frame(payload)) is receiver.ReportStatus.RECEIVED
        assert (inbox / "report.bin").read_bytes() == payload
        assert stat.S_IMODE((inbox / "report.bin").stat().st_mode) == 0o400
        assert list(inbox.iterdir()) == [inbox / "report.bin"]

    @pytest.mark.parametrize(
        "data, message",
        [
            (b"", "truncated"),
            (b"123", "truncated"),
            (frame(b""), "length"),
            ((1001).to_bytes(8, "big"), "length"),
            ((4).to_bytes(8, "big") + b"abc", "truncated"),
            (frame(b"abc") + b"extra", "trailing"),
        ],
    )
    def test_invalid_framing(self, inbox: Path, data: bytes, message: str) -> None:
        with pytest.raises(receiver.ReportRejectedError, match=message):
            deliver(inbox, data)
        assert list(inbox.iterdir()) == []

    @pytest.mark.parametrize("name", ["report.bin", "report.partial"])
    @pytest.mark.parametrize("kind", ["file", "symlink", "directory"])
    def test_existing_state_preserved(self, inbox: Path, name: str, kind: str) -> None:
        path = inbox / name
        if kind == "file":
            path.write_bytes(b"original")
        elif kind == "symlink":
            path.symlink_to(inbox / "missing")
        else:
            path.mkdir()
        with pytest.raises(receiver.ReportRejectedError, match="clearance"):
            deliver(inbox, frame(b"new"))
        assert path.is_symlink() if kind == "symlink" else path.exists()
        if kind == "file":
            assert path.read_bytes() == b"original"

    def test_clean_failure_allows_retry_but_success_refuses_retry(
        self, inbox: Path
    ) -> None:
        with pytest.raises(receiver.ReportRejectedError, match="truncated"):
            deliver(inbox, (2).to_bytes(8, "big") + b"x")
        deliver(inbox, frame(b"ok"))
        with pytest.raises(receiver.ReportRejectedError, match="clearance"):
            deliver(inbox, frame(b"replace"))
        assert (inbox / "report.bin").read_bytes() == b"ok"

    def test_native_directory_lock_contention(self, inbox: Path) -> None:
        import fcntl

        fd = os.open(inbox, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert deliver(inbox, frame(b"x")) is receiver.ReportStatus.BUSY
            assert list(inbox.iterdir()) == []
        finally:
            os.close(fd)
        assert deliver(inbox, frame(b"x")) is receiver.ReportStatus.RECEIVED

    @pytest.mark.parametrize("mode", [0o777, 0o750, 0o755])
    def test_inbox_permissions(self, inbox: Path, mode: int) -> None:
        inbox.chmod(mode)
        with pytest.raises(receiver.ReportRejectedError, match="private"):
            deliver(inbox, frame(b"x"))

    def test_writable_ancestor(self, inbox: Path) -> None:
        inbox.parent.chmod(0o777)
        with pytest.raises(receiver.ReportRejectedError, match="ancestor"):
            deliver(inbox, frame(b"x"))

    def test_symlink_ancestor(self, private_root: Path, inbox: Path) -> None:
        link = private_root / "alias"
        link.symlink_to(inbox.parent, target_is_directory=True)
        with pytest.raises(OSError):
            deliver(link / "inbox", frame(b"x"))

    def test_symlink_inbox(self, private_root: Path, inbox: Path) -> None:
        link = private_root / "alias"
        link.symlink_to(inbox, target_is_directory=True)
        with pytest.raises(OSError):
            deliver(link, frame(b"x"))

    def test_wrong_uid(self, inbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = configuration(inbox)
        uid = os.geteuid()
        monkeypatch.setattr(receiver.os, "geteuid", lambda: uid + 1)
        with pytest.raises(receiver.ReportRejectedError, match="wrong upload uid"):
            receiver.receive_report(
                config, -1, trusted_owner_uid=uid, clock=lambda: 0.0
            )

    @pytest.mark.parametrize("read_number", [1, 2, 3])
    def test_timeout_header_body_eof(self, inbox: Path, read_number: int) -> None:
        calls = 0

        def wait(fd: int, event: int, seconds: float) -> bool:
            nonlocal calls
            calls += 1
            return calls != read_number

        with pytest.raises(receiver.ReportRejectedError, match="deadline"):
            deliver(inbox, frame(b"x"), wait=wait)
        assert list(inbox.iterdir()) == []

    def test_absolute_deadline_not_reset(self, inbox: Path) -> None:
        read_fd, write_fd = os.pipe()
        clock = iter([0.0, 1.0, 11.0])

        def ready(fd: int, event: int, seconds: float) -> bool:
            return True

        try:
            os.write(write_fd, frame(b"x"))
            with pytest.raises(receiver.ReportRejectedError, match="deadline"):
                receiver.receive_report(
                    configuration(inbox),
                    read_fd,
                    trusted_owner_uid=os.geteuid(),
                    clock=lambda: next(clock),
                    wait=ready,
                )
            assert list(inbox.iterdir()) == []
        finally:
            os.close(read_fd)
            os.close(write_fd)

    @pytest.mark.parametrize("sig", [signal.SIGHUP, signal.SIGINT, signal.SIGTERM])
    def test_interruption_cleanup(self, inbox: Path, sig: int) -> None:
        calls = 0

        def wait(fd: int, event: int, seconds: float) -> bool:
            nonlocal calls
            calls += 1
            if calls == 2:
                receiver._interrupt(sig, None)
            return True

        with pytest.raises(receiver.ReportInterruptedError, match="interrupted"):
            deliver(inbox, frame(b"x"), wait=wait)
        assert list(inbox.iterdir()) == []

    def test_short_writes(self, inbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        original = os.write

        def write(fd: int, data: bytes) -> int:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                return original(fd, data[:1])
            return original(fd, data)

        monkeypatch.setattr(receiver.os, "write", write)
        deliver(inbox, frame(b"abcdef"))
        assert (inbox / "report.bin").read_bytes() == b"abcdef"

    @pytest.mark.parametrize("operation", ["write", "fsync", "link"])
    def test_io_errors_cleanup(
        self, inbox: Path, monkeypatch: pytest.MonkeyPatch, operation: str
    ) -> None:
        original = getattr(os, operation)

        def fail(*args: object, **kwargs: object) -> object:
            if operation != "write":
                raise OSError("synthetic private diagnostic")
            assert isinstance(args[0], int)
            if stat.S_ISREG(os.fstat(args[0]).st_mode):
                raise OSError("synthetic private diagnostic")
            return original(*args, **kwargs)

        monkeypatch.setattr(receiver.os, operation, fail)
        with pytest.raises(OSError, match="synthetic"):
            deliver(inbox, frame(b"x"))
        assert list(inbox.iterdir()) == []

    def test_late_fsync_preserves_uncertain_final(
        self, inbox: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = os.fsync

        def fsync(fd: int) -> None:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("synthetic directory fsync failure")
            original(fd)

        monkeypatch.setattr(receiver.os, "fsync", fsync)
        with pytest.raises(OSError, match="directory"):
            deliver(inbox, frame(b"x"))
        assert (inbox / "report.bin").read_bytes() == b"x"
        with pytest.raises(receiver.ReportRejectedError, match="clearance"):
            deliver(inbox, frame(b"y"))

    def test_no_clobber_link(
        self, inbox: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = os.link

        def conflict(
            src: str,
            dst: str,
            *,
            src_dir_fd: int,
            dst_dir_fd: int,
            follow_symlinks: bool,
        ) -> None:
            (inbox / "report.bin").write_bytes(b"other")
            original(
                src,
                dst,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )

        monkeypatch.setattr(receiver.os, "link", conflict)
        with pytest.raises(FileExistsError):
            deliver(inbox, frame(b"x"))
        assert (inbox / "report.bin").read_bytes() == b"other"
        assert not (inbox / "report.partial").exists()

    def test_cleanup_failure_leaves_bounded_stale_file(
        self, inbox: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*args: object, **kwargs: object) -> None:
            raise OSError("synthetic cleanup failure")

        monkeypatch.setattr(receiver.os, "unlink", fail)
        with pytest.raises(OSError, match="cleanup"):
            deliver(inbox, (2).to_bytes(8, "big") + b"x")
        assert (inbox / "report.partial").read_bytes() == b"x"
        with pytest.raises(receiver.ReportRejectedError, match="clearance"):
            deliver(inbox, frame(b"y"))

    def test_multichunk_input_with_bounded_reads(
        self, inbox: Path, private_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = b"synthetic" * 16000
        path = private_root / "input"
        path.write_bytes(frame(payload))
        original = os.read
        requested: list[int] = []

        def read(fd: int, count: int) -> bytes:
            requested.append(count)
            return original(fd, count)

        def ready(fd: int, event: int, seconds: float) -> bool:
            return True

        monkeypatch.setattr(receiver.os, "read", read)
        with path.open("rb") as source:
            assert (
                receiver.receive_report(
                    configuration(inbox, max_bytes=len(payload)),
                    source.fileno(),
                    trusted_owner_uid=os.geteuid(),
                    clock=lambda: 0.0,
                    wait=ready,
                )
                is receiver.ReportStatus.RECEIVED
            )
        assert (inbox / "report.bin").read_bytes() == payload
        assert max(requested) <= receiver.CHUNK_BYTES

    def test_read_failure_cleans_created_partial(
        self, inbox: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = os.read
        calls = 0

        def read(fd: int, count: int) -> bytes:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("private read diagnostic")
            return original(fd, count)

        monkeypatch.setattr(receiver.os, "read", read)
        with pytest.raises(OSError, match="read diagnostic"):
            deliver(inbox, frame(b"x"))
        assert list(inbox.iterdir()) == []

    def test_input_eagain_retries_without_resetting_deadline(
        self, inbox: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = os.read
        calls = 0

        def read(fd: int, count: int) -> bytes:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise BlockingIOError()
            return original(fd, count)

        monkeypatch.setattr(receiver.os, "read", read)
        assert deliver(inbox, frame(b"x")) is receiver.ReportStatus.RECEIVED

    def test_raced_partial_is_not_deleted(
        self, inbox: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = inbox / "report.partial"
        path.write_bytes(b"existing report")
        path.chmod(0o600)

        def absent(directory: int, name: str) -> bool:
            return False

        monkeypatch.setattr(receiver, "_exists", absent)
        with pytest.raises(FileExistsError):
            deliver(inbox, frame(b"new"))
        assert path.read_bytes() == b"existing report"

    def test_wrong_ancestor_owner(self, inbox: Path) -> None:
        read_fd, write_fd = os.pipe()
        try:
            with pytest.raises(receiver.ReportRejectedError, match="ancestor"):
                receiver.receive_report(
                    configuration(inbox),
                    read_fd,
                    trusted_owner_uid=os.geteuid() + 1,
                    clock=lambda: 0.0,
                )
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_wrong_inbox_owner(
        self, inbox: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        uid = os.geteuid()
        config = configuration(inbox, upload_uid=uid + 1)
        monkeypatch.setattr(receiver.os, "geteuid", lambda: uid + 1)
        read_fd, write_fd = os.pipe()
        try:
            with pytest.raises(receiver.ReportRejectedError, match="private"):
                receiver.receive_report(
                    config, read_fd, trusted_owner_uid=uid, clock=lambda: 0.0
                )
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_fragmented_header(
        self, inbox: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = os.read

        def fragmented(fd: int, count: int) -> bytes:
            return original(fd, min(1, count))

        monkeypatch.setattr(receiver.os, "read", fragmented)
        assert deliver(inbox, frame(b"report")) is receiver.ReportStatus.RECEIVED
        assert (inbox / "report.bin").read_bytes() == b"report"

    def test_progress_does_not_extend_deadline(
        self, inbox: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = os.read
        now = 0.0
        reads = 0

        def progressing(fd: int, count: int) -> bytes:
            nonlocal now, reads
            reads += 1
            now += 0.75
            return original(fd, min(1, count))

        monkeypatch.setattr(receiver.os, "read", progressing)
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, frame(b"abcdefghij"))
            os.close(write_fd)
            write_fd = -1
            with pytest.raises(receiver.ReportRejectedError, match="deadline"):
                receiver.receive_report(
                    configuration(inbox),
                    read_fd,
                    trusted_owner_uid=os.geteuid(),
                    clock=lambda: now,
                )
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)
        assert reads > 8  # Header and part of the body progressed before expiry.
        assert list(inbox.iterdir()) == []

    def test_deadline_crossed_after_commit_is_received(
        self, inbox: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = 0.0
        original = os.fsync

        def committed(fd: int) -> None:
            nonlocal now
            original(fd)
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                now = 11.0

        monkeypatch.setattr(receiver.os, "fsync", committed)
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, frame(b"x"))
            os.close(write_fd)
            write_fd = -1
            assert (
                receiver.receive_report(
                    configuration(inbox),
                    read_fd,
                    trusted_owner_uid=os.geteuid(),
                    clock=lambda: now,
                )
                is receiver.ReportStatus.RECEIVED
            )
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)
        assert (inbox / "report.bin").read_bytes() == b"x"

    def test_create_and_cleanup_mask_interrupts(
        self, inbox: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_open = os.open
        original_unlink = os.unlink
        masks: list[frozenset[int]] = []

        def opened(
            path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
        ) -> int:
            if path == receiver.PARTIAL:
                masks.append(frozenset(signal.pthread_sigmask(signal.SIG_BLOCK, [])))
            return original_open(path, flags, mode, dir_fd=dir_fd)

        def unlinked(path: str, *, dir_fd: int | None = None) -> None:
            masks.append(frozenset(signal.pthread_sigmask(signal.SIG_BLOCK, [])))
            original_unlink(path, dir_fd=dir_fd)

        monkeypatch.setattr(receiver.os, "open", opened)
        monkeypatch.setattr(receiver.os, "unlink", unlinked)
        before = signal.pthread_sigmask(signal.SIG_BLOCK, [])
        with pytest.raises(receiver.ReportRejectedError, match="truncated"):
            deliver(inbox, (2).to_bytes(8, "big") + b"x")
        assert len(masks) == 2
        assert all(mask >= receiver.INTERRUPT_SIGNALS for mask in masks)
        assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == before
        assert list(inbox.iterdir()) == []

    def test_first_signal_at_cleanup_guard_entry_still_removes_partial(
        self, inbox: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = signal.pthread_sigmask
        triggered = False

        def interrupted_block(how: int, mask: set[int]) -> set[int]:
            nonlocal triggered
            result = original(how, mask)
            if (
                how == signal.SIG_BLOCK
                and not triggered
                and (inbox / "report.partial").exists()
            ):
                triggered = True
                receiver._interrupt(signal.SIGTERM, None)
            return result

        monkeypatch.setattr(receiver.signal, "pthread_sigmask", interrupted_block)
        with pytest.raises(receiver.ReportInterruptedError, match="interrupted"):
            deliver(inbox, (2).to_bytes(8, "big") + b"x")
        assert triggered
        assert list(inbox.iterdir()) == []


class TestOutput:
    def test_shared_socket_descriptions(self, inbox: Path) -> None:
        server, client = socket.socketpair()
        output_fd = os.dup(server.fileno())
        try:
            client.sendall(frame(b"x"))
            client.shutdown(socket.SHUT_WR)
            status = receiver.receive_report(
                configuration(inbox),
                server.fileno(),
                trusted_owner_uid=os.geteuid(),
                clock=lambda: 0.0,
            )
            assert os.get_blocking(output_fd)
            receiver.emit_status(status, output_fd, clock=lambda: 0.0)
            assert client.recv(100) == b"REPORT_RECEIVED\n"
            assert os.get_blocking(output_fd)
            assert os.get_blocking(server.fileno())
        finally:
            os.close(output_fd)
            server.close()
            client.close()

    def test_disconnected_output_keeps_received_file(self, inbox: Path) -> None:
        deliver(inbox, frame(b"x"))
        server, client = socket.socketpair()
        client.close()
        try:
            receiver.emit_status(
                receiver.ReportStatus.RECEIVED, server.fileno(), clock=lambda: 0.0
            )
        finally:
            server.close()
        assert (inbox / "report.bin").read_bytes() == b"x"

    def test_output_eagain_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        waits: list[tuple[int, float]] = []

        def blocked(fd: int, data: bytes) -> int:
            raise BlockingIOError()

        def wait(fd: int, event: int, seconds: float) -> bool:
            waits.append((event, seconds))
            return False

        read_fd, write_fd = os.pipe()
        try:
            monkeypatch.setattr(receiver.os, "write", blocked)
            receiver.emit_status(
                receiver.ReportStatus.REJECTED, write_fd, clock=lambda: 0.0, wait=wait
            )
        finally:
            os.close(read_fd)
            os.close(write_fd)
        assert waits == [(selectors.EVENT_WRITE, 1.0)]

    @pytest.mark.parametrize(
        "status, code, line",
        [
            (receiver.ReportStatus.RECEIVED, 0, b"REPORT_RECEIVED\n"),
            (receiver.ReportStatus.REJECTED, 1, b"REPORT_REJECTED\n"),
            (receiver.ReportStatus.BUSY, 2, b"REPORT_BUSY\n"),
        ],
    )
    def test_status_contract(
        self, status: ReportStatus, code: int, line: bytes
    ) -> None:
        assert status.value == (code, line)

    @pytest.mark.parametrize(
        "args, command",
        [
            ([], "anything-private"),
            (["--config", "/private"], receiver.COMMAND),
            ([], receiver.COMMAND),
        ],
    )
    def test_cli_refuses_without_config_quietly(
        self, args: list[str], command: str
    ) -> None:
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), *args],
            input=b"private data",
            capture_output=True,
            env={"SSH_ORIGINAL_COMMAND": command, "CONFIG_PATH": "/anything"},
            timeout=5,
            check=False,
        )
        assert (result.returncode, result.stdout, result.stderr) == (
            1,
            b"REPORT_REJECTED\n",
            b"",
        )

    def test_output_short_writes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = os.write

        def write(fd: int, data: bytes) -> int:
            return original(fd, data[:1])

        read_fd, write_fd = os.pipe()
        try:
            monkeypatch.setattr(receiver.os, "write", write)
            receiver.emit_status(
                receiver.ReportStatus.RECEIVED, write_fd, clock=lambda: 0.0
            )
            assert os.read(read_fd, 100) == b"REPORT_RECEIVED\n"
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_output_absolute_deadline(self) -> None:
        read_fd, write_fd = os.pipe()
        clock = iter([0.0, 2.0])
        try:
            receiver.emit_status(
                receiver.ReportStatus.RECEIVED, write_fd, clock=lambda: next(clock)
            )
            os.set_blocking(read_fd, False)
            with pytest.raises(BlockingIOError):
                os.read(read_fd, 100)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_entrypoint_suppresses_private_exception(
        self,
        inbox: Path,
        monkeypatch: pytest.MonkeyPatch,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        owners: list[int] = []

        def load(path: str, *, trusted_owner_uid: int) -> UploadConfig:
            owners.append(trusted_owner_uid)
            assert path == receiver.CONFIG_PATH
            raise OSError("synthetic private path and report contents")

        def handle(signum: int, handler: object) -> None:
            return None

        monkeypatch.setattr(receiver, "load_config", load)
        monkeypatch.setattr(receiver.signal, "signal", handle)
        monkeypatch.setattr(receiver.sys, "argv", [str(SCRIPT)])
        monkeypatch.setenv("SSH_ORIGINAL_COMMAND", receiver.COMMAND)
        assert receiver.main() == 1
        assert capfd.readouterr() == ("REPORT_REJECTED\n", "")
        assert owners == [0]

    def test_emit_restores_original_pipe_mode(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            assert os.get_blocking(write_fd)
            receiver.emit_status(
                receiver.ReportStatus.REJECTED, write_fd, clock=lambda: 0.0
            )
            assert os.get_blocking(write_fd)
            os.set_blocking(write_fd, False)
            receiver.emit_status(
                receiver.ReportStatus.REJECTED, write_fd, clock=lambda: 0.0
            )
            assert not os.get_blocking(write_fd)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_interrupt_handler_is_one_shot(self) -> None:
        with pytest.raises(receiver.ReportInterruptedError, match="interrupted"):
            receiver._interrupt(signal.SIGTERM, None)
        receiver._interrupt(signal.SIGHUP, None)
        receiver._interrupt(signal.SIGINT, None)
        assert (
            signal.pthread_sigmask(signal.SIG_BLOCK, []) >= receiver.INTERRUPT_SIGNALS
        )

    @pytest.mark.parametrize("stage", ["teardown", "repeated"])
    def test_real_signals_have_quiet_terminal_teardown(self, stage: str) -> None:
        code = """
import importlib.util, os, signal, sys
spec = importlib.util.spec_from_file_location("receiver", sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
stage = sys.argv[2]
sys.argv = [sys.argv[1]]
os.environ["SSH_ORIGINAL_COMMAND"] = m.COMMAND
original = signal.signal
sent = False
def wrapped(sig, handler):
    global sent
    result = original(sig, handler)
    if stage == "teardown" and handler == signal.SIG_IGN and not sent:
        sent = True
        os.kill(os.getpid(), signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGHUP)
    return result
signal.signal = wrapped
def load(*args, **kwargs):
    if stage == "repeated":
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        finally:
            os.kill(os.getpid(), signal.SIGHUP)
            os.kill(os.getpid(), signal.SIGINT)
    raise OSError("private diagnostic")
m.load_config = load
sys.exit(m.main())
"""
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, str(SCRIPT), stage],
            capture_output=True,
            timeout=5,
            check=False,
        )
        assert (result.returncode, result.stdout, result.stderr) == (
            1,
            b"REPORT_REJECTED\n",
            b"",
        )

    @pytest.mark.parametrize("stage", ["create", "cleanup"])
    def test_signal_in_file_bookkeeping_preserves_cleanup(
        self, inbox: Path, stage: str
    ) -> None:
        code = """
import importlib.util, os, signal, sys
spec = importlib.util.spec_from_file_location("receiver", sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
script, inbox, stage = sys.argv[1:]
sys.argv = [script]
os.environ["SSH_ORIGINAL_COMMAND"] = m.COMMAND
config = m.UploadConfig(inbox=inbox, upload_uid=os.geteuid(),
                        max_bytes=1000, timeout_seconds=10)
m.load_config = lambda *args, **kwargs: config
receive = m.receive_report
def run(config, fd, *, trusted_owner_uid, clock):
    return receive(config, fd, trusted_owner_uid=os.geteuid(), clock=clock)
m.receive_report = run
original_open, original_unlink = os.open, os.unlink
sent = False
def trigger():
    global sent
    assert m.INTERRUPT_SIGNALS <= signal.pthread_sigmask(signal.SIG_BLOCK, [])
    if not sent:
        sent = True
        os.kill(os.getpid(), signal.SIGTERM)
        os.kill(os.getpid(), signal.SIGHUP)
def opened(path, *args, **kwargs):
    result = original_open(path, *args, **kwargs)
    if stage == "create" and path == m.PARTIAL:
        trigger()
    return result
def unlinked(path, *args, **kwargs):
    if stage == "cleanup" and path == m.PARTIAL:
        trigger()
    return original_unlink(path, *args, **kwargs)
os.open, os.unlink = opened, unlinked
sys.exit(m.main())
"""
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, str(SCRIPT), str(inbox), stage],
            input=(2).to_bytes(8, "big") + b"x",
            capture_output=True,
            timeout=5,
            check=False,
        )
        assert (result.returncode, result.stdout, result.stderr) == (
            1,
            b"REPORT_REJECTED\n",
            b"",
        )
        assert list(inbox.iterdir()) == []
