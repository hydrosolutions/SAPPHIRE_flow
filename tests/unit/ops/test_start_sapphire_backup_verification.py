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
"""

from __future__ import annotations

import json
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
    marker_path: Path,
    bin_dir: Path,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "SAPPHIRE_REPO_ROOT": str(repo_root),
        "SAPPHIRE_BACKUP_DIR": str(backup_dir),
        "SAPPHIRE_BACKUP_MARKER_PATH": str(marker_path),
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
        marker_path = repo_root / ".backup-volume-unverified.json"
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
            marker_path=marker_path,
            bin_dir=bin_dir,
        )

        assert result.returncode == 0, result.stderr
        assert marker_path.exists(), "marker must be written on an unverified check"
        payload = json.loads(marker_path.read_text())
        assert payload["verified"] is False
        assert compose_log.exists(), (
            "the stack must still start (D3: never fail closed)"
        )
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
        marker_path = repo_root / ".backup-volume-unverified.json"
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
            marker_path=marker_path,
            bin_dir=bin_dir,
        )

        assert result.returncode == 0, result.stderr
        assert not marker_path.exists()
        assert compose_log.exists()
        assert "up -d" in compose_log.read_text()

    def test_stale_marker_from_prior_failure_is_cleared_once_verified(
        self, tmp_path: Path
    ) -> None:
        """A marker written by a previous unverified tick must not linger
        forever once the volume is verified again."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        marker_path = repo_root / ".backup-volume-unverified.json"
        marker_path.write_text('{"verified": false, "checked_at": "stale"}\n')
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
            marker_path=marker_path,
            bin_dir=bin_dir,
        )

        assert result.returncode == 0, result.stderr
        assert not marker_path.exists()
