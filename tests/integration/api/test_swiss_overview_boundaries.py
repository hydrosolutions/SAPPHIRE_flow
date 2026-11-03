# ruff: noqa: F811 -- shared isolated assignment fixture
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from sapphire_flow.api import deps
from sapphire_flow.api.errors import http_exception_handler
from sapphire_flow.api.publication_cache import PublicationCacheMiddleware
from sapphire_flow.api.publication_gate import get_publication_gate
from sapphire_flow.api.routes import api_swiss_overview as route
from sapphire_flow.api.security import load_access_token_pepper
from sapphire_flow.config.swiss_overview import SwissOverviewConfig
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.daily_forecast_claim import DailyClaim
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberState,
    DailyOutputReference,
    DailyOutputSource,
)
from sapphire_flow.types.daily_input_provenance import DailyInputProvenance
from sapphire_flow.types.daily_output import DailyOutputContract, DailyOutputEvidence
from sapphire_flow.types.enums import AccessTokenRole, QcStatus
from sapphire_flow.types.evaluation_forecast import EvaluationReadSettings
from sapphire_flow.types.forecast_reference import ForecastOutputKey
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.swiss_overview import SwissRosterEntry
from tests.conftest import make_forecast_ensemble, make_observation, make_station_config
from tests.integration.api.test_access_token_auth import _auth, _make_token
from tests.integration.store.test_bafu_unit_read_filter import hold
from tests.integration.store.test_daily_forecast_claim_store import (
    assignment as assignment,
)
from tests.integration.store.test_forecast_store import _make_forecast

_PATH = "/api/v1/swiss/overview"


