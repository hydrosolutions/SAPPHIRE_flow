"""Plan 194 T1 — `backup_target_verified` (bootstrap-mac-mini.sh).

Shell-script tests, mirroring the subprocess-shell-out convention in
tests/unit/ops/test_launchd_prune_docker.py: we `source` the script (which
stops right after its function definitions when sourced — see the
BASH_SOURCE guard) and call `backup_target_verified` directly, rather than
running the full interactive bootstrap flow (which requires Apple Silicon,
Docker Desktop, uv, and a hardcoded repo path — none available off-host).

Predicate under test (Plan 194 D1): a backup directory is verified only if
its device id differs from the data directory's AND its mount root is a
real, currently-mounted volume (`mount` output) — not merely a directory
that happens to report a different device id. Both checks are exercised
here via a fake `stat`/`mount` pair placed first on PATH, so the test does
not depend on any real distinct device being available in CI.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

_SCRIPT = (
    Path(__file__).parent.parent.parent.parent / "scripts" / "bootstrap-mac-mini.sh"
)


def _write_fake_bin(
    bin_dir: Path,
    *,
    backup_path: str,
    data_path: str,
    backup_dev: str,
    data_dev: str,
    mount_output: str,
) -> None:
    """A fake `stat` (returns the device id for a known path, keyed off the
    LAST argument regardless of which stat-flag dialect the caller used) and
    a fake `mount` (prints canned output), both ahead of the real binaries
    on PATH.
    """
    stat_stub = textwrap.dedent(
        f"""\
        #!/bin/bash
        path="${{@: -1}}"
        case "${{path}}" in
            "{backup_path}") echo "{backup_dev}" ;;
            "{data_path}") echo "{data_dev}" ;;
            *) exit 1 ;;
        esac
        """
    )
    (bin_dir / "stat").write_text(stat_stub)
    (bin_dir / "stat").chmod(0o755)

    mount_stub = textwrap.dedent(
        f"""\
        #!/bin/bash
        printf '%s\\n' "{mount_output}"
        """
    )
    (bin_dir / "mount").write_text(mount_stub)
    (bin_dir / "mount").chmod(0o755)


def _run_predicate(
    tmp_path: Path, *, backup_dir: Path, data_dir: Path, bin_dir: Path
) -> subprocess.CompletedProcess[str]:
    script = textwrap.dedent(
        f"""\
        source "{_SCRIPT}"
        if backup_target_verified "{backup_dir}" "{data_dir}"; then
            echo VERIFIED
        else
            echo NOT_VERIFIED
        fi
        """
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
    )


class TestBackupTargetVerified:
    def test_plain_directory_same_device_is_not_verified(self, tmp_path: Path) -> None:
        """The exact bug on the mac mini (2026-08-20): a bind-mount host
        path Docker silently created is a plain directory sharing the boot
        disk's device id — must NOT be verified."""
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_bin(
            bin_dir,
            backup_path=str(backup_dir),
            data_path=str(data_dir),
            backup_dev="16777234",
            data_dev="16777234",  # SAME device id — the mini's actual state
            mount_output=f"/dev/disk1 on {backup_dir.parent} (apfs, local)",
        )

        result = _run_predicate(
            tmp_path, backup_dir=backup_dir, data_dir=data_dir, bin_dir=bin_dir
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "NOT_VERIFIED"

    def test_missing_backup_directory_is_not_verified(self, tmp_path: Path) -> None:
        backup_dir = tmp_path / "does-not-exist"
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_bin(
            bin_dir,
            backup_path=str(backup_dir),
            data_path=str(data_dir),
            backup_dev="1",
            data_dev="2",
            mount_output=f"/dev/disk1 on {backup_dir.parent} (apfs, local)",
        )

        result = _run_predicate(
            tmp_path, backup_dir=backup_dir, data_dir=data_dir, bin_dir=bin_dir
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "NOT_VERIFIED"

    def test_different_device_but_not_a_real_mount_point_is_not_verified(
        self, tmp_path: Path
    ) -> None:
        """Defense-in-depth (Plan 194 D1): even if the device id happens to
        differ, a directory `mount` does not list as a mount point is not
        proof of a real attached volume."""
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_bin(
            bin_dir,
            backup_path=str(backup_dir),
            data_path=str(data_dir),
            backup_dev="1",
            data_dev="2",
            mount_output="/dev/disk1 on /some/unrelated/path (apfs, local)",
        )

        result = _run_predicate(
            tmp_path, backup_dir=backup_dir, data_dir=data_dir, bin_dir=bin_dir
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "NOT_VERIFIED"

    def test_distinct_device_and_real_mount_point_is_verified(
        self, tmp_path: Path
    ) -> None:
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_bin(
            bin_dir,
            backup_path=str(backup_dir),
            data_path=str(data_dir),
            backup_dev="99",
            data_dev="1",
            mount_output=f"/dev/disk4s1 on {backup_dir.parent} (apfs, local)",
        )

        result = _run_predicate(
            tmp_path, backup_dir=backup_dir, data_dir=data_dir, bin_dir=bin_dir
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "VERIFIED"
