"""Plan 199 T2 fixer round — locks the shared Docker endpoint contract
(``scripts/launchd/docker-endpoint.sh``) itself, plus its consumption by
every launchd wrapper that sources it.

Independent review of Plan 199 (MAJOR): the only previously-committed test
touching this contract exercised ``DOCKER_CMD`` — the pre-existing
test-injection seam. ``SAPPHIRE_DOCKER_BIN``, ``SAPPHIRE_DOCKER_HOST``, the
documented defaults, ``DOCKER_CMD``'s precedence over the two new
variables, and whether all four wrappers actually consume the contract
(rather than one of them quietly hardcoding the old endpoint) had zero
coverage. A wrapper reverted to hardcoding ``/usr/local/bin/docker`` and
Docker Desktop's socket while still honouring ``DOCKER_CMD`` would have
passed every committed test.

``TestDockerEndpointContractDefaults`` sources the file directly (no
wrapper) and proves the default values, both overrides, and that
``DOCKER_HOST`` is actually EXPORTED — visible to a child process, not just
the sourcing shell.

``TestDockerEndpointConsumedByEveryWrapper`` parametrizes across all four
wrappers enumerated in ``docker-endpoint.sh``'s own header comment
(``start-sapphire.sh``, ``prune-docker.sh``, ``run-recap-probe.sh``,
``run-nepal-forcing.sh``). Each fake ``docker`` stub only records its own
invocation (marker, the ``DOCKER_HOST`` it saw, its argv) to
``$INVOCATION_LOG`` and exits 0 — a deliberately dumb stub, because command
construction for each wrapper is already locked by that wrapper's own test
file; this file's job is only to prove binary/host RESOLUTION and
PRECEDENCE reach the actual invocation.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts" / "launchd"
_ENDPOINT_SCRIPT = _SCRIPTS_DIR / "docker-endpoint.sh"


# ---------- direct sourcing: defaults, overrides, export --------------------


def _source_and_report(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = textwrap.dedent(
        f"""\
        source "{_ENDPOINT_SCRIPT}"
        printf 'DOCKER_BIN=%s\\n' "$DOCKER_BIN"
        printf 'DOCKER_HOST=%s\\n' "$DOCKER_HOST"
        # Prove DOCKER_HOST is EXPORTED, not merely a shell-local variable:
        # a child process only inherits variables actually in its
        # environment, so this would be empty if `export` were dropped.
        printf 'CHILD_DOCKER_HOST=%s\\n' "$(bash -c 'printf %s "$DOCKER_HOST"')"
        """
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
    )


def _clean_base_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("SAPPHIRE_DOCKER_BIN", "SAPPHIRE_DOCKER_HOST", "DOCKER_CMD")
    }


class TestDockerEndpointContractDefaults:
    def test_defaults_are_docker_desktop(self) -> None:
        result = _source_and_report(_clean_base_env())

        assert result.returncode == 0, result.stderr
        assert "DOCKER_BIN=/usr/local/bin/docker" in result.stdout
        assert "DOCKER_HOST=unix:///var/run/docker.sock" in result.stdout
        assert "CHILD_DOCKER_HOST=unix:///var/run/docker.sock" in result.stdout

    def test_sapphire_docker_bin_overrides_the_default(self) -> None:
        env = {**_clean_base_env(), "SAPPHIRE_DOCKER_BIN": "/opt/headless/bin/docker"}

        result = _source_and_report(env)

        assert result.returncode == 0, result.stderr
        assert "DOCKER_BIN=/opt/headless/bin/docker" in result.stdout
        # Overriding the binary must not disturb the (unset) host default.
        assert "DOCKER_HOST=unix:///var/run/docker.sock" in result.stdout

    def test_sapphire_docker_host_overrides_the_default_and_is_exported(self) -> None:
        env = {
            **_clean_base_env(),
            "SAPPHIRE_DOCKER_HOST": "tcp://headless-runtime:2375",
        }

        result = _source_and_report(env)

        assert result.returncode == 0, result.stderr
        assert "DOCKER_HOST=tcp://headless-runtime:2375" in result.stdout
        assert "CHILD_DOCKER_HOST=tcp://headless-runtime:2375" in result.stdout
        # Overriding the host must not disturb the (unset) binary default.
        assert "DOCKER_BIN=/usr/local/bin/docker" in result.stdout

    def test_both_overrides_apply_independently(self) -> None:
        env = {
            **_clean_base_env(),
            "SAPPHIRE_DOCKER_BIN": "/opt/headless/bin/docker",
            "SAPPHIRE_DOCKER_HOST": "tcp://headless-runtime:2375",
        }

        result = _source_and_report(env)

        assert result.returncode == 0, result.stderr
        assert "DOCKER_BIN=/opt/headless/bin/docker" in result.stdout
        assert "DOCKER_HOST=tcp://headless-runtime:2375" in result.stdout


# ---------- consumption by every wrapper -------------------------------------


def _write_fake_docker_logging_invocation(path: Path, *, marker: str) -> None:
    """A fake docker binary that appends an invocation record — MARKER, the
    DOCKER_HOST it was invoked with, and its argv — to ``$INVOCATION_LOG``,
    then exits 0 regardless of the subcommand. Deliberately dumb: this file
    only proves binary/host RESOLUTION reaches the invocation, not the
    exact command each wrapper builds (already locked elsewhere)."""
    stub = (
        "#!/bin/bash\n"
        f"printf '%s DOCKER_HOST=%s ARGS=%s\\n' \"{marker}\" "
        '"${DOCKER_HOST:-}" "$*" >> "$INVOCATION_LOG"\n'
        "exit 0\n"
    )
    path.write_text(stub)
    path.chmod(0o755)


def _start_sapphire_env(tmp_path: Path) -> dict[str, str]:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir(exist_ok=True)
    return {
        "SAPPHIRE_REPO_ROOT": str(repo_root),
        "SAPPHIRE_BACKUP_DIR": str(backup_dir),
    }


def _prune_docker_env(tmp_path: Path) -> dict[str, str]:
    # No sapphire container in `docker ps`'s (empty) output -> the
    # stack-down branch exits 0 right after the FIRST docker invocation,
    # which is exactly the one call this file needs to see.
    return {}


def _recap_probe_env(tmp_path: Path) -> dict[str, str]:
    key_file = tmp_path / "recap_key"
    key_file.write_text("fake-key\n")
    probe_script = tmp_path / "recap_probe_loop.py"
    probe_script.write_text("# stub probe script for wrapper tests\n")
    return {
        "RECAP_PROBE_KEY_FILE": str(key_file),
        "RECAP_PROBE_SCRIPT": str(probe_script),
        "RECAP_PROBE_HOST_LOG": str(tmp_path / "recap.jsonl"),
        "RECAP_PROBE_HOST_SUMMARY": str(tmp_path / "recap.summary.log"),
    }


def _nepal_forcing_env(tmp_path: Path) -> dict[str, str]:
    key_file = tmp_path / "nepal_key"
    key_file.write_text("fake-key\n")
    db_password_file = tmp_path / "nepal_db_password"
    db_password_file.write_text("fake-password\n")
    run_script = tmp_path / "nepal_forcing_run.py"
    run_script.write_text("# stub run script for wrapper tests\n")
    return {
        "NEPAL_KEY_FILE": str(key_file),
        "NEPAL_DB_PASSWORD_FILE": str(db_password_file),
        "NEPAL_RUN_SCRIPT": str(run_script),
        "NEPAL_HOST_LOG": str(tmp_path / "nepal.jsonl"),
        "NEPAL_HOST_SUMMARY": str(tmp_path / "nepal.summary.log"),
        # Bypasses the `docker inspect` image-resolution call entirely, so
        # the ONE docker invocation this file cares about is `docker run`.
        "NEPAL_IMAGE": "sapphire-flow:test",
    }


_WRAPPERS: list[tuple[str, Callable[[Path], dict[str, str]]]] = [
    ("start-sapphire.sh", _start_sapphire_env),
    ("prune-docker.sh", _prune_docker_env),
    ("run-recap-probe.sh", _recap_probe_env),
    ("run-nepal-forcing.sh", _nepal_forcing_env),
]


def _run(wrapper: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(wrapper)], capture_output=True, text=True, env=env
    )


class TestEveryWrapperSourcesTheSharedFile:
    """The behavioural tests below prove each wrapper RESOLVES the right
    endpoint — but a wrapper that duplicated the same
    SAPPHIRE_DOCKER_BIN/SAPPHIRE_DOCKER_HOST logic inline would satisfy them
    all while re-creating the drift this contract exists to prevent. That is
    not hypothetical: the recap probe's launchd wrapper drifted from the
    repo's copy in exactly that way and sat dead for 31 days (Plan 132).

    So assert the STRUCTURE too: each wrapper must `source` the shared file,
    and must not re-derive the endpoint itself.
    """

    @pytest.mark.parametrize("wrapper_name", [name for name, _ in _WRAPPERS])
    def test_wrapper_sources_docker_endpoint(self, wrapper_name: str) -> None:
        text = (_SCRIPTS_DIR / wrapper_name).read_text()
        assert 'source "$(dirname "${BASH_SOURCE[0]}")/docker-endpoint.sh"' in text, (
            f"{wrapper_name} must source the shared endpoint contract"
        )

    @pytest.mark.parametrize("wrapper_name", [name for name, _ in _WRAPPERS])
    def test_wrapper_does_not_re_derive_the_endpoint(self, wrapper_name: str) -> None:
        """Only docker-endpoint.sh may read the SAPPHIRE_DOCKER_* overrides."""
        text = (_SCRIPTS_DIR / wrapper_name).read_text()
        for override in ("SAPPHIRE_DOCKER_BIN", "SAPPHIRE_DOCKER_HOST"):
            assert override not in text, (
                f"{wrapper_name} reads {override} itself — that is the shared "
                "file's job, and duplicating it is how endpoints drift apart"
            )


class TestDockerEndpointConsumedByEveryWrapper:
    """Each wrapper enumerated in docker-endpoint.sh's own header comment
    must actually resolve its docker binary/host through the shared
    contract, not merely `source` the file and go on hardcoding the old
    endpoint anyway."""

    @pytest.mark.parametrize(("wrapper_name", "env_builder"), _WRAPPERS)
    def test_sapphire_docker_bin_reaches_invocation_when_docker_cmd_unset(
        self,
        tmp_path: Path,
        wrapper_name: str,
        env_builder: Callable[[Path], dict[str, str]],
    ) -> None:
        wrapper = _SCRIPTS_DIR / wrapper_name
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "docker"
        _write_fake_docker_logging_invocation(fake, marker="RIGHT")
        invocation_log = tmp_path / "invocations.log"

        env = {
            **os.environ,
            **env_builder(tmp_path),
            "SAPPHIRE_DOCKER_BIN": str(fake),
            "INVOCATION_LOG": str(invocation_log),
        }
        env.pop("DOCKER_CMD", None)
        env.pop("SAPPHIRE_DOCKER_HOST", None)

        result = _run(wrapper, env)

        assert result.returncode == 0, result.stderr
        assert invocation_log.exists(), (
            f"{wrapper_name} never invoked the SAPPHIRE_DOCKER_BIN-resolved "
            "docker binary — it is not consuming docker-endpoint.sh's "
            "DOCKER_BIN"
        )
        assert "RIGHT" in invocation_log.read_text()

    @pytest.mark.parametrize(("wrapper_name", "env_builder"), _WRAPPERS)
    def test_docker_cmd_still_wins_over_sapphire_docker_bin(
        self,
        tmp_path: Path,
        wrapper_name: str,
        env_builder: Callable[[Path], dict[str, str]],
    ) -> None:
        wrapper = _SCRIPTS_DIR / wrapper_name
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        wrong = bin_dir / "docker-wrong"
        right = bin_dir / "docker-right"
        _write_fake_docker_logging_invocation(wrong, marker="WRONG")
        _write_fake_docker_logging_invocation(right, marker="RIGHT")
        invocation_log = tmp_path / "invocations.log"

        env = {
            **os.environ,
            **env_builder(tmp_path),
            "SAPPHIRE_DOCKER_BIN": str(wrong),
            "DOCKER_CMD": str(right),
            "INVOCATION_LOG": str(invocation_log),
        }
        env.pop("SAPPHIRE_DOCKER_HOST", None)

        result = _run(wrapper, env)

        assert result.returncode == 0, result.stderr
        log_text = invocation_log.read_text() if invocation_log.exists() else ""
        assert "RIGHT" in log_text
        assert "WRONG" not in log_text, (
            f"{wrapper_name} invoked the SAPPHIRE_DOCKER_BIN binary instead "
            "of honouring DOCKER_CMD's precedence over it"
        )

    @pytest.mark.parametrize(("wrapper_name", "env_builder"), _WRAPPERS)
    def test_sapphire_docker_host_is_exported_to_the_invoked_docker(
        self,
        tmp_path: Path,
        wrapper_name: str,
        env_builder: Callable[[Path], dict[str, str]],
    ) -> None:
        wrapper = _SCRIPTS_DIR / wrapper_name
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "docker"
        _write_fake_docker_logging_invocation(fake, marker="RIGHT")
        invocation_log = tmp_path / "invocations.log"

        env = {
            **os.environ,
            **env_builder(tmp_path),
            "SAPPHIRE_DOCKER_BIN": str(fake),
            "SAPPHIRE_DOCKER_HOST": "tcp://headless-runtime:2375",
            "INVOCATION_LOG": str(invocation_log),
        }
        env.pop("DOCKER_CMD", None)

        result = _run(wrapper, env)

        assert result.returncode == 0, result.stderr
        assert invocation_log.exists()
        log_text = invocation_log.read_text()
        assert "DOCKER_HOST=tcp://headless-runtime:2375" in log_text, (
            f"{wrapper_name} did not forward SAPPHIRE_DOCKER_HOST's resolved "
            "DOCKER_HOST to the docker invocation"
        )