@pytest.fixture
def boundary(
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[SimpleNamespace]:
    key, frozen = assignment
    with isolated_committed_db_engine.connect() as conn, conn.begin():
        stations = [
            make_station_config(
                station_id=StationId(uuid4()),
                code=f"scope-{key.tenant_id}-{i}",
                tenant_id=key.tenant_id,
            )
            for i in range(134)
        ]
        extra = make_station_config(
            station_id=StationId(uuid4()),
            code=f"unlisted-{key.tenant_id}",
            tenant_id=key.tenant_id,
        )
        nepal = replace(
            extra,
            id=StationId(uuid4()),
            code=f"nepal-synthetic-{key.tenant_id}",
            network="dhm",
        )
        station_store = PgStationStore(conn)
        for station in [*stations, extra, nepal]:
            station_store.store_station(station)
        obs = PgObservationStore(conn)
        now = key.context.issue_time + timedelta(hours=1, minutes=30)
        for station in stations:
            obs.store_observations(
                [
                    replace(
                        make_observation(
                            station_id=station.id,
                            timestamp=now - timedelta(minutes=30),
                            qc_status=QcStatus.QC_PASSED,
                            value=42,
                        ),
                        id=uuid4(),
                    )
                ]
            )
        ensemble = make_forecast_ensemble(
            station_id=stations[0].id, model_id=key.model_id, n_steps=2, n_members=2
        )
        import polars as pl

        ensemble = replace(
            ensemble,
            issued_at=key.context.issue_time,
            values=ensemble.values.with_columns(
                pl.col("valid_time") + (key.context.issue_time - ensemble.issued_at)
            ),
        )
        record = replace(
            _make_forecast(stations[0].id, key.model_id, frozen.artifact_id),
            ensemble=ensemble,
            issued_at=key.context.issue_time,
            qc_status=QcStatus.QC_PASSED,
        )
        forecasts = PgForecastStore(conn, transaction_factory=lambda: nullcontext(conn))
        forecasts.store_forecast(record)
        contract = DailyOutputContract(
            parameter="discharge",
            units=ensemble.units,
            representation=ensemble.representation,
            time_step=ensemble.time_step,
            first_lead=timedelta(hours=1),
            horizon_steps=2,
            series_keys=(0.0, 1.0),
        )
        member = replace(
            frozen.members[0],
            station_id=stations[0].id,
            required_parameters=("discharge",),
        )
        frozen = replace(frozen, members=(member,), output_contracts=(contract,))
        daily = PgDailyForecastStore(lambda: nullcontext(conn))
        claim = daily.claim(
            key,
            frozen,
            attempt_id=uuid4(),
            owner_id=uuid4(),
            started_at=key.context.issue_time,
        )
        assert isinstance(claim, DailyClaim)
        ref = DailyOutputReference(
            key=ForecastOutputKey(
                station_id=record.station_id,
                model_id=record.model_id,
                issued_at=record.issued_at,
                parameter="discharge",
            ),
            source=DailyOutputSource.FORECAST,
            output_id=record.id,
        )
        provenance = DailyInputProvenance(
            nwp_source=record.nwp_cycle_source,
            nwp_reference_time=record.nwp_cycle_reference_time,
            observation_staleness_hours=record.observation_staleness_hours,
        )
        daily.finalize(
            claim,
            outcomes=(
                replace(
                    member,
                    state=DailyMemberState.PRODUCED,
                    references=(ref,),
                    provenance=provenance,
                ),
            ),
            evidence=(
                DailyOutputEvidence(
                    reference=ref,
                    ensemble=ensemble,
                    qc_status=record.qc_status,
                    qc_flags=(),
                    provenance=provenance,
                ),
            ),
            finished_at=key.context.issue_time,
        )
        settings = EvaluationReadSettings(
            tenant_id=key.tenant_id,
            deployment=key.deployment,
            group_ids=(key.group_id,),
            daily_model_id=key.model_id,
            parameters=("discharge",),
        )
        state = SimpleNamespace(
            conn=conn,
            stations=stations,
            extra=extra,
            nepal=nepal,
            now=now,
            active=False,
            record=record,
        )
        state.roster = tuple(
            SwissRosterEntry(station_id=s.id, network=s.network, code=s.code)
            for s in stations
        )
        state.config = SimpleNamespace(
            swiss_overview=SwissOverviewConfig(
                roster_count=134, roster_sha256="a" * 64
            ),
            evaluation_forecasts=SimpleNamespace(to_domain=lambda: settings),
            bafu_forecast_archive_path=None,
        )
        monkeypatch.setattr(route, "load_swiss_roster", lambda _: state.roster)
        app = FastAPI()
        app.include_router(route.router)
        app.add_exception_handler(HTTPException, http_exception_handler)
        app.add_middleware(PublicationCacheMiddleware)
        app.state.access_token_pepper = load_access_token_pepper()
        app.dependency_overrides.update(
            {
                deps.get_connection: lambda: conn,
                deps.get_stores: lambda: dict(
                    station_store=station_store,
                    obs_store=obs,
                    forecast_store=forecasts,
                    rejected_forecast_store=PgRejectedForecastStore(
                        conn, transaction_factory=None
                    ),
                    group_store=PgStationGroupStore(conn),
                ),
                deps.get_daily_evaluation_reader: lambda: daily,
                deps.get_deployment_config: lambda: state.config,
                deps.get_evaluation_clock: lambda: now,
                get_publication_gate: lambda: SimpleNamespace(
                    active=lambda _: state.active
                ),
            }
        )
        with TestClient(app) as client:
            state.client = client
            yield state


class TestSwissOverviewBoundaries:
    @pytest.mark.parametrize("role", [AccessTokenRole.REVIEWER, AccessTokenRole.ADMIN])
    def test_manifest_intersection_and_exact_gate_redaction(
        self, boundary: SimpleNamespace, role: AccessTokenRole
    ) -> None:
        b = boundary
        grants = (
            frozenset([*(s.id for s in b.stations[:-1]), b.extra.id, b.nepal.id])
            if role is AccessTokenRole.REVIEWER
            else frozenset()
        )
        token = _make_token(
            b.conn,
            role=role,
            tenant_id=b.stations[0].tenant_id
            if role is AccessTokenRole.REVIEWER
            else None,
            station_ids=grants,
        )
        b.active = True
        response = b.client.get(_PATH, headers=_auth(token))
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        expected = b.stations[:-1] if role is AccessTokenRole.REVIEWER else b.stations
        assert body["station_count"] == len(expected)
        assert {i["station"]["id"] for i in body["items"]} == {
            str(s.id) for s in expected
        }
        assert all(
            i["bafu_comparison"]
            == {"state": "withheld", "reason": "publication_gate", "source": "bafu"}
            for i in body["items"]
        )
        forecast = body["items"][0]["forecast"]
        if role is AccessTokenRole.REVIEWER:
            assert forecast == {
                "state": "withheld",
                "reason": "publication_gate",
                "output_id": str(b.record.id),
                "output_source": "forecast",
                "qc_status": "qc_passed",
                "model_id": str(b.record.model_id),
                "issued_at": b.record.issued_at.isoformat().replace("+00:00", "Z"),
            }
        else:
            assert forecast["state"] == "available"
            assert forecast["peak_value"] is not None

    @pytest.mark.parametrize("role", [AccessTokenRole.REVIEWER, AccessTokenRole.ADMIN])
    def test_hold_rechecked_on_every_request(
        self, boundary: SimpleNamespace, role: AccessTokenRole
    ) -> None:
        b = boundary
        s = b.stations[0]
        token = _make_token(
            b.conn,
            role=role,
            tenant_id=s.tenant_id if role is AccessTokenRole.REVIEWER else None,
            station_ids=frozenset({s.id})
            if role is AccessTokenRole.REVIEWER
            else frozenset(),
        )

        def observation() -> dict:
            response = b.client.get(_PATH, headers=_auth(token))
            assert response.status_code == 200
            return response.json()["items"][0]["observation"]

        assert observation()["latest"]["value"] == 42
        hold(b.conn, (s.id, s.tenant_id), start=b.now - timedelta(hours=2))
        hidden = observation()
        assert hidden["availability"] == "unavailable"
        assert hidden["latest"] is None
        assert all(h["sample"] is None for h in hidden["hourly"])

    @pytest.mark.parametrize(
        "failure", ["missing_token", "invalid_token", "consumer", "metadata"]
    )
    def test_failures_precede_numeric_reads(
        self, boundary: SimpleNamespace, failure: str
    ) -> None:
        b = boundary
        token = _make_token(
            b.conn,
            role=AccessTokenRole.CONSUMER
            if failure == "consumer"
            else AccessTokenRole.REVIEWER,
            tenant_id=b.stations[0].tenant_id,
            station_ids=frozenset({b.stations[0].id}),
        )
        headers = (
            {}
            if failure == "missing_token"
            else _auth("invalid.synthetic" if failure == "invalid_token" else token)
        )
        if failure == "metadata":
            b.roster = (replace(b.roster[0], code="drift"),)
        queries = []

        def capture(
            _conn: object,
            _cursor: object,
            statement: str,
            _params: object,
            _ctx: object,
            _many: object,
        ) -> None:
            queries.append(statement.lower())

        sa.event.listen(b.conn, "before_cursor_execute", capture)
        try:
            response = b.client.get(_PATH, headers=headers)
        finally:
            sa.event.remove(b.conn, "before_cursor_execute", capture)
        assert (
            response.status_code
            == {
                "missing_token": 401,
                "invalid_token": 401,
                "consumer": 403,
                "metadata": 503,
            }[failure]
        )
        assert response.headers["cache-control"] == "no-store"
        assert not any(
            "from observations" in q
            or "from forecasts" in q
            or "from rejected_forecasts" in q
            or "from daily_forecast" in q
            for q in queries
        )

    def test_other_tenant_reviewer_cannot_enumerate_manifest(
        self, boundary: SimpleNamespace
    ) -> None:
        from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

        token = _make_token(
            boundary.conn, role=AccessTokenRole.REVIEWER, tenant_id=DEFAULT_TENANT_ID
        )
        response = boundary.client.get(_PATH, headers=_auth(token))
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["station_count"] == 0
