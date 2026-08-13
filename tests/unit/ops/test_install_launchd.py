"""Ops tests for Plan 158 D2b/T2 — install-launchd.sh learns the launchd
"domain" concept (per-user "agent" vs system-wide "daemon").

These are shell-script tests, not Python unit tests in the usual sense — we
shell out via subprocess to exercise the actual installer, mirroring
tests/unit/ops/test_launchd_prune_docker.py and test_recap_probe_wrapper.py.

Most of this file exercises `--dry-run` paths (pure preview, never shells out
to `plutil`/`launchctl`) plus the root-refusal check for an explicit
`--label` daemon request (which also never reaches `plutil`/`launchctl` —
the check fires first). Those tests are portable to ubuntu-latest CI, which
has neither binary.

A handful of classes below (`TestStaleGuiBootoutFailureAbortsSystemBootstrap`,
`TestUnprivilegedFullSweepSkipsDaemonAndContinues`) instead run the REAL
(non-dry-run) installer with `id`, `plutil`, and `launchctl` faked via a
PATH-injected stub bin dir — mirroring the `docker` stub pattern in
test_launchd_prune_docker.py::_write_fake_docker — so they too never touch a
real launchd domain or plutil, and stay portable to CI.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts" / "launchd"
_INSTALL_SCRIPT = _SCRIPTS_DIR / "install-launchd.sh"

_WATCHDOG_LABEL = "ch.hydrosolutions.sapphire-watchdog"
_WATCHDOG_PLIST = f"{_WATCHDOG_LABEL}.plist"
_STARTER_LABEL = "ch.hydrosolutions.sapphire"
_PRUNE_LABEL = "ch.hydrosolutions.sapphire-docker-prune"


def _write_fake_bin(bin_dir: Path, name: str, body: str) -> Path:
    fake = bin_dir / name
    fake.write_text(f"#!/bin/bash\n{body}\n")
    fake.chmod(0o755)
    return fake


def _run_install(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the real installer with HOME pointed at an isolated tmp dir,
    so even a bug that skips the dry-run early-return can't touch the real
    ~/Library/LaunchAgents."""
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        ["bash", str(_INSTALL_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(home),
    )


class TestDryRunWritesNothing:
    def test_exit_zero_and_no_files_created(self, tmp_path: Path) -> None:
        result = _run_install(tmp_path, "--dry-run")
        assert result.returncode == 0, result.stderr
        home = tmp_path / "home"
        assert not (home / "Library" / "LaunchAgents").exists(), (
            "dry-run must write nothing, but LaunchAgents/ was created"
        )

    def test_prints_daemon_path_and_privileged_step(self, tmp_path: Path) -> None:
        result = _run_install(tmp_path, "--dry-run")
        combined = result.stdout + result.stderr
        assert "/Library/LaunchDaemons/ch.hydrosolutions.sapphire-watchdog.plist" in (
            combined
        ), combined
        assert "sudo" in combined.lower(), (
            f"dry-run must surface the privileged (sudo) step: {combined}"
        )

    def test_prints_agent_path_for_starter_and_prune(self, tmp_path: Path) -> None:
        # D2/D5: only the watchdog moves to "daemon" in this plan; the
        # stack-starter and prune jobs stay "agent" (their conversion is a
        # separate host cutover, T5).
        result = _run_install(tmp_path, "--dry-run")
        combined = result.stdout + result.stderr
        assert "gui/" in combined
        assert "LaunchAgent" in combined
        assert "/Library/LaunchDaemons/ch.hydrosolutions.sapphire.plist" not in (
            combined
        )
        assert (
            "/Library/LaunchDaemons/ch.hydrosolutions.sapphire-docker-prune.plist"
            not in combined
        )

    def test_mentions_stale_domain_bootout_for_daemon_entry(
        self, tmp_path: Path
    ) -> None:
        result = _run_install(tmp_path, "--dry-run")
        combined = (result.stdout + result.stderr).lower()
        assert "stale" in combined and "gui/" in combined, (
            f"dry-run must describe the stale gui/<uid> bootout before a "
            f"system bootstrap: {combined}"
        )


class TestLabelFilter:
    def test_label_restricts_to_single_plist(self, tmp_path: Path) -> None:
        result = _run_install(tmp_path, "--dry-run", "--label", _WATCHDOG_LABEL)
        assert result.returncode == 0, result.stderr
        combined = result.stdout + result.stderr
        assert _WATCHDOG_LABEL in combined
        assert _STARTER_LABEL not in combined.replace(_WATCHDOG_LABEL, "")
        assert _PRUNE_LABEL not in combined

    def test_unknown_label_errors(self, tmp_path: Path) -> None:
        result = _run_install(
            tmp_path, "--dry-run", "--label", "ch.hydrosolutions.does-not-exist"
        )
        assert result.returncode != 0
        assert "ch.hydrosolutions.does-not-exist" in (result.stdout + result.stderr)

    def test_label_requires_a_value(self, tmp_path: Path) -> None:
        result = _run_install(tmp_path, "--dry-run", "--label")
        assert result.returncode != 0


class TestDaemonRefusesWithoutRoot:
    """The installer must REFUSE a daemon-domain install without root, never
    escalate privileges itself. This check must fire before any
    plutil/launchctl call (so the test is portable to ubuntu-latest CI,
    which has neither binary)."""

    def test_explicit_daemon_label_without_root_exits_nonzero(
        self, tmp_path: Path
    ) -> None:
        if os.geteuid() == 0:
            pytest.skip("test invalid when the test runner itself is root")
        result = _run_install(tmp_path, "--label", _WATCHDOG_LABEL)
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "sudo" in combined.lower()
        assert "root" in combined.lower()
        # Confirm it refused, not attempted-and-failed: no LaunchAgents dir
        # should exist either (nothing else ran).
        home = tmp_path / "home"
        assert not (home / "Library" / "LaunchAgents").exists()


class TestLabelEqualsRejectsEmptyValue:
    """`--label=` (equals-form, empty value) must be rejected exactly like
    the two-arg form `--label` with no following value — an empty
    ONLY_LABEL silently disables filtering and turns into an all-label
    install/preview."""

    def test_label_equals_empty_errors(self, tmp_path: Path) -> None:
        result = _run_install(tmp_path, "--dry-run", "--label=")
        assert result.returncode != 0, (
            f"--label= (empty) must be rejected, not treated as 'no filter'; "
            f"got exit 0: {result.stdout}{result.stderr}"
        )

    def test_label_equals_empty_does_not_preview_all_labels(
        self, tmp_path: Path
    ) -> None:
        result = _run_install(tmp_path, "--dry-run", "--label=")
        combined = result.stdout + result.stderr
        # Buggy behaviour: exits 0 and previews all three jobs.
        assert not (
            result.returncode == 0
            and _STARTER_LABEL in combined
            and _WATCHDOG_LABEL in combined
            and _PRUNE_LABEL in combined
        ), f"--label= silently became an all-label install: {combined}"


class TestRootFullSweepRejected:
    """Major fix: a root FULL sweep (no --label) must be refused, never
    proceed. Under sudo, $HOME resolves to root's home while the agent
    plists get bootstrapped into gui/<SUDO_UID> (the real operator's
    session) — they would load once but never survive the next
    login/reboot, since launchd rescans the REAL operator's
    ~/Library/LaunchAgents, not root's."""

    def _run_as_root(
        self, tmp_path: Path, *args: str, sudo_uid: str | None = "501"
    ) -> subprocess.CompletedProcess[str]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_bin(bin_dir, "id", "echo 0")
        home = tmp_path / "home"
        home.mkdir()
        env = {
            **os.environ,
            "HOME": str(home),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
        }
        if sudo_uid is not None:
            env["SUDO_UID"] = sudo_uid
        else:
            env.pop("SUDO_UID", None)
        return subprocess.run(
            ["bash", str(_INSTALL_SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(home),
        )

    def test_root_full_sweep_exits_nonzero(self, tmp_path: Path) -> None:
        result = self._run_as_root(tmp_path)
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "full sweep" in combined.lower()

    def test_root_full_sweep_writes_nothing(self, tmp_path: Path) -> None:
        result = self._run_as_root(tmp_path)
        assert result.returncode != 0, result.stdout + result.stderr
        home = tmp_path / "home"
        assert not (home / "Library" / "LaunchAgents").exists()

    def test_root_full_sweep_rejected_even_under_dry_run(self, tmp_path: Path) -> None:
        # Dry-run must not become a loophole for the misleading preview —
        # reject before even the preview is printed.
        result = self._run_as_root(tmp_path, "--dry-run")
        assert result.returncode != 0
        assert "full sweep" in (result.stdout + result.stderr).lower()

    def test_root_with_explicit_label_still_allowed(self, tmp_path: Path) -> None:
        # Control case: root + --label (the documented privileged step) is
        # NOT the full-sweep case and must not be rejected by this check.
        result = self._run_as_root(tmp_path, "--dry-run", "--label", _WATCHDOG_LABEL)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "full sweep" not in (result.stdout + result.stderr).lower()


class TestRootAgentLabelRejected:
    """Plan 158 T1c F2 (major): `sudo ... --label <agent-domain-label>` must
    also be refused, not just a root full sweep. The root guard above only
    covers "no --label" — an explicit AGENT target under sudo still resolves
    AGENTS_DIR against root's $HOME (not the real operator's), so it would
    land in /var/root/Library/LaunchAgents while being bootstrapped into
    gui/$SUDO_UID: it loads once, then vanishes at the next login/reboot."""

    def _run_as_root(
        self, tmp_path: Path, *args: str, sudo_uid: str | None = "501"
    ) -> subprocess.CompletedProcess[str]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_bin(bin_dir, "id", "echo 0")
        home = tmp_path / "home"
        home.mkdir()
        env = {
            **os.environ,
            "HOME": str(home),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
        }
        if sudo_uid is not None:
            env["SUDO_UID"] = sudo_uid
        return subprocess.run(
            ["bash", str(_INSTALL_SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(home),
        )

    def test_root_plus_starter_label_rejected(self, tmp_path: Path) -> None:
        result = self._run_as_root(tmp_path, "--dry-run", "--label", _STARTER_LABEL)
        assert result.returncode != 0, result.stdout + result.stderr
        combined = result.stdout + result.stderr
        assert "sudo" in combined.lower()
        assert "root" in combined.lower()

    def test_root_plus_prune_label_rejected(self, tmp_path: Path) -> None:
        result = self._run_as_root(tmp_path, "--dry-run", "--label", _PRUNE_LABEL)
        assert result.returncode != 0, result.stdout + result.stderr
        combined = result.stdout + result.stderr
        assert "sudo" in combined.lower()
        assert "root" in combined.lower()

    def test_rejection_writes_nothing(self, tmp_path: Path) -> None:
        result = self._run_as_root(tmp_path, "--label", _STARTER_LABEL)
        assert result.returncode != 0
        home = tmp_path / "home"
        assert not (home / "Library" / "LaunchAgents").exists()

    def test_root_plus_daemon_label_still_allowed(self, tmp_path: Path) -> None:
        # Control case: the daemon label is the documented privileged path
        # and must NOT be caught by this new guard.
        result = self._run_as_root(tmp_path, "--dry-run", "--label", _WATCHDOG_LABEL)
        assert result.returncode == 0, result.stdout + result.stderr


class TestStaleGuiBootoutFailureAbortsSystemBootstrap:
    """Blocker fix: install_daemon() must ABORT — never proceed to
    `launchctl bootstrap system ...` — when it cannot positively confirm the
    stale gui/<uid>/<label> registration is gone. Both is exercised: the
    bootout call itself failing, and bootout claiming success while the
    registration is still visible afterwards."""

    _UID = "501"

    def _fake_bins(self, tmp_path: Path, *, launchctl_body: str) -> Path:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        # `id -u` -> 0 (root) unconditionally; the script only ever calls it
        # as `id -u`, so no need to disambiguate other invocations.
        _write_fake_bin(bin_dir, "id", "echo 0")
        _write_fake_bin(bin_dir, "plutil", "exit 0")
        log_path = tmp_path / "launchctl.log"
        log_path.write_text("")
        _write_fake_bin(
            bin_dir,
            "launchctl",
            f'printf \'%s\\n\' "$*" >> "{log_path}"\n{launchctl_body}\n',
        )
        return bin_dir

    def _run(
        self, tmp_path: Path, *, launchctl_body: str
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        bin_dir = self._fake_bins(tmp_path, launchctl_body=launchctl_body)
        home = tmp_path / "home"
        home.mkdir()
        env = {
            **os.environ,
            "HOME": str(home),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "SUDO_UID": self._UID,
        }
        result = subprocess.run(
            ["bash", str(_INSTALL_SCRIPT), "--label", _WATCHDOG_LABEL],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(home),
        )
        return result, tmp_path / "launchctl.log"

    @property
    def _gui_target(self) -> str:
        return f"gui/{self._UID}/{_WATCHDOG_LABEL}"

    def test_bootout_command_failure_aborts_before_system_bootstrap(
        self, tmp_path: Path
    ) -> None:
        # gui print always finds it (stale); bootout itself fails.
        target = self._gui_target
        body = (
            f'if [[ "$1" == "print" && "$2" == "{target}" ]]; then exit 0; fi\n'
            'if [[ "$1" == "bootout" ]]; then exit 1; fi\n'
            "exit 0"
        )
        result, log_path = self._run(tmp_path, launchctl_body=body)

        assert result.returncode != 0, result.stdout + result.stderr
        log = log_path.read_text()
        assert "bootstrap system" not in log, log
        combined = result.stdout + result.stderr
        assert "ERROR" in combined and "boot out" in combined.lower()
        # It must not have reached the daemon-dir copy either.
        assert not (tmp_path / "home" / "Library" / "LaunchDaemons").exists()

    def test_bootout_reports_success_but_still_registered_aborts(
        self, tmp_path: Path
    ) -> None:
        # gui print ALWAYS reports found (before AND after bootout), even
        # though bootout itself claims success (exit 0) — the raced /
        # flaky-removal case the plain exit-code check alone would miss.
        target = self._gui_target
        body = (
            f'if [[ "$1" == "print" && "$2" == "{target}" ]]; then exit 0; fi\nexit 0'
        )
        result, log_path = self._run(tmp_path, launchctl_body=body)

        assert result.returncode != 0, result.stdout + result.stderr
        log = log_path.read_text()
        assert "bootstrap system" not in log, log
        # Confirm the bootout WAS attempted (so this is the verify-after
        # branch, not some earlier short-circuit).
        assert f"bootout {target}" in log, log
        combined = result.stdout + result.stderr
        assert "still registered" in combined.lower()

    def test_clean_bootout_proceeds_past_the_check(self, tmp_path: Path) -> None:
        # Control case: bootout succeeds AND the post-check confirms it is
        # gone -> the installer must proceed past the stale-gui check
        # (proves the abort in the two tests above is conditional, not
        # unconditional). It cannot run to full completion here — writing
        # to the real /Library/LaunchDaemons needs real root, which the
        # test process does not have — so "proceeded" is evidenced by
        # reaching the copy step / a permission error there, NOT by our own
        # abort message.
        target = self._gui_target
        bootout_marker = tmp_path / "bootout_done"
        body = (
            f'if [[ "$1" == "print" && "$2" == "{target}" ]]; then\n'
            f'    if [[ -f "{bootout_marker}" ]]; then exit 1; else exit 0; fi\n'
            "fi\n"
            f'if [[ "$1" == "bootout" ]]; then touch "{bootout_marker}"; exit 0; fi\n'
            "exit 0"
        )
        result, log_path = self._run(tmp_path, launchctl_body=body)

        combined = result.stdout + result.stderr
        assert "still registered" not in combined.lower(), combined
        assert "failed to boot out" not in combined.lower(), combined
        assert f"copying {_WATCHDOG_PLIST}" in combined, combined
        log = log_path.read_text()
        assert f"bootout gui/{self._UID}/{_WATCHDOG_LABEL}" in log, log


class TestDaemonInstallRemovesLegacyAgentPlist:
    """Plan 158 T1c F1 (blocker): a successful daemon install must remove
    (not merely bootout-from-the-current-session) any leftover agent-domain
    plist FILE at AGENTS_DIR — launchd re-scans that directory at every
    login, so a leftover file would silently reload as a duplicate
    LaunchAgent racing the new daemon on the same state file. On a FAILED
    daemon bootstrap, the legacy plist must be restored (rollback) so a
    half-migration never leaves the host with no watchdog at all."""

    _UID = "501"

    def _fake_bins(
        self, tmp_path: Path, *, launchctl_body: str
    ) -> tuple[Path, Path, Path]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_bin(bin_dir, "id", "echo 0")
        _write_fake_bin(bin_dir, "plutil", "exit 0")
        chown_log = tmp_path / "chown.log"
        chown_log.write_text("")
        _write_fake_bin(
            bin_dir, "chown", f'printf \'%s\\n\' "$*" >> "{chown_log}"\nexit 0'
        )
        chmod_log = tmp_path / "chmod.log"
        chmod_log.write_text("")
        _write_fake_bin(
            bin_dir, "chmod", f'printf \'%s\\n\' "$*" >> "{chmod_log}"\nexit 0'
        )

        cp_log = tmp_path / "cp.log"
        cp_log.write_text("")
        cp_body = (
            f'printf \'%s\\n\' "$*" >> "{cp_log}"\n'
            'case "$*" in\n'
            "  */Library/LaunchDaemons/*) exit 0 ;;\n"
            '  *) exec /bin/cp "$@" ;;\n'
            "esac\n"
        )
        _write_fake_bin(bin_dir, "cp", cp_body)

        log_path = tmp_path / "launchctl.log"
        log_path.write_text("")
        _write_fake_bin(
            bin_dir,
            "launchctl",
            f'printf \'%s\\n\' "$*" >> "{log_path}"\n{launchctl_body}\n',
        )
        return bin_dir, log_path, cp_log

    def _run(
        self, tmp_path: Path, *, launchctl_body: str, legacy_content: str
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
        bin_dir, log_path, cp_log = self._fake_bins(
            tmp_path, launchctl_body=launchctl_body
        )
        home = tmp_path / "home"
        agents_dir = home / "Library" / "LaunchAgents"
        agents_dir.mkdir(parents=True)
        legacy_plist = agents_dir / _WATCHDOG_PLIST
        legacy_plist.write_text(legacy_content)

        env = {
            **os.environ,
            "HOME": str(home),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "SUDO_UID": self._UID,
        }
        result = subprocess.run(
            ["bash", str(_INSTALL_SCRIPT), "--label", _WATCHDOG_LABEL],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(home),
        )
        return result, legacy_plist, log_path, cp_log

    # Nothing registered anywhere -> every stale-gui/stale-system bootout
    # branch is skipped; bootstrap/enable succeed.
    _CLEAN_SUCCESS = 'if [[ "$1" == "print" ]]; then exit 1; fi\nexit 0'

    def test_successful_install_removes_legacy_plist_file(self, tmp_path: Path) -> None:
        result, legacy_plist, _log_path, _cp_log = self._run(
            tmp_path,
            launchctl_body=self._CLEAN_SUCCESS,
            legacy_content="LEGACY-MARKER",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not legacy_plist.exists(), (
            "legacy agent plist must be removed after a successful daemon "
            "install, or it silently reloads as a duplicate LaunchAgent at "
            "the next login"
        )

    def test_successful_install_sets_root_wheel_ownership_and_0644_mode(
        self, tmp_path: Path
    ) -> None:
        result, _legacy_plist, _log_path, _cp_log = self._run(
            tmp_path,
            launchctl_body=self._CLEAN_SUCCESS,
            legacy_content="LEGACY-MARKER",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        chown_calls = (tmp_path / "chown.log").read_text().splitlines()
        chmod_calls = (tmp_path / "chmod.log").read_text().splitlines()
        assert any(
            "root:wheel" in c and "/Library/LaunchDaemons/" in c for c in chown_calls
        ), chown_calls
        assert any(
            c.startswith("644 ") and "/Library/LaunchDaemons/" in c for c in chmod_calls
        ), chmod_calls

    def test_successful_install_bootstraps_system_domain(self, tmp_path: Path) -> None:
        result, _legacy_plist, log_path, _cp_log = self._run(
            tmp_path,
            launchctl_body=self._CLEAN_SUCCESS,
            legacy_content="LEGACY-MARKER",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        log = log_path.read_text()
        assert "bootstrap system" in log, log
        assert "enable system" in log, log

    # Everything unregistered, but `launchctl bootstrap system ...` itself
    # fails (e.g. a malformed plist rejected at bootstrap time).
    _BOOTSTRAP_FAILS = (
        'if [[ "$1" == "print" ]]; then exit 1; fi\n'
        'if [[ "$1" == "bootstrap" && "$2" == "system" ]]; then exit 1; fi\n'
        "exit 0"
    )

    def test_failed_bootstrap_restores_legacy_plist(self, tmp_path: Path) -> None:
        result, legacy_plist, _log_path, _cp_log = self._run(
            tmp_path,
            launchctl_body=self._BOOTSTRAP_FAILS,
            legacy_content="LEGACY-MARKER",
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert legacy_plist.exists(), (
            "a FAILED daemon bootstrap must roll back to the legacy agent "
            "plist, or the host is left with NO watchdog running at all"
        )
        assert legacy_plist.read_text() == "LEGACY-MARKER"

    def test_failed_bootstrap_reloads_gui_agent(self, tmp_path: Path) -> None:
        result, _legacy_plist, log_path, _cp_log = self._run(
            tmp_path,
            launchctl_body=self._BOOTSTRAP_FAILS,
            legacy_content="LEGACY-MARKER",
        )
        assert result.returncode != 0, result.stdout + result.stderr
        log = log_path.read_text()
        gui_bootstraps = [
            line for line in log.splitlines() if line.startswith("bootstrap gui/")
        ]
        assert len(gui_bootstraps) >= 1, (
            f"rollback must re-bootstrap the gui/<uid> agent: {log}"
        )


class TestUnprivilegedFullSweepSkipsDaemonAndContinues:
    """D2b's acceptance contract: a full (no --label) unprivileged sweep
    WARNs and skips the daemon-domain label (return 0, keep going) — the
    behaviour scripts/bootstrap-mac-mini.sh step 11 relies on for every
    unprivileged bootstrap run. This is the previously-untested sibling of
    TestDaemonRefusesWithoutRoot (which only covers the explicit --label
    hard-fail arm)."""

    def test_unprivileged_sweep_warns_skips_daemon_installs_agents(
        self, tmp_path: Path
    ) -> None:
        if os.geteuid() == 0:
            pytest.skip("test invalid when the test runner itself is root")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_bin(bin_dir, "plutil", "exit 0")
        log_path = tmp_path / "launchctl.log"
        log_path.write_text("")
        # Nothing is pre-registered anywhere -> `print` always "not found";
        # every other call (bootstrap, enable) is a safe no-op.
        _write_fake_bin(
            bin_dir,
            "launchctl",
            f'printf \'%s\\n\' "$*" >> "{log_path}"\n'
            'if [[ "$1" == "print" ]]; then exit 1; fi\n'
            "exit 0",
        )
        home = tmp_path / "home"
        home.mkdir()
        env = {
            **os.environ,
            "HOME": str(home),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
        }
        env.pop("SUDO_UID", None)

        result = subprocess.run(
            ["bash", str(_INSTALL_SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(home),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        combined = result.stdout + result.stderr
        assert "WARNING" in combined and "requires root" in combined
        assert _WATCHDOG_LABEL in combined

        log = log_path.read_text()
        # The sweep continued PAST the skipped daemon label: both
        # agent-domain plists were actually bootstrapped (not just
        # skipped/no-op'd silently).
        real_uid = os.getuid()
        assert f"bootstrap gui/{real_uid}" in log, log
        assert log.count("bootstrap gui/") == 2, log
        assert "bootstrap system" not in log, log


class TestPlistsArrayUnchanged:
    """Static sanity check mirroring test_launchd_prune_docker.py's
    TestInstallLaunchdPruneRegistration — the domain rework must not drop
    any existing PLISTS entry."""

    def test_all_three_labels_present_in_source(self) -> None:
        content = _INSTALL_SCRIPT.read_text()
        for plist in (
            "ch.hydrosolutions.sapphire.plist",
            "ch.hydrosolutions.sapphire-watchdog.plist",
            "ch.hydrosolutions.sapphire-docker-prune.plist",
        ):
            assert plist in content, f"{plist} missing from install-launchd.sh"
