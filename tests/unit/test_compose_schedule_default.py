"""LOCKED regression test for the compose init-container poll cadence default.

Milestone: obs-ingest-upsert-cadence.

The deployed default cadence is the compose init-container fallback at
``docker-compose.yml`` (services.init.environment.SCHEDULE_INGEST_OBSERVATIONS).
A code-only change is a deployment no-op, so the compose fallback must also be
``*/5 * * * *`` (with the env override still expanded by compose).

MUST FAIL while the compose fallback is ``*/30 * * * *``.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            return parent
    raise FileNotFoundError("docker-compose.yml not found above test file")


class TestComposeScheduleDefault:
    def test_compose_ingest_fallback_is_five_minutes(self) -> None:
        compose = yaml.safe_load((_repo_root() / "docker-compose.yml").read_text())
        env = compose["services"]["init"]["environment"]
        assert (
            env["SCHEDULE_INGEST_OBSERVATIONS"]
            == "${SCHEDULE_INGEST_OBSERVATIONS:-*/5 * * * *}"
        )
