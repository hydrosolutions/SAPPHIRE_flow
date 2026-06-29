"""LOCKED regression tests for the ingest poll cadence default.

Milestone: obs-ingest-upsert-cadence.

Under the snapshot-only LINDAS adapter (no backfill), a ``*/30`` poll
permanently misses readings. The default ingest cron must be ``*/5 * * * *``
in the code default (cli/register_deployments.py); an explicit env override
must still be honoured.

MUST FAIL while the default is ``*/30 * * * *``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sapphire_flow.cli.register_deployments import _build_specs

if TYPE_CHECKING:
    import pytest


class TestIngestScheduleDefault:
    def test_default_ingest_cron_is_five_minutes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SCHEDULE_INGEST_OBSERVATIONS", raising=False)

        specs = _build_specs()
        by_name = {s.deployment_name: s for s in specs}

        assert by_name["ingest-observations"].cron == "*/5 * * * *"

    def test_env_override_still_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHEDULE_INGEST_OBSERVATIONS", "*/15 * * * *")

        specs = _build_specs()
        by_name = {s.deployment_name: s for s in specs}

        assert by_name["ingest-observations"].cron == "*/15 * * * *"
