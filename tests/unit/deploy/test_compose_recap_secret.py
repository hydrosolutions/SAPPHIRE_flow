"""Plan 082 Task 2A: recap Data Gateway API-key Docker secret wiring.

Structural YAML-parse checks (never a substring scan) — fails against today's
``db_password``-only compose.
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


class TestComposeRecapSecret:
    def test_top_level_secret_declared(self) -> None:
        compose = _compose()
        secrets = compose["secrets"]
        assert "sapphire_dg_api_key" in secrets
        assert secrets["sapphire_dg_api_key"]["file"] == "./secrets/sapphire_dg_api_key"

    def test_prefect_worker_declares_the_secret(self) -> None:
        compose = _compose()
        worker_secrets = compose["services"]["prefect-worker"]["secrets"]
        assert "sapphire_dg_api_key" in worker_secrets

    def test_prefect_worker_ingest_declares_the_secret(self) -> None:
        compose = _compose()
        ingest_secrets = compose["services"]["prefect-worker-ingest"]["secrets"]
        assert "sapphire_dg_api_key" in ingest_secrets
