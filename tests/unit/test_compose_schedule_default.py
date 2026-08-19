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


class TestComposeBafuObservationScheduleDefault:
    """Plan 176 T2 (D1), tightened by Plan 189 T2 — the SAME class of no-op
    the sibling test above guards against: ``docker-compose.yml``'s
    init-container env fallback is the ONE that actually deploys
    (``cli/register_deployments.py``'s Python default is a fallback for a
    non-compose run only). A code-only cron change here would leave the
    mini on the old, looser cadence forever.

    MUST FAIL while the compose fallback is Plan 176's ``max<=4/min>=3``
    list: Plan 189 T2 tightened the bound to ``max<=3/min>=2`` after Plan
    176 T7's 45-min live run measured a 7.0 min minimum publish gap."""

    def test_compose_bafu_observation_fallback_satisfies_d1_properties(self) -> None:
        from sapphire_flow.cli.register_deployments import (
            _build_specs as _register_deployments_build_specs,
        )
        from tests.unit.cli.test_register_deployments import (
            _cron_minute_set,
            _cyclic_gaps,
        )

        compose = yaml.safe_load((_repo_root() / "docker-compose.yml").read_text())
        env = compose["services"]["init"]["environment"]
        raw = env["SCHEDULE_COLLECT_BAFU_OBSERVATIONS"]
        assert raw.startswith("${SCHEDULE_COLLECT_BAFU_OBSERVATIONS:-")
        assert raw.endswith("}")
        compose_cron = raw.removeprefix(
            "${SCHEDULE_COLLECT_BAFU_OBSERVATIONS:-"
        ).removesuffix("}")

        # The compose fallback must MATCH the code default exactly (Plan 175's
        # blocker: two places for one cron default drifting apart) ...
        by_name = {s.deployment_name: s for s in _register_deployments_build_specs()}
        assert compose_cron == by_name["collect-bafu-observations"].cron

        # ... and that shared value must itself satisfy Plan 189 T2's
        # tightened properties.
        minutes = _cron_minute_set(compose_cron)
        assert max(_cyclic_gaps(minutes)) <= 3
        assert min(_cyclic_gaps(minutes)) >= 2
        assert all(m % 5 != 0 for m in minutes)
