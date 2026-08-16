"""Plan 162 D2/T1 — the dedicated backup component. Structural checks that
`prefect-worker-backup` is the ONLY container holding the read-everything
`sapphire_backup` credential, that the default `prefect-worker` no longer
mounts the `backups` volume, and that the `backup-database` deployment
routes only to the dedicated `backup` pool.

Plan 162 T1 review fix (minor): asserts against the REAL RENDERED
configuration (`docker compose ... config --format json`), not a raw
per-file `yaml.safe_load`. A naive YAML parse cannot see Compose's own
variable interpolation or its overlay list-merge semantics, so it can miss
exactly the kind of drift this regression guard exists to catch — e.g. an
overlay silently re-adding the backup secret to a service, or an
interpolated `${VAR}` masking a real value mismatch. Requires the `docker`
CLI; skips (does not fail) if it is unavailable.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent
    raise FileNotFoundError("docker-compose.yml not found above test file")


def _rendered_compose(*filenames: str) -> dict[str, object]:
    """Render the given compose file(s), relative to the repo root, with
    `docker compose ... config --format json` — the merged, interpolated
    configuration a real deploy actually uses."""
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    root = _repo_root()
    args = ["docker", "compose"]
    for name in filenames:
        args += ["-f", str(root / name)]
    args += ["config", "--format", "json"]
    env = dict(os.environ)
    # `VERSION` is the only hard-required (`${VERSION:?...}`) interpolation
    # in these files — any value renders the same service graph.
    env.setdefault("VERSION", "0.0.0-test")
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell
        args, cwd=root, env=env, capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        pytest.fail(f"docker compose config failed: {result.stderr}")
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


@functools.cache
def _base_compose() -> dict[str, object]:
    return _rendered_compose("docker-compose.yml")


@functools.cache
def _macmini_compose() -> dict[str, object]:
    return _rendered_compose("docker-compose.yml", "docker-compose.macmini.yml")


def _services(compose: dict[str, object]) -> dict[str, dict[str, object]]:
    services = compose["services"]
    assert isinstance(services, dict)
    return services


def _service(compose: dict[str, object], name: str) -> dict[str, object]:
    svc = _services(compose)[name]
    assert isinstance(svc, dict)
    return svc


def _secret_names(svc: dict[str, object]) -> list[str]:
    secrets = svc.get("secrets") or []
    return [s["source"] if isinstance(s, dict) else s for s in secrets]


def _env(svc: dict[str, object]) -> dict[str, object]:
    environment = svc.get("environment") or {}
    assert isinstance(environment, dict)
    return environment


def _volume_targets(svc: dict[str, object]) -> list[str]:
    # Rendered `config --format json` always normalizes `volumes` entries
    # to objects with a `target` key (never the raw "source:target[:mode]"
    # string form a hand-parsed YAML file might show).
    volumes = svc.get("volumes") or []
    return [v["target"] for v in volumes if isinstance(v, dict) and "target" in v]


def _volume_sources(svc: dict[str, object]) -> list[str]:
    volumes = svc.get("volumes") or []
    return [v["source"] for v in volumes if isinstance(v, dict) and "source" in v]


def _command_tokens(svc: dict[str, object]) -> list[str]:
    # Rendered form is a list (`["prefect", "worker", "start", ...]`); guard
    # a plain string too so this helper degrades gracefully across Compose
    # versions rather than raising deep inside a test.
    command = svc.get("command")
    if isinstance(command, list):
        return [str(c) for c in command]
    if isinstance(command, str):
        return command.split()
    return []


class TestBackupSecretDeclared:
    def test_sapphire_backup_db_password_declared(self) -> None:
        secrets = _base_compose()["secrets"]
        assert isinstance(secrets, dict)
        assert secrets["sapphire_backup_db_password"]["file"] == str(
            _repo_root() / "secrets" / "sapphire_backup_db_password"
        )


class TestBackupSecretConsumerSetIsExact:
    """Plan 162 D2 regression guard: the backup secret must never silently
    spread to a container that also runs flow code / adapters — the whole
    point of the dedicated component."""

    def test_consumer_set_is_exactly_init_and_backup_worker(self) -> None:
        compose = _base_compose()
        consumers = {
            name
            for name, svc in _services(compose).items()
            if "sapphire_backup_db_password" in _secret_names(svc)
        }
        assert consumers == {"init", "prefect-worker-backup"}

    def test_api_does_not_mount_backup_secret(self) -> None:
        svc = _service(_base_compose(), "api")
        assert "sapphire_backup_db_password" not in _secret_names(svc)

    def test_default_worker_does_not_mount_backup_secret(self) -> None:
        svc = _service(_base_compose(), "prefect-worker")
        assert "sapphire_backup_db_password" not in _secret_names(svc)

    def test_ingest_worker_does_not_mount_backup_secret(self) -> None:
        svc = _service(_base_compose(), "prefect-worker-ingest")
        assert "sapphire_backup_db_password" not in _secret_names(svc)


class TestPrefectWorkerBackupService:
    def test_mounts_its_own_scoped_secret(self) -> None:
        svc = _service(_base_compose(), "prefect-worker-backup")
        assert "sapphire_backup_db_password" in _secret_names(svc)

    def test_declares_backup_pg_connection_env(self) -> None:
        svc = _service(_base_compose(), "prefect-worker-backup")
        env = _env(svc)
        assert env.get("SAPPHIRE_BACKUP_PGUSER") == "sapphire_backup"
        assert env.get("SAPPHIRE_BACKUP_PGDATABASE") == "sapphire"
        assert env.get("SAPPHIRE_BACKUP_DB_PASSWORD_FILE") == (
            "/run/secrets/sapphire_backup_db_password"
        )

    def test_constructs_no_url(self) -> None:
        # Plan 162 T2: no DATABASE_URL_TEMPLATE anywhere in this service —
        # the backup path never constructs or parses a URL. (DB_PASSWORD_SECRET
        # IS present — the shared entrypoint's password-file read needs it —
        # but with no DATABASE_URL_TEMPLATE it is never used to build a URL.)
        svc = _service(_base_compose(), "prefect-worker-backup")
        env = _env(svc)
        assert "DATABASE_URL_TEMPLATE" not in env

    def test_serves_only_the_backup_pool(self) -> None:
        svc = _service(_base_compose(), "prefect-worker-backup")
        tokens = _command_tokens(svc)
        assert "--pool" in tokens
        assert tokens[tokens.index("--pool") + 1] == "backup"

    def test_mounts_the_backups_volume(self) -> None:
        svc = _service(_base_compose(), "prefect-worker-backup")
        assert "/data/backups" in _volume_targets(svc)

    def test_depends_on_init_completed(self) -> None:
        svc = _service(_base_compose(), "prefect-worker-backup")
        depends_on = svc.get("depends_on")
        assert isinstance(depends_on, dict)
        assert depends_on["init"]["condition"] == "service_completed_successfully"


class TestDefaultWorkerNoLongerMountsBackups:
    def test_prefect_worker_does_not_mount_backups_volume(self) -> None:
        svc = _service(_base_compose(), "prefect-worker")
        assert "/data/backups" not in _volume_targets(svc)


class TestInitBootstrapsBackupRole:
    def test_init_mounts_backup_secret(self) -> None:
        svc = _service(_base_compose(), "init")
        assert "sapphire_backup_db_password" in _secret_names(svc)

    def test_init_declares_backup_password_file_env(self) -> None:
        svc = _service(_base_compose(), "init")
        assert _env(svc).get("SAPPHIRE_BACKUP_DB_PASSWORD_FILE") == (
            "/run/secrets/sapphire_backup_db_password"
        )


class TestBackupDeploymentTargetsOnlyTheBackupPool:
    def test_register_deployments_routes_backup_to_backup_pool(self) -> None:
        from sapphire_flow.cli.register_deployments import BACKUP_POOL, _build_specs

        by_name = {s.deployment_name: s for s in _build_specs()}
        assert by_name["backup-database"].work_pool_name == BACKUP_POOL
        assert by_name["backup-database"].work_pool_name == "backup"
        for name, spec in by_name.items():
            if name != "backup-database":
                assert spec.work_pool_name != BACKUP_POOL


class TestMacminiOverlayBindsBackupVolumeToTheDedicatedWorker:
    """Regression guard for the exact failure mode named in the plan: 7
    dumps landed on the boot disk because the bind mount pointed at the
    wrong container after the backup flow moved to a dedicated worker.

    Rendered against the REAL base+overlay merge (`docker compose -f
    docker-compose.yml -f docker-compose.macmini.yml config`) — the
    combination actually deployed (docs/deployment/mac-mini-staging.md),
    not each file parsed in isolation, which cannot see Compose's overlay
    list-merge semantics."""

    def test_prefect_worker_backup_gets_the_usb_bind_mount(self) -> None:
        svc = _service(_macmini_compose(), "prefect-worker-backup")
        assert "/Volumes/sapphire-backup/pg_dumps" in _volume_sources(svc)
        assert "/data/backups" in _volume_targets(svc)

    def test_default_prefect_worker_no_longer_gets_the_bind_mount(self) -> None:
        svc = _service(_macmini_compose(), "prefect-worker")
        assert "/Volumes/sapphire-backup/pg_dumps" not in _volume_sources(svc)
