from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from sapphire_flow.store.observation_store import PgObservationStore
from tests.integration.db.test_bafu_ingest_dispatch import (
    batch as batch,
)
from tests.integration.db.test_bafu_ingest_dispatch import (
    bootstrapped as bootstrapped,
)
from tests.integration.db.test_bafu_ingest_dispatch import (
    isolated_committed_db_engine as isolated_committed_db_engine,
)
from tests.integration.db.test_bafu_ingest_dispatch import (
    protected as protected,
)
from tests.integration.db.test_bafu_ingest_dispatch import (
    role_harness as role_harness,
)
from tests.integration.db.test_bafu_ingest_dispatch import (
    worker as worker,
)
from tests.integration.store.test_bafu_unit_writer import QUOTA

if TYPE_CHECKING:
    import pytest
    import sqlalchemy as sa

    from sapphire_flow.types.bafu_capture_batch import BafuStationCaptureBatch


def test_environment_composition_uses_real_adapter_and_worker_stores(
    worker: sa.Engine,
    protected: BafuStationCaptureBatch,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import json
    from dataclasses import asdict

    import httpx
    import psycopg
    import yaml

    import sapphire_flow.flows.ingest_observations as flow_module
    from sapphire_flow.flows.ingest_observations import ingest_observations_flow

    # Run the real task bodies without starting a Prefect service.
    for name in (
        "_fetch_observations_task",
        "_store_raw_task",
        "_run_qc_task",
        "_derive_calculated_task",
    ):
        task = getattr(flow_module, name)
        monkeypatch.setattr(flow_module, name, task.fn)
    from sapphire_flow.store.pipeline_health_store import PgPipelineHealthStore
    from sapphire_flow.types.domain import QcRuleSet
    from sapphire_flow.types.enums import PipelineCheckType, PipelineHealthStatus

    # The actual shipped worker URL and both production factories use psycopg3.
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())
    templates = [
        service.get("environment", {}).get("DATABASE_URL_TEMPLATE", "")
        for service in compose["services"].values()
    ]
    assert "postgresql+psycopg://sapphire_worker@postgres:5432/sapphire" in templates
    assert worker.url.drivername == "postgresql+psycopg"
    with worker.connect() as conn:
        assert isinstance(conn.connection.driver_connection, psycopg.Connection)

    rule = protected.registry.rules[0]
    registry = {
        "schema_version": 1,
        "source": "lindas_foen_hydro",
        "parameter": "discharge",
        "version": protected.registry.version,
        "rules": [
            {
                "rule_id": rule.rule_id,
                "gauge_code": rule.key.gauge.value,
                "kind": rule.key.kind,
                "unit": rule.unit.value,
                "valid_from": rule.valid_from.isoformat(),
                "valid_until": None,
                "verified_at": rule.verified_at.isoformat(),
                "refresh_due": rule.refresh_due.isoformat(),
                "evidence_reference": rule.evidence_reference,
                "evidence_sha256": rule.evidence_sha256,
            }
        ],
    }
    config = tmp_path / "units.json"
    config.write_text(json.dumps({"registry": registry, "quota": asdict(QUOTA)}))
    monkeypatch.setenv("DATABASE_URL", worker.url.render_as_string(hide_password=False))
    monkeypatch.setenv("SAPPHIRE_BAFU_UNIT_INGEST_CONFIG", str(config))
    monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "results": {
                    "bindings": [
                        {
                            "subject": {
                                "value": "https://environment.ld.admin.ch/foen/hydro/river/observation/"
                                + rule.key.gauge.value
                            },
                            "predicate": {
                                "value": "https://environment.ld.admin.ch/foen/hydro/dimension/"
                                + parameter
                            },
                            "object": {"value": value},
                        }
                        for parameter, value in (
                            ("measurementTime", protected.received_at.isoformat()),
                            ("discharge", "22"),
                        )
                    ]
                }
            },
        )

    client_class = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client_class(**kwargs, transport=httpx.MockTransport(handler)),
    )
    result = ingest_observations_flow.fn(
        clock=lambda: protected.received_at,
        qc_rules=QcRuleSet(version="none", rules=()),
    )
    assert len(calls) == 1
    assert result.observations_stored == 1
    assert result.bafu_unit_results[0].observations_written == 1
    with worker.connect() as conn:
        rows = PgObservationStore(conn).fetch_observations(
            protected.station_id,
            "discharge",
            protected.received_at,
            protected.received_at + timedelta(days=1),
        )
        assert [row.value for row in rows] == [0.022]
        record = PgPipelineHealthStore(conn).fetch_recent(
            PipelineCheckType.OBSERVATION_INGEST_FETCH, limit=1
        )[0]
        assert record.status is PipelineHealthStatus.OK
        assert record.detail["unit_capture_results"][0]["status"] == "committed"
