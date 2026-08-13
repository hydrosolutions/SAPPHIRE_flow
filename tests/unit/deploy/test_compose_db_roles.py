"""Plan 147 Slice D — least-privilege DB role wiring, structural checks.

Locks the credential-separation invariant at the compose-config level: the
`api` / `prefect-worker` / `prefect-worker-ingest` containers must connect as
their OWN scoped role (`sapphire_api` / `sapphire_worker`) via a DEDICATED
password secret, and must NOT be able to reconstruct the owner/migration
`db_password` credential. Only `init` (migrations + the idempotent role
bootstrap) keeps the owner secret. Structural YAML-parse checks — no Docker
dependency, mirrors tests/unit/deploy/test_compose_recap_secret.py.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent
    raise FileNotFoundError("docker-compose.yml not found above test file")


def _compose() -> dict[str, object]:
    return yaml.safe_load((_repo_root() / "docker-compose.yml").read_text())


def _service(compose: dict[str, object], name: str) -> dict[str, object]:
    services = compose["services"]
    assert isinstance(services, dict)
    svc = services[name]
    assert isinstance(svc, dict)
    return svc


def _secret_names(svc: dict[str, object]) -> list[str]:
    secrets = svc.get("secrets") or []
    return [s["source"] if isinstance(s, dict) else s for s in secrets]


def _env(svc: dict[str, object]) -> dict[str, object]:
    environment = svc.get("environment") or {}
    assert isinstance(environment, dict)
    return environment


class TestTopLevelSecretsDeclareScopedRolePasswords:
    def test_sapphire_api_db_password_declared(self) -> None:
        secrets = _compose()["secrets"]
        assert isinstance(secrets, dict)
        assert secrets["sapphire_api_db_password"]["file"] == (
            "./secrets/sapphire_api_db_password"
        )

    def test_sapphire_worker_db_password_declared(self) -> None:
        secrets = _compose()["secrets"]
        assert isinstance(secrets, dict)
        assert secrets["sapphire_worker_db_password"]["file"] == (
            "./secrets/sapphire_worker_db_password"
        )

    def test_owner_db_password_still_declared(self) -> None:
        # The owner/migration secret is NOT removed — `init` still needs it.
        secrets = _compose()["secrets"]
        assert isinstance(secrets, dict)
        assert secrets["db_password"]["file"] == "./secrets/db_password"


class TestApiServiceUsesScopedRoleOnly:
    def test_api_does_not_mount_owner_secret(self) -> None:
        svc = _service(_compose(), "api")
        assert "db_password" not in _secret_names(svc), (
            "api must not mount the owner/migration db_password secret — it "
            "would let the API container reconstruct the owner credential"
        )

    def test_api_mounts_its_own_scoped_secret(self) -> None:
        svc = _service(_compose(), "api")
        assert "sapphire_api_db_password" in _secret_names(svc)

    def test_api_connects_as_sapphire_api_role(self) -> None:
        svc = _service(_compose(), "api")
        template = _env(svc).get("DATABASE_URL_TEMPLATE")
        assert isinstance(template, str)
        assert template.startswith("postgresql+psycopg://sapphire_api@"), template

    def test_api_db_password_secret_env_points_at_its_own_secret(self) -> None:
        svc = _service(_compose(), "api")
        assert _env(svc).get("DB_PASSWORD_SECRET") == (
            "/run/secrets/sapphire_api_db_password"
        )


class TestWorkerServicesUseScopedRoleOnly:
    def _assert_worker_wiring(self, service_name: str) -> None:
        svc = _service(_compose(), service_name)
        names = _secret_names(svc)
        assert "db_password" not in names, (
            f"{service_name} must not mount the owner/migration db_password secret"
        )
        assert "sapphire_worker_db_password" in names
        template = _env(svc).get("DATABASE_URL_TEMPLATE")
        assert isinstance(template, str)
        assert template.startswith("postgresql+psycopg://sapphire_worker@"), template
        assert _env(svc).get("DB_PASSWORD_SECRET") == (
            "/run/secrets/sapphire_worker_db_password"
        )

    def test_prefect_worker(self) -> None:
        self._assert_worker_wiring("prefect-worker")

    def test_prefect_worker_ingest(self) -> None:
        self._assert_worker_wiring("prefect-worker-ingest")


class TestInitServiceKeepsOwnerAndBootstrapsRoles:
    def test_init_keeps_owner_secret(self) -> None:
        svc = _service(_compose(), "init")
        assert "db_password" in _secret_names(svc)

    def test_init_mounts_both_scoped_role_secrets(self) -> None:
        svc = _service(_compose(), "init")
        names = _secret_names(svc)
        assert "sapphire_api_db_password" in names
        assert "sapphire_worker_db_password" in names

    def test_init_declares_scoped_password_file_env_vars(self) -> None:
        svc = _service(_compose(), "init")
        env = _env(svc)
        assert env.get("SAPPHIRE_API_DB_PASSWORD_FILE") == (
            "/run/secrets/sapphire_api_db_password"
        )
        assert env.get("SAPPHIRE_WORKER_DB_PASSWORD_FILE") == (
            "/run/secrets/sapphire_worker_db_password"
        )

    def test_init_still_connects_as_owner(self) -> None:
        # Migrations run as the owner role (plan §Slice D) — init's own
        # DATABASE_URL_TEMPLATE is unchanged (${DB_USER:-sapphire}).
        svc = _service(_compose(), "init")
        template = _env(svc).get("DATABASE_URL_TEMPLATE")
        assert isinstance(template, str)
        assert "${DB_USER:-sapphire}" in template

    def test_init_command_runs_bootstrap_after_migrations_before_deployments(
        self,
    ) -> None:
        svc = _service(_compose(), "init")
        command = svc["command"]
        assert isinstance(command, str)
        migrate_pos = command.find("alembic upgrade head")
        bootstrap_pos = command.find("/app/docker/bootstrap-roles.sh")
        deployments_pos = command.find(
            "python -m sapphire_flow.cli.register_deployments"
        )
        assert migrate_pos != -1, "init command must run alembic upgrade head"
        assert bootstrap_pos != -1, (
            "init command must run the least-privilege role bootstrap "
            "(docker/bootstrap-roles.sh)"
        )
        assert deployments_pos != -1
        assert migrate_pos < bootstrap_pos < deployments_pos, (
            "role bootstrap must run AFTER migrations (so grants cover every "
            "migrated table) and BEFORE deployment registration"
        )
