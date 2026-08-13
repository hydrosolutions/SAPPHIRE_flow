"""Plan 082 Task 2A: recap Data Gateway API-key Docker secret wiring.

Owner decision (Codex round 3): the Recap API-key secret is NEPAL-ONLY. The
base ``docker-compose.yml`` declares NO recap secret (Swiss deploys need no
placeholder file); the ``docker-compose.recap.yml`` overlay re-adds it (top
level + both workers). Structural YAML-parse checks — no substring scans, no
docker dependency.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent
    raise FileNotFoundError("docker-compose.yml not found above test file")


def _base_compose() -> dict[str, object]:
    return yaml.safe_load((_repo_root() / "docker-compose.yml").read_text())


def _recap_overlay() -> dict[str, object]:
    return yaml.safe_load((_repo_root() / "docker-compose.recap.yml").read_text())


def _service_secret_names(compose: dict[str, object], service: str) -> list[str]:
    services = compose["services"]
    assert isinstance(services, dict)
    svc = services[service]
    assert isinstance(svc, dict)
    secrets = svc.get("secrets") or []
    # Short (str) or long (dict with 'source') secret syntax.
    return [s["source"] if isinstance(s, dict) else s for s in secrets]


class TestBaseComposeHasNoRecapSecret:
    """The base compose must NOT declare the Nepal-only recap secret anywhere —
    Swiss deployments then need no ``./secrets/sapphire_dg_api_key`` file."""

    def test_no_top_level_recap_secret(self) -> None:
        compose = _base_compose()
        secrets = compose.get("secrets") or {}
        assert isinstance(secrets, dict)
        assert "sapphire_dg_api_key" not in secrets
        # db_password stays — the base still declares its own secrets.
        assert "db_password" in secrets

    def test_prefect_worker_has_no_recap_secret(self) -> None:
        names = _service_secret_names(_base_compose(), "prefect-worker")
        assert "sapphire_dg_api_key" not in names
        # Plan 147 Slice D: the worker connects as the scoped `sapphire_worker`
        # role via its OWN credential, not the owner's `db_password`.
        assert "sapphire_worker_db_password" in names

    def test_prefect_worker_ingest_has_no_recap_secret(self) -> None:
        names = _service_secret_names(_base_compose(), "prefect-worker-ingest")
        assert "sapphire_dg_api_key" not in names
        assert "sapphire_worker_db_password" in names


class TestRecapOverlayDeclaresSecret:
    """The Nepal overlay re-adds the recap secret at the top level and on both
    worker services (Compose merges service ``secrets`` additively, so the
    overlay lists only the recap secret; db_password comes from the base)."""

    def test_overlay_top_level_secret_declared(self) -> None:
        overlay = _recap_overlay()
        secrets = overlay["secrets"]
        assert isinstance(secrets, dict)
        assert "sapphire_dg_api_key" in secrets
        assert secrets["sapphire_dg_api_key"]["file"] == "./secrets/sapphire_dg_api_key"

    def test_overlay_prefect_worker_declares_the_secret(self) -> None:
        names = _service_secret_names(_recap_overlay(), "prefect-worker")
        assert "sapphire_dg_api_key" in names

    def test_overlay_prefect_worker_ingest_declares_the_secret(self) -> None:
        names = _service_secret_names(_recap_overlay(), "prefect-worker-ingest")
        assert "sapphire_dg_api_key" in names
