"""Plan 194 T1 — `backup_target_verified` (bootstrap-mac-mini.sh).

Shell-script tests, mirroring the subprocess-shell-out convention in
tests/unit/ops/test_launchd_prune_docker.py: we `source` the script (which
stops right after its function definitions when sourced — see the
BASH_SOURCE guard) and call `backup_target_verified` directly, rather than
running the full interactive bootstrap flow (which requires Apple Silicon,
Docker Desktop, uv, and a hardcoded repo path — none available off-host).

Predicate under test (Plan 194 D1, fixer round): a backup directory is
verified only if its OWN device id differs from the data directory's AND
its mount root (the backup directory's parent) is a real, currently-mounted
volume (`mount` output) — not merely a directory that happens to report a
different device id. The device-id check is deliberately performed on the
backup directory ITSELF, matching the Python predicate
(`backup_target_verified` in `src/sapphire_flow/ops/watchdog.py`) — a
parent-only check would let a nested bind-mount, or a backup directory that
is itself a symlink onto the data device, satisfy verification while still
landing dumps on the wrong device (`TestBackupTargetVerifiedChecksBackupDirNotParent`
below reproduces both). `_backup_mount_root_verified` is a SEPARATE,
narrower helper: it verifies only the mount root, deliberately tolerating a
not-yet-created backup directory, so bootstrap Step 6 can decide whether
it's safe to `mkdir -p` a `pg_dumps/` subdirectory onto a freshly attached
but empty volume. All of this is exercised here via a fake `stat`/`mount`
pair placed first on PATH, so the test does not depend on any real distinct
device being available in CI.
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
    devices: dict[str, str],
    mount_output: str,
) -> None:
    """A fake `stat` (returns the device id for a known path, keyed off the
    LAST argument regardless of which stat-flag dialect the caller used,
    via the `devices` path->device-id mapping) and a fake `mount` (prints
    canned output), both ahead of the real binaries on PATH.

    Every path the script under test actually `stat`s must have an entry
    in `devices` — an unlisted path exits 1, same as a real stat failure.
    """
    cases = "\n".join(
        f'            "{path}") echo "{dev}" ;;' for path, dev in devices.items()
    )
    stat_stub = textwrap.dedent(
        f"""\
        #!/bin/bash
        path="${{@: -1}}"
        case "${{path}}" in
{cases}
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


