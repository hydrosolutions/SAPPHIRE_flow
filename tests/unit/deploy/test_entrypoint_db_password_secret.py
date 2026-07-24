"""Plan 147 Slice D — entrypoint.sh's DB_PASSWORD_SECRET generalization.

docker/entrypoint.sh used to read a single hard-coded secret path
(``/run/secrets/db_password``). Distinct services now mount DISTINCT DB
credentials (owner / sapphire_api / sapphire_worker — docker-compose.yml), so
entrypoint.sh must read whichever secret ``$DB_PASSWORD_SECRET`` names,
defaulting to the pre-Slice-D path when unset (back-compat for any caller
that does not set it, e.g. `postgres`/`prefect-server`, which never run this
script at all, and any future caller that forgets to set it).

Runs the REAL entrypoint.sh as a subprocess with stub `chown`/`gosu` binaries
on PATH (so it never needs root or the real gosu package) and CMD = an
`env`-printing shell one-liner, so this is a genuine black-box exercise of
the script's password-resolution + DATABASE_URL-construction logic.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent
    raise FileNotFoundError("docker-compose.yml not found above test file")


@pytest.fixture
def stub_bin(tmp_path: Path) -> Path:
    """A PATH-prependable dir with no-op `chown` + passthrough `gosu` stubs."""
    bin_dir = tmp_path / "stub_bin"
    bin_dir.mkdir()
    (bin_dir / "chown").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "gosu").write_text('#!/bin/sh\nshift\nexec "$@"\n')
    for stub in ("chown", "gosu"):
        (bin_dir / stub).chmod(0o755)
    return bin_dir


def _run_entrypoint(
    stub_bin: Path,
    tmp_path: Path,
    *,
    env_overrides: dict[str, str],
    secret_files: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    entrypoint = _repo_root() / "docker" / "entrypoint.sh"
    assert entrypoint.is_file(), "docker/entrypoint.sh must exist"

    for rel_path, content in secret_files.items():
        secret_path = tmp_path / rel_path
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret_path.write_text(content)

    env = {
        "PATH": f"{stub_bin}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
    }
    env.update(env_overrides)

    return subprocess.run(
        [str(entrypoint), "sh", "-c", "printf '%s' \"$DATABASE_URL\""],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=10,
    )


class TestDbPasswordSecretIsConfigurable:
    def test_default_falls_back_to_db_password_env_when_secret_file_absent(
        self, stub_bin: Path, tmp_path: Path
    ) -> None:
        # DB_PASSWORD_SECRET is left UNSET. The default it must resolve to
        # (/run/secrets/db_password) genuinely does not exist inside this
        # sandboxed subprocess env, so the script's env-var fallback
        # (`${DB_PASSWORD:?...}`) is reached ONLY if the default path was
        # correctly assigned — proving the default, without writing to the
        # real host-absolute /run/secrets path.
        result = _run_entrypoint(
            stub_bin,
            tmp_path,
            env_overrides={
                "DATABASE_URL_TEMPLATE": "postgresql+psycopg://sapphire@postgres:5432/sapphire",
                "DB_PASSWORD": "owner-secret-pw-via-env-fallback",
            },
            secret_files={},
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == (
            "postgresql+psycopg://sapphire:owner-secret-pw-via-env-fallback"
            "@postgres:5432/sapphire"
        )

    def test_default_secret_path_is_the_owner_path(self) -> None:
        # Structural guard on the literal default, independent of whether
        # /run/secrets/db_password happens to exist on the host running the
        # test — the source line itself must name the pre-Slice-D path.
        entrypoint = _repo_root() / "docker" / "entrypoint.sh"
        text = entrypoint.read_text()
        expected = (
            'DB_PASSWORD_SECRET="${DB_PASSWORD_SECRET:-/run/secrets/db_password}"'
        )
        assert expected in text

    def test_named_secret_path_is_honored(self, stub_bin: Path, tmp_path: Path) -> None:
        secret_path = tmp_path / "run" / "secrets" / "sapphire_api_db_password"
        result = _run_entrypoint(
            stub_bin,
            tmp_path,
            env_overrides={
                "DATABASE_URL_TEMPLATE": "postgresql+psycopg://sapphire_api@postgres:5432/sapphire",
                "DB_PASSWORD_SECRET": str(secret_path),
            },
            secret_files={
                "run/secrets/sapphire_api_db_password": "api-role-secret-pw",
                # The owner secret is ALSO present (mirrors a misconfigured
                # mount) — entrypoint.sh must use the NAMED secret, never
                # silently fall back to the owner path when one is set.
                "run/secrets/db_password": "owner-secret-pw-should-not-be-used",
            },
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == (
            "postgresql+psycopg://sapphire_api:api-role-secret-pw@postgres:5432/sapphire"
        )

    def test_fails_closed_when_named_secret_missing_and_no_fallback_env(
        self, stub_bin: Path, tmp_path: Path
    ) -> None:
        result = _run_entrypoint(
            stub_bin,
            tmp_path,
            env_overrides={
                "DATABASE_URL_TEMPLATE": "postgresql+psycopg://sapphire_worker@postgres:5432/sapphire",
                "DB_PASSWORD_SECRET": str(
                    tmp_path / "run" / "secrets" / "sapphire_worker_db_password"
                ),
            },
            secret_files={},
        )
        assert result.returncode != 0
        assert "DB_PASSWORD" in result.stderr
