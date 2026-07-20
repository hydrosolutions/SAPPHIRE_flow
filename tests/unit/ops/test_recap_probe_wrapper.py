"""Ops tests for Plan 132 — recap-probe deployment reconciliation.

Tests assert the JSONL-purity branching in
``scripts/launchd/run-recap-probe.sh``:

  (a) exec exits 0, every stderr line is valid JSON -> lines land in the
      JSONL, nothing else.
  (b) exec exits 0 but a non-JSON line reaches stderr -> JSONL untouched,
      buffer + banner routed to the wrapper's own stderr (the launchd log).
  (c) exec exits non-zero -> JSONL untouched, error routed to the launchd
      log.

Plus the key-file guard: a missing/unreadable/empty key file must exit
non-zero WITHOUT invoking docker at all.

These are shell-script tests, not Python unit tests in the usual sense. We
shell out via subprocess to exercise the actual wrapper; docker is faked by
setting DOCKER_CMD to an absolute path of a stub (the same convention
tests/unit/ops/test_launchd_prune_docker.py uses, not PATH injection). Host
paths (key file, JSONL log, summary log, and the stdin-fed probe-script
path) are overridden via env vars so CI never touches
``/Users/sapphire/...``.

Soundness: branches (b) and (c) are what prove the fix. Both are exercised
against a "buggy" wrapper stand-in (```_run_buggy_wrapper``) that mirrors the
originally-deployed wrapper -- it pipes the container's raw stderr straight
into the JSONL with no purity check and no ``--user app`` -- to confirm they
would let a non-JSON line / a failed-exec error into the JSONL. Branch (a)
passes against both, so it is a guard-rail, not the discriminating case.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import textwrap
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts" / "launchd"
_WRAPPER_SCRIPT = _SCRIPTS_DIR / "run-recap-probe.sh"


def _write_fake_docker(
    bin_dir: Path,
    *,
    stdout: str,
    stderr: str,
    exit_code: int,
    args_log: Path,
    stderr_newline: bool = True,
) -> Path:
    """Write a fake docker executable that records its args then emits
    fixed stdout/stderr and exits with the given code -- standing in for
    `docker exec` without touching the real Docker daemon.

    When ``stderr_newline`` is False the stderr bytes are emitted verbatim via
    ``printf %s`` (no trailing newline), so a test can exercise an unterminated
    final line -- the case a plain ``while read`` in the wrapper would drop."""
    stderr_emit = (
        ["cat >&2 <<'__STDERR__'", stderr, "__STDERR__"]
        if stderr_newline
        else [f"printf %s {shlex.quote(stderr)} >&2"]
    )
    lines = [
        "#!/bin/bash",
        f'printf \'%s\\n\' "$@" > "{args_log}"',
        "cat >&1 <<'__STDOUT__'",
        stdout,
        "__STDOUT__",
        *stderr_emit,
        f"exit {exit_code}",
        "",
    ]
    fake = bin_dir / "docker"
    fake.write_text("\n".join(lines))
    fake.chmod(0o755)
    return fake


def _write_probe_stub(tmp_path: Path) -> Path:
    """A stand-in for scripts/recap_probe_loop.py -- the fake docker never
    reads it, but the wrapper's stdin redirection needs a real file to
    open (the hardcoded production path does not exist off the mac mini)."""
    stub = tmp_path / "recap_probe_loop.py"
    stub.write_text("# stub probe script for wrapper tests\n")
    return stub


def _run_wrapper(
    tmp_path: Path,
    *,
    docker_cmd: Path,
    key_file: Path,
    host_jsonl: Path,
    host_summary: Path,
    wrapper: Path = _WRAPPER_SCRIPT,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "DOCKER_CMD": str(docker_cmd),
        "RECAP_PROBE_KEY_FILE": str(key_file),
        "RECAP_PROBE_HOST_LOG": str(host_jsonl),
        "RECAP_PROBE_HOST_SUMMARY": str(host_summary),
        "RECAP_PROBE_SCRIPT": str(_write_probe_stub(tmp_path)),
    }
    return subprocess.run(
        ["bash", str(wrapper)],
        capture_output=True,
        text=True,
        env=env,
    )


def _write_buggy_wrapper(tmp_path: Path) -> Path:
    """A stand-in for the originally-deployed (uncorrected) wrapper: it
    pipes the container's raw stderr straight into the JSONL with no
    purity check, and runs `docker exec` without `--user app`. Used only
    to prove the locking tests are sound (they must fail against this)."""
    script = textwrap.dedent(
        """\
        #!/bin/bash
        set -uo pipefail
        DOCKER="${DOCKER_CMD:-/usr/local/bin/docker}"
        KEY_FILE="${RECAP_PROBE_KEY_FILE:-/Users/sapphire/.config/sapphire/recap_api_key}"
        HOST_JSONL="${RECAP_PROBE_HOST_LOG:-/Users/sapphire/Library/Logs/sapphire-recap-probe.jsonl}"
        HOST_SUMMARY="${RECAP_PROBE_HOST_SUMMARY:-/Users/sapphire/Library/Logs/sapphire-recap-probe.summary.log}"
        PROBE_SCRIPT="${RECAP_PROBE_SCRIPT:-/Users/sapphire/SAPPHIRE_flow/scripts/recap_probe_loop.py}"
        KEY="$(cat "${KEY_FILE}" 2>/dev/null)"
        "${DOCKER}" exec -i \\
            -e RECAP_API_KEY="${KEY}" \\
            -e RECAP_TEST_HRU=12300 \\
            -e RECAP_PROBE_LOG=/dev/stderr \\
            sapphire_flow-prefect-worker-1 python - \\
            <"${PROBE_SCRIPT}" >"${HOST_SUMMARY}.tmp" 2>>"${HOST_JSONL}"
        EXIT_CODE=$?
        cat "${HOST_SUMMARY}.tmp" >> "${HOST_SUMMARY}"
        exit "${EXIT_CODE}"
        """
    )
    buggy = tmp_path / "run-recap-probe-buggy.sh"
    buggy.write_text(script)
    buggy.chmod(0o755)
    return buggy


class TestKeyGuard:
    """The key-file guard must fire before docker is ever invoked."""

    def test_missing_key_file_exits_nonzero_without_invoking_docker(
        self, tmp_path: Path
    ) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        fake = _write_fake_docker(
            bin_dir, stdout="", stderr="", exit_code=0, args_log=args_log
        )
        result = _run_wrapper(
            tmp_path,
            docker_cmd=fake,
            key_file=tmp_path / "does-not-exist",
            host_jsonl=tmp_path / "out.jsonl",
            host_summary=tmp_path / "out.summary.log",
        )
        assert result.returncode != 0
        assert not args_log.exists(), "docker was invoked despite a missing key file"

    def test_empty_key_file_exits_nonzero_without_invoking_docker(
        self, tmp_path: Path
    ) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        fake = _write_fake_docker(
            bin_dir, stdout="", stderr="", exit_code=0, args_log=args_log
        )
        key_file = tmp_path / "empty-key"
        key_file.write_text("")
        result = _run_wrapper(
            tmp_path,
            docker_cmd=fake,
            key_file=key_file,
            host_jsonl=tmp_path / "out.jsonl",
            host_summary=tmp_path / "out.summary.log",
        )
        assert result.returncode != 0
        assert not args_log.exists(), "docker was invoked despite an empty key file"

    @pytest.mark.skipif(
        os.geteuid() == 0,
        reason="root bypasses file permissions; guard is for non-root",
    )
    def test_unreadable_key_file_exits_nonzero_without_invoking_docker(
        self, tmp_path: Path
    ) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        fake = _write_fake_docker(
            bin_dir, stdout="", stderr="", exit_code=0, args_log=args_log
        )
        key_file = tmp_path / "unreadable-key"
        key_file.write_text("secret")
        key_file.chmod(0o000)
        try:
            result = _run_wrapper(
                tmp_path,
                docker_cmd=fake,
                key_file=key_file,
                host_jsonl=tmp_path / "out.jsonl",
                host_summary=tmp_path / "out.summary.log",
            )
            assert result.returncode != 0
            assert not args_log.exists(), (
                "docker was invoked despite an unreadable key file"
            )
        finally:
            key_file.chmod(0o600)  # let tmp_path teardown remove it


class TestJsonlPurityBranches:
    """The three JSONL-purity branches described in Plan 132 §1/§6."""

    def _key_file(self, tmp_path: Path) -> Path:
        key_file = tmp_path / "key"
        key_file.write_text("test-key-value")
        return key_file

    def test_clean_run_pure_json_lands_in_jsonl(self, tmp_path: Path) -> None:
        """(a) exit 0 + only valid JSON on stderr -> lines land in the JSONL."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        line1 = json.dumps({"run_ts": "2026-07-20T00:00:00Z", "endpoint": "e1"})
        line2 = json.dumps({"run_ts": "2026-07-20T00:00:00Z", "endpoint": "e2"})
        fake = _write_fake_docker(
            bin_dir,
            stdout="# recap probe cycle complete\n",
            stderr=f"{line1}\n{line2}\n",
            exit_code=0,
            args_log=args_log,
        )
        host_jsonl = tmp_path / "out.jsonl"
        host_summary = tmp_path / "out.summary.log"
        result = _run_wrapper(
            tmp_path,
            docker_cmd=fake,
            key_file=self._key_file(tmp_path),
            host_jsonl=host_jsonl,
            host_summary=host_summary,
        )
        assert result.returncode == 0, f"wrapper failed: {result.stderr}"
        jsonl_content = host_jsonl.read_text()
        assert line1 in jsonl_content
        assert line2 in jsonl_content
        assert "# recap probe cycle complete" in host_summary.read_text()

        # Locks the non-root invariant and that the key is passed (not its value).
        args_text = args_log.read_text()
        assert "--user" in args_text
        assert "app" in args_text
        assert "RECAP_API_KEY=" in args_text

    def test_nonjson_stderr_line_on_clean_exit_leaves_jsonl_untouched(
        self, tmp_path: Path
    ) -> None:
        """(b) exit 0 but a non-JSON line reaches stderr -> JSONL untouched,
        the buffer + a banner are routed to the wrapper's own stderr."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        good_line = json.dumps({"run_ts": "2026-07-20T00:00:00Z", "endpoint": "e1"})
        stray_warning = "WARNING: some non-JSON noise on stderr"
        fake = _write_fake_docker(
            bin_dir,
            stdout="# recap probe cycle complete\n",
            stderr=f"{good_line}\n{stray_warning}\n",
            exit_code=0,
            args_log=args_log,
        )
        host_jsonl = tmp_path / "out.jsonl"
        host_summary = tmp_path / "out.summary.log"
        result = _run_wrapper(
            tmp_path,
            docker_cmd=fake,
            key_file=self._key_file(tmp_path),
            host_jsonl=host_jsonl,
            host_summary=host_summary,
        )
        assert result.returncode != 0
        assert not host_jsonl.exists() or host_jsonl.read_text() == "", (
            f"JSONL must stay untouched on a non-JSON stderr line, got: "
            f"{host_jsonl.read_text() if host_jsonl.exists() else '<absent>'}"
        )
        assert stray_warning in result.stderr, (
            "the impure buffer must be routed to the wrapper's own stderr "
            "(the launchd log)"
        )

    def test_unterminated_nonjson_stderr_line_leaves_jsonl_untouched(
        self, tmp_path: Path
    ) -> None:
        """(b') a non-JSON FINAL line with no trailing newline must still be
        caught -- a plain `while read` in the wrapper would silently drop it
        and let it pollute the JSONL."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        stray = "WARNING: unterminated non-JSON noise"
        fake = _write_fake_docker(
            bin_dir,
            stdout="",
            stderr=stray,
            exit_code=0,
            args_log=args_log,
            stderr_newline=False,
        )
        host_jsonl = tmp_path / "out.jsonl"
        host_summary = tmp_path / "out.summary.log"
        result = _run_wrapper(
            tmp_path,
            docker_cmd=fake,
            key_file=self._key_file(tmp_path),
            host_jsonl=host_jsonl,
            host_summary=host_summary,
        )
        assert result.returncode != 0
        assert not host_jsonl.exists() or host_jsonl.read_text() == "", (
            "an unterminated non-JSON line must still leave the JSONL untouched, "
            f"got: {host_jsonl.read_text() if host_jsonl.exists() else '<absent>'}"
        )
        assert stray in result.stderr

    def test_nonzero_exec_exit_leaves_jsonl_untouched(self, tmp_path: Path) -> None:
        """(c) exec exits non-zero -> JSONL untouched, error routed to the
        launchd log."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        error_text = "Traceback (most recent call last): ImportError: recap_client"
        fake = _write_fake_docker(
            bin_dir,
            stdout="",
            stderr=error_text,
            exit_code=2,
            args_log=args_log,
        )
        host_jsonl = tmp_path / "out.jsonl"
        host_summary = tmp_path / "out.summary.log"
        result = _run_wrapper(
            tmp_path,
            docker_cmd=fake,
            key_file=self._key_file(tmp_path),
            host_jsonl=host_jsonl,
            host_summary=host_summary,
        )
        assert result.returncode != 0
        assert not host_jsonl.exists() or host_jsonl.read_text() == "", (
            f"JSONL must stay untouched on a non-zero exec exit, got: "
            f"{host_jsonl.read_text() if host_jsonl.exists() else '<absent>'}"
        )
        assert error_text in result.stderr


class TestSoundnessAgainstBuggyWrapper:
    """Proves branches (b) and (c) are the discriminating cases: they must
    FAIL when run against a stand-in for the originally-deployed wrapper
    (raw stderr piped straight into the JSONL, no purity check)."""

    def _key_file(self, tmp_path: Path) -> Path:
        key_file = tmp_path / "key"
        key_file.write_text("test-key-value")
        return key_file

    def test_buggy_wrapper_lets_nonjson_line_into_jsonl(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        good_line = json.dumps({"run_ts": "2026-07-20T00:00:00Z", "endpoint": "e1"})
        stray_warning = "WARNING: some non-JSON noise on stderr"
        fake = _write_fake_docker(
            bin_dir,
            stdout="# recap probe cycle complete\n",
            stderr=f"{good_line}\n{stray_warning}\n",
            exit_code=0,
            args_log=args_log,
        )
        host_jsonl = tmp_path / "out.jsonl"
        host_summary = tmp_path / "out.summary.log"
        buggy = _write_buggy_wrapper(tmp_path)
        result = _run_wrapper(
            tmp_path,
            docker_cmd=fake,
            key_file=self._key_file(tmp_path),
            host_jsonl=host_jsonl,
            host_summary=host_summary,
            wrapper=buggy,
        )
        assert result.returncode == 0, (
            f"buggy wrapper unexpectedly failed: {result.stderr}"
        )
        # The bug: the non-JSON line DOES land in the JSONL.
        assert stray_warning in host_jsonl.read_text()

    def test_buggy_wrapper_lets_failed_exec_into_jsonl(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        error_text = "Traceback (most recent call last): ImportError: recap_client"
        fake = _write_fake_docker(
            bin_dir,
            stdout="",
            stderr=error_text,
            exit_code=2,
            args_log=args_log,
        )
        host_jsonl = tmp_path / "out.jsonl"
        host_summary = tmp_path / "out.summary.log"
        buggy = _write_buggy_wrapper(tmp_path)
        _run_wrapper(
            tmp_path,
            docker_cmd=fake,
            key_file=self._key_file(tmp_path),
            host_jsonl=host_jsonl,
            host_summary=host_summary,
            wrapper=buggy,
        )
        # The bug: the wrapper still exits non-zero here (docker exec's exit
        # code propagates), but the error text has ALREADY landed in the
        # JSONL before that -- the discriminating defect.
        assert error_text in host_jsonl.read_text()