def _run_mount_root_predicate(
    tmp_path: Path, *, mount_root: Path, data_dir: Path, bin_dir: Path
) -> subprocess.CompletedProcess[str]:
    script = textwrap.dedent(
        f"""\
        source "{_SCRIPT}"
        if _backup_mount_root_verified "{mount_root}" "{data_dir}"; then
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
            devices={
                str(backup_dir): "16777234",
                str(data_dir): "16777234",  # SAME device id — the mini's actual state
            },
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
            devices={str(backup_dir): "1", str(data_dir): "2"},
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
            devices={str(backup_dir): "1", str(data_dir): "2"},
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
            devices={str(backup_dir): "99", str(data_dir): "1"},
            mount_output=f"/dev/disk4s1 on {backup_dir.parent} (apfs, local)",
        )

        result = _run_predicate(
            tmp_path, backup_dir=backup_dir, data_dir=data_dir, bin_dir=bin_dir
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "VERIFIED"


class TestBackupTargetVerifiedChecksBackupDirNotParent:
    """Fixer round (independent Codex review of Plan 194): the predicate
    must stat the backup directory ITSELF, not merely its parent — matching
    the Python predicate (`backup_target_verified` in watchdog.py), which
    checks `backup_dir` directly. Both cases here have a mount root that
    genuinely differs from the data device (so a parent-only check would
    say VERIFIED) while the backup directory itself — via a nested
    bind-mount or a symlink — actually resolves onto the SAME device as the
    data path. Both must be NOT_VERIFIED."""

    def test_nested_bind_mount_onto_data_device_is_not_verified(
        self, tmp_path: Path
    ) -> None:
        """The mount root (e.g. `/Volumes/sapphire-backup`) is a real,
        distinct, mounted volume — but `pg_dumps/` underneath it is itself
        a separate bind-mount back onto the boot disk (the same device the
        data lives on). A parent-only check never notices this."""
        mount_root = tmp_path / "sapphire-backup"
        mount_root.mkdir()
        backup_dir = mount_root / "pg_dumps"
        backup_dir.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_bin(
            bin_dir,
            devices={
                str(mount_root): "99",  # the mount root: genuinely distinct
                str(backup_dir): "1",  # but pg_dumps/ is bind-mounted onto...
                str(data_dir): "1",  # ...the SAME device the data is on
            },
            mount_output=f"/dev/disk4s1 on {mount_root} (apfs, local)",
        )

        result = _run_predicate(
            tmp_path, backup_dir=backup_dir, data_dir=data_dir, bin_dir=bin_dir
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "NOT_VERIFIED"

    def test_symlinked_backup_dir_onto_data_device_is_not_verified(
        self, tmp_path: Path
    ) -> None:
        """`pg_dumps` is a symlink whose parent directory sits on a
        genuinely distinct, correctly-mounted volume, but the symlink
        itself resolves onto the data device (`stat` follows symlinks, so
        the real device id reported for the symlink path IS the target's
        device — reproduced here by keying the fake stat's `devices` map
        on the symlink's own path)."""
        mount_root = tmp_path / "sapphire-backup"
        mount_root.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        real_target = data_dir / "pg_dumps_on_boot_disk"
        real_target.mkdir()
        backup_dir = mount_root / "pg_dumps"
        backup_dir.symlink_to(real_target)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_bin(
            bin_dir,
            devices={
                str(mount_root): "99",  # the mount root: genuinely distinct
                # `stat` follows the symlink -> reports the TARGET's device,
                # which is the data device (this is what the fixer-round
                # bug missed by only ever stat'ing the parent):
                str(backup_dir): "1",
                str(data_dir): "1",
            },
            mount_output=f"/dev/disk4s1 on {mount_root} (apfs, local)",
        )

        result = _run_predicate(
            tmp_path, backup_dir=backup_dir, data_dir=data_dir, bin_dir=bin_dir
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "NOT_VERIFIED"
        assert backup_dir.is_dir()  # sanity: -d follows the symlink to a real dir


class TestFreshlyMountedVolumeWithoutPgDumps:
    """Fixer round (Codex review of Plan 194): a fresh external volume,
    correctly mounted at the expected mount root but with no `pg_dumps/`
    subdirectory created yet, previously failed `backup_target_verified`
    outright — the predicate required `pg_dumps` to already exist before it
    would even look at the mount root's device id. `_backup_mount_root_verified`
    exists precisely to let a caller (bootstrap Step 6) tell "disk genuinely
    absent" apart from "disk present, subdirectory not created yet" and
    `mkdir -p` only in the second case.
    """

    def test_mount_root_alone_is_verified_without_pg_dumps_existing(
        self, tmp_path: Path
    ) -> None:
        mount_root = tmp_path / "sapphire-backup"
        mount_root.mkdir()  # the disk IS mounted...
        pg_dumps = mount_root / "pg_dumps"  # ...but never initialised
        assert not pg_dumps.exists()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_bin(
            bin_dir,
            devices={str(mount_root): "99", str(data_dir): "1"},
            mount_output=f"/dev/disk4s1 on {mount_root} (apfs, local)",
        )

        result = _run_mount_root_predicate(
            tmp_path, mount_root=mount_root, data_dir=data_dir, bin_dir=bin_dir
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "VERIFIED"

    def test_backup_target_verified_still_fails_closed_on_missing_pg_dumps(
        self, tmp_path: Path
    ) -> None:
        """Even with a verified mount root, `backup_target_verified` itself
        must keep failing until `pg_dumps/` actually exists — Step 6 is
        responsible for creating it (gated on the mount-root check above),
        not the predicate silently treating "missing" as "fine"."""
        mount_root = tmp_path / "sapphire-backup"
        mount_root.mkdir()
        backup_dir = mount_root / "pg_dumps"
        assert not backup_dir.exists()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_bin(
            bin_dir,
            devices={str(mount_root): "99", str(data_dir): "1"},
            mount_output=f"/dev/disk4s1 on {mount_root} (apfs, local)",
        )

        result = _run_predicate(
            tmp_path, backup_dir=backup_dir, data_dir=data_dir, bin_dir=bin_dir
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "NOT_VERIFIED"

    def test_step6_gated_mkdir_creates_pg_dumps_then_verifies(
        self, tmp_path: Path
    ) -> None:
        """Reproduces bootstrap-mac-mini.sh Step 6's exact gating logic
        (scripts/bootstrap-mac-mini.sh, "USB backup disk"): `mkdir -p` the
        backup directory ONLY when `_backup_mount_root_verified` already
        passed, then re-check `backup_target_verified`. Locks that a
        freshly mounted, empty, correctly-distinct disk ends up VERIFIED
        with `pg_dumps/` actually created on it — not on the boot disk."""
        mount_root = tmp_path / "sapphire-backup"
        mount_root.mkdir()
        backup_dir = mount_root / "pg_dumps"
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_bin(
            bin_dir,
            devices={
                str(mount_root): "99",
                str(backup_dir): "99",  # a real subdir of mount_root: same device
                str(data_dir): "1",
            },
            mount_output=f"/dev/disk4s1 on {mount_root} (apfs, local)",
        )

        script = textwrap.dedent(
            f"""\
            source "{_SCRIPT}"
            if _backup_mount_root_verified "{mount_root}" "{data_dir}" \\
                    && [ ! -d "{backup_dir}" ]; then
                mkdir -p "{backup_dir}"
            fi
            if backup_target_verified "{backup_dir}" "{data_dir}"; then
                echo VERIFIED
            else
                echo NOT_VERIFIED
            fi
            """
        )
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "VERIFIED"
        assert backup_dir.is_dir(), "pg_dumps must be created on the verified volume"

    def test_step6_gated_mkdir_does_not_create_dir_when_root_unverified(
        self, tmp_path: Path
    ) -> None:
        """Safety property: if the mount root is NOT a real, distinct,
        mounted volume (e.g. the disk was never attached at all), Step 6's
        gate must never `mkdir -p` the backup path — doing so unconditionally
        is exactly how an absent disk becomes a healthy-looking plain
        directory on the boot disk (the original bug this plan exists to
        catch)."""
        mount_root = tmp_path / "sapphire-backup"
        mount_root.mkdir()
        backup_dir = mount_root / "pg_dumps"
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _write_fake_bin(
            bin_dir,
            devices={
                str(mount_root): "1",
                str(data_dir): "1",  # SAME device — not a distinct volume
            },
            mount_output=f"/dev/disk1 on {mount_root} (apfs, local)",
        )

        script = textwrap.dedent(
            f"""\
            source "{_SCRIPT}"
            if _backup_mount_root_verified "{mount_root}" "{data_dir}" \\
                    && [ ! -d "{backup_dir}" ]; then
                mkdir -p "{backup_dir}"
            fi
            if backup_target_verified "{backup_dir}" "{data_dir}"; then
                echo VERIFIED
            else
                echo NOT_VERIFIED
            fi
            """
        )
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}
        result = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "NOT_VERIFIED"
        assert not backup_dir.exists(), (
            "must never create pg_dumps when the mount root itself is unverified"
        )
