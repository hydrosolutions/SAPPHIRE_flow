"""`teardown_stack` (bootstrap-mac-mini.sh) must report a failed teardown.

Shell-script tests, mirroring the source-and-call convention in
tests/unit/scripts/test_bootstrap_backup_target_verified.py: the script
returns early when sourced, so `teardown_stack` is defined above that guard
and is called directly here with a fake `docker` first on PATH.

The defect being locked: the uninstall path used to run `docker compose down
|| true` and then print "uninstall complete" unconditionally, so a teardown
that left containers running reported success. Two behaviours are required —
a non-zero `down` is a failure, and an exit-0 `down` is NOT by itself proof
the containers are gone. A FAILED verification must read as UNKNOWN, never as
"nothing running"; that is the silent-success bug already fixed once in
prune-docker.sh (tests/unit/ops/test_launchd_prune_docker.py).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "bootstrap-mac-mini.sh"

_FAKE_DOCKER = """#!/bin/bash
# Validates the REAL call shape before answering: `compose -f <file> [-f <file>]
# (down|ps -q)`. A crude token-matcher would keep passing if production stopped
# passing `compose` or `-f` at all, so it rejects anything unexpected.
if [ "$1" != "compose" ]; then
  echo "fake docker: expected 'compose', got '$1'" >&2; exit 90
fi
shift
files=0
while [ "$1" = "-f" ]; do
  [ -n "$2" ] || { echo "fake docker: -f with no file" >&2; exit 91; }
  files=$((files+1)); shift 2
done
[ "$files" -ge 1 ] || { echo "fake docker: no -f compose file" >&2; exit 92; }
case "$1" in
  down) exit ${DOWN_RC:-0} ;;
  ps)   [ "$2" = "-q" ] || { echo "fake docker: expected 'ps -q'" >&2; exit 93; }
        [ "${PS_RC:-0}" -ne 0 ] && exit "${PS_RC}"
        printf '%s' "${PS_OUT:-}"; exit 0 ;;
  *)    echo "fake docker: unexpected subcommand '$1'" >&2; exit 94 ;;
esac
"""


def _run_teardown(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "docker"
    fake.write_text(_FAKE_DOCKER)
    fake.chmod(0o755)
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


class TestUninstallEntryPointHonoursTheResult:
    """The defect lived at the CALL SITE, not in the helper: `teardown_stack ||
    true` there would reproduce false success while every helper test above
    still passed (verified by mutation). These run the real script."""

    def _uninstall(
        self, tmp_path: Path, **env: str
    ) -> subprocess.CompletedProcess[str]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake = bin_dir / "docker"
        fake.write_text(_FAKE_DOCKER)
        fake.chmod(0o755)
        (bin_dir / "id").write_text("#!/bin/bash\necho 501\n")
        (bin_dir / "id").chmod(0o755)
        # `print` exiting 0 means the label STILL resolves — still loaded.
        (bin_dir / "launchctl").write_text(
            '#!/bin/bash\n[ "$1" = "print" ] && exit ${PRINT_RC:-1}\nexit 0\n'
        )
        (bin_dir / "launchctl").chmod(0o755)
        return subprocess.run(
            ["bash", str(SCRIPT), "--uninstall"],
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
        r = self._uninstall(tmp_path, DOWN_RC="1")
        r2 = subprocess.run(
            ["bash", str(SCRIPT), "--uninstall", "--dry-run"],
            capture_output=True,
            text=True,
            env={"PATH": f"{tmp_path}/bin:/usr/bin:/bin", "HOME": str(tmp_path)},
        )
        assert "uninstall complete" not in r2.stdout, r2.stdout
        assert "dry-run complete" in r2.stdout
        assert r.returncode != 0

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
