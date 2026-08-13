"""Ops tests for Plan 158 D8/T4 — start-sapphire.sh resolves its Docker
binary through the shared docker-endpoint.sh contract instead of a bare
`docker` (resolved from launchd's minimal PATH, which does not reliably
find a Homebrew-installed CLI such as Colima's).

These are shell-script tests, not Python unit tests in the usual sense — we
shell out via subprocess to exercise the actual wrapper; docker is faked by
setting DOCKER_CMD to an absolute path of a stub, the same convention
tests/unit/ops/test_launchd_prune_docker.py and test_recap_probe_wrapper.py
use. `exec docker compose ... up -d` (the final step on a successful
`docker info`) is never reached in these tests — the fake docker always
reports "not up" so the script exercises only the wait/timeout path, which
needs no real compose stack.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts" / "launchd"
_WRAPPER_SCRIPT = _SCRIPTS_DIR / "start-sapphire.sh"


def _write_fake_docker(bin_dir: Path, *, info_exit_code: int, args_log: Path) -> Path:
    fake = bin_dir / "docker"
    fake.write_text(
        "#!/bin/bash\n"
        f'printf \'%s\\n\' "$@" >> "{args_log}"\n'
        f'if [[ "$1" == "info" ]]; then exit {info_exit_code}; fi\n'
        "exit 0\n"
    )
    fake.chmod(0o755)
    return fake


class TestDockerEndpointContract:
    """Only the "docker already up" path is exercised here — the retry
    loop's WAIT_MAX=240s/sleep=3s are hardcoded (not overridable via env),
    so a "docker never comes up" scenario would take 4 minutes for real;
    that path is unchanged by this plan (still `until ... info; do sleep
    3; done`) and not worth a 4-minute test."""

    def test_docker_ready_proceeds_to_compose_up(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        fake = _write_fake_docker(bin_dir, info_exit_code=0, args_log=args_log)

        env = {**os.environ, "DOCKER_CMD": str(fake)}
        result = subprocess.run(
            ["bash", str(_WRAPPER_SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        log = args_log.read_text()
        assert "compose" in log
        assert "up" in log

    def test_sapphire_docker_bin_override_is_used_when_docker_cmd_unset(
        self, tmp_path: Path
    ) -> None:
        colima_bin_dir = tmp_path / "colima-style-bin"
        colima_bin_dir.mkdir()
        args_log = tmp_path / "docker-args.log"
        fake = _write_fake_docker(colima_bin_dir, info_exit_code=0, args_log=args_log)

        env = {**os.environ, "SAPPHIRE_DOCKER_BIN": str(fake)}
        env.pop("DOCKER_CMD", None)
        result = subprocess.run(
            ["bash", str(_WRAPPER_SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert args_log.exists() and args_log.read_text().strip() != ""
