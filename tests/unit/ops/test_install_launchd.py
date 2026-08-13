"""Ops tests for Plan 158 D2b/T2 — install-launchd.sh learns the launchd
"domain" concept (per-user "agent" vs system-wide "daemon").

These are shell-script tests, not Python unit tests in the usual sense — we
shell out via subprocess to exercise the actual installer, mirroring
tests/unit/ops/test_launchd_prune_docker.py and test_recap_probe_wrapper.py.

Scope, deliberately narrow: this file only exercises `--dry-run` paths (pure
preview, never shells out to `plutil`/`launchctl`) plus the root-refusal
check for an explicit `--label` daemon request (which also never reaches
`plutil`/`launchctl` — the check fires first). That keeps every test
portable to ubuntu-latest CI, which has neither binary. A REAL (non
dry-run) agent/daemon install is a live-host concern exercised by the T3/T5
runbooks, not by this automated suite.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts" / "launchd"
_INSTALL_SCRIPT = _SCRIPTS_DIR / "install-launchd.sh"

_WATCHDOG_LABEL = "ch.hydrosolutions.sapphire-watchdog"
_STARTER_LABEL = "ch.hydrosolutions.sapphire"
_PRUNE_LABEL = "ch.hydrosolutions.sapphire-docker-prune"


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
