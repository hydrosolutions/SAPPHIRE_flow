"""Ops tests for Plan 158 (fixer round) — bootstrap-mac-mini.sh --uninstall.

These are shell-script tests, not Python unit tests in the usual sense — we
shell out via subprocess to exercise the actual `--uninstall` path, mirroring
tests/unit/ops/test_install_launchd.py and test_launchd_prune_docker.py.
`launchctl` and `docker` are faked via a PATH-injected stub bin dir so the
tests never touch a real launchd domain or the Docker daemon, and are
portable to ubuntu-latest CI (which has neither binary).

Scope: the reviewer-flagged uninstall gaps —
  1. SUDO_UID (not root's own uid) must resolve the gui/<uid> domain.
  2. Registration state must be checked via `launchctl print`, not
     plist-file existence.
  3. A system-domain (LaunchDaemon) removal must be refused (not silently
     skipped) when the invoking process isn't actually root.
  4. The script must exit nonzero — not print "uninstall complete" — when a
     label is still registered after the bootout attempt.
  5. --dry-run must never invoke a real bootout and must skip the
     post-bootout verification (nothing was actually done).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts"
_BOOTSTRAP_SCRIPT = _SCRIPTS_DIR / "bootstrap-mac-mini.sh"

_LABELS = (
    "ch.hydrosolutions.sapphire",
    "ch.hydrosolutions.sapphire-watchdog",
    "ch.hydrosolutions.sapphire-docker-prune",
)


def _write_fake_bin(bin_dir: Path, name: str, body: str) -> Path:
    fake = bin_dir / name
    fake.write_text(f"#!/bin/bash\n{body}\n")
    fake.chmod(0o755)
    return fake


def _run_uninstall(
    tmp_path: Path,
    *,
    launchctl_body: str,
    extra_args: tuple[str, ...] = (),
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run `bootstrap-mac-mini.sh --uninstall` with launchctl/docker faked.

    `launchctl_body` is bash logic (receives "$@" as the launchctl
    subcommand+target) run AFTER every invocation is appended to
    launchctl.log, one line per call.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "launchctl.log"
    log_path.write_text("")

    _write_fake_bin(
        bin_dir,
        "launchctl",
        f'printf \'%s\\n\' "$*" >> "{log_path}"\n{launchctl_body}\n',
    )
    _write_fake_bin(bin_dir, "docker", "exit 0")

    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    env.pop("SUDO_UID", None)
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        ["bash", str(_BOOTSTRAP_SCRIPT), "--uninstall", *extra_args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    return result, log_path


# `launchctl print` exits 1 (not found) for every domain/label — the
# freshly-installed-nothing case.
_NOTHING_REGISTERED = 'if [[ "$1" == "print" ]]; then exit 1; fi\nexit 0'


class TestUninstallNothingRegistered:
    def test_exits_zero_and_attempts_no_bootout(self, tmp_path: Path) -> None:
        result, log_path = _run_uninstall(tmp_path, launchctl_body=_NOTHING_REGISTERED)
        assert result.returncode == 0, result.stderr
        assert "bootout" not in log_path.read_text()
        combined = result.stdout + result.stderr
        for label in _LABELS:
            assert "gui/" in combined and label in combined

    def test_reports_uninstall_complete(self, tmp_path: Path) -> None:
        result, _ = _run_uninstall(tmp_path, launchctl_body=_NOTHING_REGISTERED)
        assert "uninstall complete" in (result.stdout + result.stderr)


class TestUninstallUsesSudoUidForGuiDomain:
    def test_gui_domain_check_uses_sudo_uid_not_real_uid(self, tmp_path: Path) -> None:
        result, log_path = _run_uninstall(
            tmp_path,
            launchctl_body=_NOTHING_REGISTERED,
            extra_env={"SUDO_UID": "501"},
        )
        assert result.returncode == 0, result.stderr
        log = log_path.read_text()
        for label in _LABELS:
            assert f"print gui/501/{label}" in log, log
        real_uid = os.getuid()
        if real_uid != 501:
            assert f"gui/{real_uid}/" not in log, log


class TestUninstallRequiresRootForSystemDomain:
    """A system/<label> registration must not be silently left in place —
    and must not be silently claimed removed — when not running as root."""

    _SYSTEM_ONLY_REGISTERED = (
        'if [[ "$1" == "print" && "$2" == system/* ]]; then exit 0; fi\n'
        'if [[ "$1" == "print" ]]; then exit 1; fi\n'
        "exit 0"
    )

    def test_exits_nonzero_and_does_not_bootout_system_domain(
        self, tmp_path: Path
    ) -> None:
        if os.geteuid() == 0:
            pytest.skip("test invalid when the test runner itself is root")
        result, log_path = _run_uninstall(
            tmp_path, launchctl_body=self._SYSTEM_ONLY_REGISTERED
        )
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "root" in combined.lower()
        assert "sudo" in combined.lower()
        assert "bootout system" not in log_path.read_text()

    def test_does_not_claim_uninstall_complete(self, tmp_path: Path) -> None:
        if os.geteuid() == 0:
            pytest.skip("test invalid when the test runner itself is root")
        result, _ = _run_uninstall(
            tmp_path, launchctl_body=self._SYSTEM_ONLY_REGISTERED
        )
        assert "uninstall complete" not in (result.stdout + result.stderr)


class TestUninstallExitsNonzeroWhenStillLoaded:
    """A gui-domain label that is STILL registered after the bootout attempt
    (bootout "succeeded" but the registration persists, or bootout itself
    failed) must fail the script, not report success. This does not require
    root: gui-domain removal never needed elevated privilege."""

    _GUI_STAYS_REGISTERED = (
        'if [[ "$1" == "print" && "$2" == gui/* ]]; then exit 0; fi\n'
        'if [[ "$1" == "print" ]]; then exit 1; fi\n'
        "exit 0"
    )

    def test_exits_nonzero_and_reports_still_registered(self, tmp_path: Path) -> None:
        result, log_path = _run_uninstall(
            tmp_path, launchctl_body=self._GUI_STAYS_REGISTERED
        )
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "still registered after bootout" in combined
        assert "bootout gui/" in log_path.read_text()

    def test_does_not_claim_uninstall_complete(self, tmp_path: Path) -> None:
        result, _ = _run_uninstall(tmp_path, launchctl_body=self._GUI_STAYS_REGISTERED)
        assert "uninstall complete" not in (result.stdout + result.stderr)


class TestUninstallDryRun:
    """--dry-run must never actually bootout anything and must skip the
    post-bootout verification (there is nothing real to verify)."""

    # Everything reports "registered" — if dry-run performed a real bootout
    # + verification, this would fail the still-registered check. It must
    # not, because `run launchctl bootout ...` only prints under --dry-run.
    _EVERYTHING_REGISTERED = 'if [[ "$1" == "print" ]]; then exit 0; fi\nexit 0'

    def test_never_calls_real_bootout(self, tmp_path: Path) -> None:
        result, log_path = _run_uninstall(
            tmp_path,
            launchctl_body=self._EVERYTHING_REGISTERED,
            extra_args=("--dry-run",),
        )
        assert result.returncode == 0, result.stderr
        assert "bootout" not in log_path.read_text()

    def test_prints_would_run_bootout(self, tmp_path: Path) -> None:
        result, _ = _run_uninstall(
            tmp_path,
            launchctl_body=self._EVERYTHING_REGISTERED,
            extra_args=("--dry-run",),
        )
        combined = result.stdout + result.stderr
        assert "would run:" in combined
        assert "launchctl bootout" in combined
