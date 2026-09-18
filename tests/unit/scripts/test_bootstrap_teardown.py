"""`teardown_stack` (bootstrap-mac-mini.sh) must report a failed teardown.

Shell-script tests, mirroring the source-and-call convention in
tests/unit/scripts/test_bootstrap_backup_target_verified.py — the other test
module for this same script: it returns early when sourced, so
`teardown_stack` is defined above that guard and is called directly here with
a fake `docker`/`launchctl`/`id` trio first on PATH.

The defect being locked: the uninstall path used to run `docker compose down
|| true` and then print "uninstall complete" unconditionally, so a teardown
that left containers running reported success. Two behaviours are required —
a non-zero `down` is a failure, and an exit-0 `down` is NOT by itself proof
the containers are gone. A FAILED verification must read as UNKNOWN, never as
"nothing running"; that is the silent-success bug already fixed once in
prune-docker.sh (tests/unit/ops/test_launchd_prune_docker.py).

The same two rules apply to launchd, and that half had its own holes:

* only three of the five ch.hydrosolutions.* LaunchAgents this repo ships
  were booted out, while the summary told the operator to delete the whole
  `ch.hydrosolutions.*.plist` glob — stranding the recap-probe and
  nepal-forcing jobs loaded with no plist to boot them out from;
* verification was gated on the plist existing, so a job that was loaded but
  whose plist had already been removed was counted as gone.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "bootstrap-mac-mini.sh"

# Every label the repo ships a plist for. install-launchd.sh installs the
# first three; the last two are installed by hand from their runbooks. All
# five run on the mini, so all five must be torn down.
SHIPPED_LABELS = (
    "ch.hydrosolutions.sapphire",
    "ch.hydrosolutions.sapphire-watchdog",
    "ch.hydrosolutions.sapphire-docker-prune",
    "ch.hydrosolutions.sapphire-recap-probe",
    "ch.hydrosolutions.sapphire-nepal-forcing",
)

_FAKE_DOCKER = """#!/bin/bash
# Validates the REAL call shape before answering:
#   compose -f <repo>/docker-compose.yml -f <repo>/docker-compose.macmini.yml \\
#           (down | ps -q -a)
# A crude token-matcher would keep passing after the macmini overlay was
# dropped from compose_args, or after the compose path was pointed at a file
# that does not exist, so the file BASENAMES and their existence are both
# checked -- not merely "at least one -f".
[ -n "${DOCKER_LOG:-}" ] && printf '%s\\n' "$*" >> "${DOCKER_LOG}"
if [ "$1" != "compose" ]; then
  echo "fake docker: expected 'compose', got '$1'" >&2; exit 90
fi
shift
files=""
while [ "${1:-}" = "-f" ]; do
  [ -n "${2:-}" ] || { echo "fake docker: -f with no file" >&2; exit 91; }
  [ -f "$2" ] || { echo "fake docker: compose file missing: $2" >&2; exit 95; }
  files="${files}${files:+ }$(basename "$2")"
  shift 2
done
expected="docker-compose.yml docker-compose.macmini.yml"
if [ "$files" != "$expected" ]; then
  echo "fake docker: compose files [${files}] != [${expected}]" >&2; exit 92
fi
case "$1" in
  down) exit ${DOWN_RC:-0} ;;
  ps)   { [ "$2" = "-q" ] && [ "$3" = "-a" ]; } || {
          echo "fake docker: expected 'ps -q -a', got 'ps $2 $3'" >&2; exit 93; }
        if [ "${PS_RC:-0}" -ne 0 ]; then
          echo "${PS_ERR:-fake docker: ps exploded}" >&2; exit "${PS_RC}"
        fi
        printf '%s' "${PS_OUT:-}"; exit 0 ;;
  *)    echo "fake docker: unexpected subcommand '$1'" >&2; exit 94 ;;
esac
"""

# `print` exiting 0 means the label STILL resolves -- still loaded.
# `list` answers the residual sweep. Every invocation is appended to
# $LAUNCHCTL_LOG when set, so a test can assert what was (not) run.
_FAKE_LAUNCHCTL = """#!/bin/bash
[ -n "${LAUNCHCTL_LOG:-}" ] && printf '%s\\n' "$*" >> "${LAUNCHCTL_LOG}"
case "$1" in
  print)   exit ${PRINT_RC:-1} ;;
  list)    printf '%s' "${LIST_OUT:-}"; exit ${LIST_RC:-0} ;;
  bootout) [ -z "${BOOTOUT_ERR:-}" ] && exit 0
           echo "${BOOTOUT_ERR}" >&2; exit 5 ;;
  *)       exit 0 ;;
