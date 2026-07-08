"""Ops tests for Plan 105 D3 — launchd docker-prune job.

Tests assert:
  1. prune-docker.sh skips the prune when docker ps finds no sapphire container.
  2. install-launchd.sh PLISTS array registers the new docker-prune plist.

These are shell-script tests, not Python unit tests in the usual sense.  We
shell out via subprocess to exercise the actual script; docker is faked by
setting DOCKER_CMD to an absolute path of a stub so the test never touches the
Docker daemon.  (We use DOCKER_CMD rather than PATH injection because
prune-docker.sh exports PATH="/usr/local/bin:..." which prepends system dirs
before any test-injected PATH entry, causing the real docker to be resolved
instead of the fake.)
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts" / "launchd"
_PRUNE_SCRIPT = _SCRIPTS_DIR / "prune-docker.sh"
_INSTALL_SCRIPT = _SCRIPTS_DIR / "install-launchd.sh"


def _write_fake_docker(bin_dir: Path, *, containers: list[str]) -> Path:
    """Write a fake docker executable to bin_dir.

    When invoked with `ps`, it prints one container name per line and exits 0.
    All other invocations (system df, image prune, builder prune) exit 0 with
    no output so they are safe no-ops.
    """
    if containers:
        # printf '%s\\n' "name1" "name2" prints each on its own line.
        printf_args = " ".join(f'"{c}"' for c in containers)
        ps_body = f"printf '%s\\n' {printf_args}"
    else:
        ps_body = "true"

    stub = textwrap.dedent(
        f"""\
        #!/bin/bash
        if [[ "$1" == "ps" ]]; then
            {ps_body}
            exit 0
        fi
        # system df, image prune, builder prune — all safe no-ops in tests.
        exit 0
        """
    )
    fake = bin_dir / "docker"
    fake.write_text(stub)
    fake.chmod(0o755)
    return fake


def _run_prune_script(
    tmp_path: Path, *, docker_cmd: Path
) -> subprocess.CompletedProcess[str]:
    """Run prune-docker.sh with DOCKER_CMD pointing at the given fake docker."""
    env = {**os.environ, "DOCKER_CMD": str(docker_cmd)}
    return subprocess.run(
        ["bash", str(_PRUNE_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )


class TestPruneDockerStackGuard:
    """prune-docker.sh must skip all pruning when no sapphire container is running."""

    def test_skips_when_no_sapphire_container(self, tmp_path: Path) -> None:
        """When docker ps returns no sapphire container names, the script exits 0
        and prints the 'skipping prune' message without running any prune command."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = _write_fake_docker(bin_dir, containers=["other-container", "unrelated"])

        result = _run_prune_script(tmp_path, docker_cmd=fake)

        assert result.returncode == 0, f"script failed: {result.stderr}"
        combined = result.stdout + result.stderr
        assert "skipping prune" in combined, (
            f"expected 'skipping prune' in output; got:\n{combined}"
        )
        # None of the prune commands must appear in stdout when the stack is down.
        assert "image prune" not in combined, (
            "image prune was called despite no sapphire containers"
        )
        assert "builder prune" not in combined, (
            "builder prune was called despite no sapphire containers"
        )

    def test_skips_when_docker_ps_empty(self, tmp_path: Path) -> None:
        """Empty docker ps output (no containers at all) is treated as stack-down."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = _write_fake_docker(bin_dir, containers=[])

        result = _run_prune_script(tmp_path, docker_cmd=fake)

        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "skipping prune" in combined

    def test_proceeds_when_sapphire_container_present(self, tmp_path: Path) -> None:
        """When a sapphire container is running the stack-up guard passes.

        The fake docker system df returns '0B' reclaimable, so neither
        prune command fires (below the 1 GB threshold) — but the script must
        NOT exit early with 'skipping prune'.
        """
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        # Override system df to return minimal JSON so the size-guard parses.
        stub = textwrap.dedent(
            """\
            #!/bin/bash
            if [[ "$1" == "ps" ]]; then
                printf 'sapphire_flow-worker-1\\n'
                exit 0
            fi
            if [[ "$1" == "system" && "$2" == "df" ]]; then
                printf '{"Type":"Images","Reclaimable":"0B"}\\n'
                printf '{"Type":"Build Cache","Reclaimable":"0B"}\\n'
                exit 0
            fi
            exit 0
            """
        )
        fake = bin_dir / "docker"
        fake.write_text(stub)
        fake.chmod(0o755)

        result = _run_prune_script(tmp_path, docker_cmd=fake)

        assert result.returncode == 0, f"script failed: {result.stderr}"
        combined = result.stdout + result.stderr
        assert "skipping prune" not in combined, (
            f"guard fired despite sapphire container running:\n{combined}"
        )
        # Size guard fires but skips both prunes (0 GB < 1 GB threshold).
        assert "skipping image prune" in combined


class TestInstallLaunchdPruneRegistration:
    """install-launchd.sh PLISTS array must contain the docker-prune plist."""

    def test_plists_contains_docker_prune_plist(self) -> None:
        content = _INSTALL_SCRIPT.read_text()
        assert "ch.hydrosolutions.sapphire-docker-prune.plist" in content, (
            "install-launchd.sh PLISTS array is missing "
            "'ch.hydrosolutions.sapphire-docker-prune.plist'"
        )

    def test_docker_prune_plist_file_exists(self) -> None:
        plist = _SCRIPTS_DIR / "ch.hydrosolutions.sapphire-docker-prune.plist"
        assert plist.exists(), f"plist file not found at {plist}"

    def test_prune_script_exists_and_is_executable(self) -> None:
        assert _PRUNE_SCRIPT.exists(), f"prune-docker.sh not found at {_PRUNE_SCRIPT}"
        assert os.access(_PRUNE_SCRIPT, os.X_OK), (
            f"prune-docker.sh is not executable: {_PRUNE_SCRIPT}"
        )
