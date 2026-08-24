"""Plan 194 T2 — start-sapphire.sh: check, record, PROCEED.

Per D3, unlike bootstrap-mac-mini.sh (interactive, fails closed),
start-sapphire.sh must NEVER fail closed on an unverified backup volume —
refusing to bring up the forecasting stack over an absent *backup* disk
would trade a backup outage for a forecasting outage. It writes a
machine-readable marker and always proceeds to `docker compose ... up -d`.

We run the full script via subprocess (mirrors
tests/unit/ops/test_launchd_prune_docker.py), with a fake `docker` (so the
Docker-wait loop and the final compose-up are both no-ops we can observe)
and a fake `stat`/`mount` pair driving the device predicate. Host-side
paths are env-overridable (SAPPHIRE_REPO_ROOT, SAPPHIRE_BACKUP_DIR,
SAPPHIRE_BACKUP_MARKER_PATH), mirroring run-recap-probe.sh's convention.

The device-id check `backup_target_verified` performs is on the backup
directory ITSELF (fixer round: not merely its parent — see
tests/unit/scripts/test_bootstrap_backup_target_verified.py's module
docstring for why a parent-only check is insufficient), plus a check that
its parent (the mount root) is a real, currently-mounted volume.

Fixer round (Codex review of Plan 194): the marker read/write in
start-sapphire.sh must be BEST-EFFORT under `set -e` — a failure to
remove/write it (marker path is a directory, unwritable/nonexistent
parent) must never itself prevent `exec docker compose ... up -d` from
running. `TestMarkerWriteIsBestEffort` locks that.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

_SCRIPT = (
    Path(__file__).parent.parent.parent.parent
    / "scripts"
    / "launchd"
    / "start-sapphire.sh"
)


def _write_fake_docker(bin_dir: Path, *, compose_log: Path) -> None:
    """`docker info` always succeeds (skip the wait loop instantly);
    `docker compose ... up -d` appends its args to compose_log and exits 0,
    so a test can assert the stack start was actually attempted."""
    stub = textwrap.dedent(
        f"""\
        #!/bin/bash
        if [[ "$1" == "info" ]]; then
            exit 0
        fi
        printf '%s\\n' "$*" >> "{compose_log}"
        exit 0
        """
    )
    fake = bin_dir / "docker"
    fake.write_text(stub)
    fake.chmod(0o755)


def _write_fake_stat_mount(
    bin_dir: Path,
    *,
    backup_path: str,
    data_path: str,
    backup_dev: str,
    data_dev: str,
    mount_output: str,
) -> None:
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


def _run_start_sapphire(
    tmp_path: Path,
    *,
    repo_root: Path,
    backup_dir: Path,
    bin_dir: Path,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        # Plan 199 T2: start-sapphire.sh now resolves its docker binary via
        # the shared docker-endpoint.sh contract (DOCKER_CMD, if set, wins
        # over DOCKER_BIN) — same test-injection seam already used by
        # test_launchd_prune_docker.py / test_recap_probe_wrapper.py.
        "DOCKER_CMD": str(bin_dir / "docker"),
        "SAPPHIRE_REPO_ROOT": str(repo_root),
        "SAPPHIRE_BACKUP_DIR": str(backup_dir),
    }
    return subprocess.run(
        ["bash", str(_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )


class TestStartSapphireBackupVerification:
    def test_unverified_backup_writes_marker_and_still_starts_stack(
        self, tmp_path: Path
    ) -> None:
        """The exact mac-mini bug: same device id -> marker written, but
        the compose stack still comes up (D3: never fail closed here)."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        compose_log = tmp_path / "compose.log"

        _write_fake_docker(bin_dir, compose_log=compose_log)
        _write_fake_stat_mount(
            bin_dir,
            backup_path=str(backup_dir),
            data_path=str(repo_root),
            backup_dev="16777234",
            data_dev="16777234",  # SAME device — the mini's actual bug
            mount_output=f"/dev/disk1 on {backup_dir.parent} (apfs, local)",
        )

        result = _run_start_sapphire(
            tmp_path,
            repo_root=repo_root,
            backup_dir=backup_dir,
            bin_dir=bin_dir,
        )

        assert result.returncode == 0, result.stderr
        assert "backup volume not verified" in result.stderr, (
            "an unverified check must WARN on stderr -> the launchd log"
        )
        assert compose_log.exists(), (
            "the stack must still start (D3: never fail closed)"
        )
        assert "up -d" in compose_log.read_text()

    def test_distinct_device_but_not_a_mount_point_is_unverified(
        self, tmp_path: Path
    ) -> None:
        """The mount clause must do real work in THIS script's copy.

        Every other unverified case here uses identical device ids, so the
        `mount | grep` clause could be deleted from start-sapphire.sh and the
        suite would stay green. This case gives the backup path a DIFFERENT
        device id but reports no mount at its root — a plain directory on some
        other filesystem — which only the mount clause can reject. The
        predicate is duplicated verbatim in bootstrap-mac-mini.sh, so that
        script's tests cannot protect this copy.
        """
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        compose_log = tmp_path / "compose.log"

        _write_fake_docker(bin_dir, compose_log=compose_log)
        _write_fake_stat_mount(
            bin_dir,
            backup_path=str(backup_dir),
            data_path=str(repo_root),
            backup_dev="99",  # DIFFERENT device...
            data_dev="1",
            # ...but nothing is mounted at the backup dir's parent.
            mount_output="/dev/disk3s5 on / (apfs, local, journaled)",
        )

        result = _run_start_sapphire(
            tmp_path,
            repo_root=repo_root,
            backup_dir=backup_dir,
            bin_dir=bin_dir,
        )

        assert result.returncode == 0, result.stderr
        assert "backup volume not verified" in result.stderr, (
            "a differing device id is NOT sufficient — the path must also be a "
            "real mount point, or the mount clause is dead code"
        )
        assert compose_log.exists(), "D3: the stack still starts"
        assert "up -d" in compose_log.read_text()

    def test_verified_backup_writes_no_marker_and_starts_stack(
        self, tmp_path: Path
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        compose_log = tmp_path / "compose.log"

        _write_fake_docker(bin_dir, compose_log=compose_log)
        _write_fake_stat_mount(
            bin_dir,
            backup_path=str(backup_dir),
            data_path=str(repo_root),
            backup_dev="99",
            data_dev="1",
            mount_output=f"/dev/disk4s1 on {backup_dir.parent} (apfs, local)",
        )

        result = _run_start_sapphire(
            tmp_path,
            repo_root=repo_root,
            backup_dir=backup_dir,
            bin_dir=bin_dir,
        )

        assert result.returncode == 0, result.stderr
        assert "backup volume not verified" not in result.stderr
        assert compose_log.exists()
        assert "up -d" in compose_log.read_text()