esac
"""


def _fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, body in (
        ("docker", _FAKE_DOCKER),
        ("launchctl", _FAKE_LAUNCHCTL),
        ("id", "#!/bin/bash\necho 501\n"),
    ):
        exe = bin_dir / name
        exe.write_text(body)
        exe.chmod(0o755)
    return bin_dir


def _run_teardown(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    bin_dir = _fake_bin(tmp_path)
    script = (
        f'source "{SCRIPT}"\n'
        "DRY_RUN=0\n"
        # the script sets -euo pipefail, which is inherited on source: a bare
        # call would kill this shell before the status could be printed.
        "if teardown_stack; then rc=0; else rc=$?; fi\n"
        'echo "RC=$rc"\n'
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), **env},
    )


class TestTeardownStackReportsFailure:
    def test_reports_failure_when_compose_down_exits_non_zero(
        self, tmp_path: Path
    ) -> None:
        r = _run_teardown(tmp_path, DOWN_RC="1")
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "docker compose down failed" in r.stderr

    def test_reports_failure_when_containers_survive_a_clean_down(
        self, tmp_path: Path
    ) -> None:
        """Exit 0 from `down` is not proof. This is the case the old
        `|| true` reported as a successful uninstall."""
        r = _run_teardown(tmp_path, DOWN_RC="0", PS_OUT="abc123\n")
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "containers still running" in r.stderr

    def test_a_failed_verification_is_unknown_not_empty(self, tmp_path: Path) -> None:
        """`docker compose ps` failing must NOT be read as 'no containers'."""
        r = _run_teardown(tmp_path, DOWN_RC="0", PS_RC="2")
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "could not verify" in r.stderr
        assert "NOT assuming" in r.stderr

    def test_succeeds_only_when_the_stack_is_verifiably_down(
        self, tmp_path: Path
    ) -> None:
        r = _run_teardown(tmp_path, DOWN_RC="0", PS_OUT="")
        assert "RC=0" in r.stdout, r.stdout + r.stderr
        assert "FAIL" not in r.stderr

    def test_the_container_check_includes_stopped_containers(
        self, tmp_path: Path
    ) -> None:
        """`ps -q` alone calls an exited-but-not-removed container clean.
        `down` is supposed to REMOVE containers, so the post-condition must
        enumerate all of them (`-a`), which is strictly stronger."""
        log = tmp_path / "docker.log"
        _run_teardown(tmp_path, DOWN_RC="0", PS_OUT="", DOCKER_LOG=str(log))
        ps_calls = [ln for ln in log.read_text().splitlines() if " ps " in f" {ln} "]
        assert ps_calls, log.read_text()
        assert all(c.endswith("ps -q -a") for c in ps_calls), ps_calls

    def test_the_macmini_overlay_reaches_every_compose_call(
        self, tmp_path: Path
    ) -> None:
        """Dropping the overlay from `compose_args` would tear down a
        DIFFERENT project than the one bootstrap brought up."""
        log = tmp_path / "docker.log"
        _run_teardown(tmp_path, DOWN_RC="0", PS_OUT="", DOCKER_LOG=str(log))
        calls = log.read_text().splitlines()
        assert calls, "fake docker was never invoked"
        for call in calls:
            assert f"-f {REPO_ROOT}/docker-compose.yml" in call, call
            assert f"-f {REPO_ROOT}/docker-compose.macmini.yml" in call, call

    def test_a_residual_hydrosolutions_job_fails_the_teardown(
        self, tmp_path: Path
    ) -> None:
        """The per-label loop only knows the labels this repo ships. launchd
        itself is the authority on what is still registered."""
        r = _run_teardown(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            LIST_OUT="-\t0\tch.hydrosolutions.sapphire-some-future-job\n",
        )
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "STILL registered after teardown" in r.stderr
        assert "ch.hydrosolutions.sapphire-some-future-job" in r.stderr

    def test_a_failed_container_check_reports_why(self, tmp_path: Path) -> None:
        """ "could not verify" with no reason sends the operator back to the
        host blind. Whatever docker said must reach them."""
        r = _run_teardown(
            tmp_path,
            DOWN_RC="0",
            PS_RC="2",
            PS_ERR="Cannot connect to the Docker daemon",
        )
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "Cannot connect to the Docker daemon" in r.stderr, r.stderr

    def test_a_failed_launchctl_list_is_unknown_not_clean(self, tmp_path: Path) -> None:
        """Same rule as the container check: a failed enumeration is UNKNOWN,
        never 'no jobs loaded'."""
        r = _run_teardown(
            tmp_path, DOWN_RC="0", PS_OUT="", LIST_RC="3", LIST_OUT="boom"
        )
        assert "RC=1" in r.stdout, r.stdout + r.stderr
        assert "could not enumerate launchd jobs" in r.stderr
        assert "NOT assuming" in r.stderr


class TestUninstallEntryPointHonoursTheResult:
    """The defect lived at the CALL SITE, not in the helper: `teardown_stack ||
    true` there would reproduce false success while every helper test above
    still passed (verified by mutation). These run the real script."""

    def _uninstall(
        self, tmp_path: Path, *args: str, **env: str
    ) -> subprocess.CompletedProcess[str]:
        bin_dir = _fake_bin(tmp_path)
        return subprocess.run(
            ["bash", str(SCRIPT), "--uninstall", *args],
            capture_output=True,
            text=True,
            env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), **env},
        )

    def test_exits_non_zero_and_does_not_claim_completion_on_failure(
        self, tmp_path: Path
    ) -> None:
        r = self._uninstall(tmp_path, DOWN_RC="1")
        assert r.returncode != 0, r.stdout + r.stderr
        assert "uninstall complete" not in r.stdout
        assert "INCOMPLETE" in r.stderr

    def test_reports_completion_only_when_teardown_succeeded(
        self, tmp_path: Path
    ) -> None:
        r = self._uninstall(tmp_path, DOWN_RC="0", PS_OUT="")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "uninstall complete" in r.stdout

    def test_dry_run_does_not_claim_an_uninstall_happened(self, tmp_path: Path) -> None:
        r = self._uninstall(tmp_path, "--dry-run")
        assert "uninstall complete" not in r.stdout, r.stdout
        assert "dry-run complete" in r.stdout

    def test_dry_run_never_touches_launchctl(self, tmp_path: Path) -> None:
        """`--dry-run` must PRINT the bootout, not perform it. Without the
        DRY_RUN guard in `bootout_label`, a dry run would really unload the
        operator's LaunchAgents."""
        agents = tmp_path / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        (agents / "ch.hydrosolutions.sapphire.plist").write_text("<plist/>")
        log = tmp_path / "launchctl.log"
        r = self._uninstall(tmp_path, "--dry-run", LAUNCHCTL_LOG=str(log))
        assert "would run: launchctl bootout" in r.stdout, r.stdout
        assert not log.exists(), (
            f"dry run invoked launchctl: {log.read_text()!r}\n{r.stdout}"
        )

    def test_a_launchd_job_that_survives_bootout_fails_the_uninstall(
        self, tmp_path: Path
    ) -> None:
        """Docker stopping cleanly is not enough: a LaunchAgent still
        registered afterwards must also fail the uninstall. Without this the
        docker half is honest and the launchd half silently is not."""
        agents = tmp_path / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        (agents / "ch.hydrosolutions.sapphire.plist").write_text("<plist/>")
        r = self._uninstall(tmp_path, DOWN_RC="0", PS_OUT="", PRINT_RC="0")
        assert r.returncode != 0, r.stdout + r.stderr
        assert "still registered after bootout" in r.stderr
        assert "uninstall complete" not in r.stdout

    def test_a_surviving_job_reports_what_bootout_said(self, tmp_path: Path) -> None:
        """Same rule as the container check: the operator gets the reason, not
        just the verdict."""
        agents = tmp_path / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        (agents / "ch.hydrosolutions.sapphire.plist").write_text("<plist/>")
        r = self._uninstall(
            tmp_path,
            DOWN_RC="0",
            PS_OUT="",
            PRINT_RC="0",
            BOOTOUT_ERR="Boot-out failed: 5: Input/output error",
        )
        assert r.returncode != 0, r.stdout + r.stderr
        assert "Boot-out failed: 5: Input/output error" in r.stderr, r.stderr

    @pytest.mark.parametrize("label", SHIPPED_LABELS)
    def test_every_shipped_launchagent_is_verified_gone(
        self, tmp_path: Path, label: str
    ) -> None:
        """One case per label. Dropping any of them from the teardown loop —
        including the two installed by hand from their runbooks, which the
        uninstall summary nonetheless tells the operator to `rm` — lets a live
        job survive an uninstall that reports success."""
        r = self._uninstall(tmp_path, DOWN_RC="0", PS_OUT="", PRINT_RC="0")
        assert f"still registered after bootout: {label}" in r.stderr, r.stderr

    def test_a_hydrosolutions_agent_the_script_does_not_know_is_torn_down_too(
        self, tmp_path: Path
    ) -> None:
        """Teardown covers the whole `ch.hydrosolutions.*.plist` glob the
        uninstall summary tells operators to delete, not just the hardcoded
        list."""
        agents = tmp_path / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        (agents / "ch.hydrosolutions.sapphire-unknown.plist").write_text("<plist/>")
        r = self._uninstall(tmp_path, DOWN_RC="0", PS_OUT="", PRINT_RC="0")
        assert r.returncode != 0, r.stdout + r.stderr
        assert (
            "still registered after bootout: ch.hydrosolutions.sapphire-unknown"
            in r.stderr
        ), r.stderr

    def test_a_loaded_job_whose_plist_is_gone_is_not_counted_as_torn_down(
        self, tmp_path: Path
    ) -> None:
        """The old code only VERIFIED a label when its plist was present and
        logged "(skipping)" otherwise — contributing 0 to the failure count
        for exactly the state the uninstall summary tells operators to create
        (`rm ~/Library/LaunchAgents/ch.hydrosolutions.*.plist`). No plists
        exist here, yet every label still resolves."""
        assert not (tmp_path / "Library" / "LaunchAgents").exists()
        r = self._uninstall(tmp_path, DOWN_RC="0", PS_OUT="", PRINT_RC="0")
        assert r.returncode != 0, r.stdout + r.stderr
        assert "still registered after bootout" in r.stderr
        assert "its plist is already gone" in r.stderr
        assert "uninstall complete" not in r.stdout
