from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import polars as pl
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from sapphire_flow.api import app
from sapphire_flow.api.deps import get_connection, get_observation_clock
from sapphire_flow.api.security import hash_token
from sapphire_flow.db.metadata import (
    access_token_stations,
    access_tokens,
    model_assignments,
    station_thresholds,
    stations,
)
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import EnsembleRepresentation
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.store.test_native_rating_forecasts import native_pair
from tests.integration.store.test_rejected_forecast_store import savepoint_factory

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness


@pytest.fixture
def overview_case(
    role_harness: _RoleBootstrapHarness, request: pytest.FixtureRequest
) -> Iterator[tuple]:
    result = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert result.returncode == 0, result.stderr
    with role_harness.owner_engine.connect() as conn:
        transaction = conn.begin()
        ordinary, native = native_pair(conn)
        frame = pl.DataFrame(
            {
                "valid_time": [
                    native.issued_at + timedelta(days=day)
                    for day in range(10)
                    for _ in range(7)
                ],
                "quantile": [
                    q
                    for _ in range(10)
                    for q in (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
                ],
                "value": [
                    5 + day + q
                    for day in range(10)
                    for q in (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
                ],
            }
        )
        ensemble = replace(
            native.ensemble,
            values=frame,
            representation=EnsembleRepresentation.QUANTILES,
            time_step=timedelta(days=1),
            issued_at=native.issued_at,
        )
        native = replace(
            native, ensemble=ensemble, representation=EnsembleRepresentation.QUANTILES
        )
        ordinary = replace(
            ordinary, ensemble=ensemble, representation=EnsembleRepresentation.QUANTILES
        )
        if getattr(request, "param", "raw") == "unchecked":
            from sapphire_flow.types.enums import QcStatus

            native = replace(native, qc_status=QcStatus.QC_UNCHECKED)
        for record in (ordinary, native):
            PgForecastStore(
                conn,
                data_use=record.data_use,
                transaction_factory=savepoint_factory(conn),
            ).store_forecast(record)
        conn.execute(
            sa.insert(model_assignments).values(
                station_id=native.station_id,
                model_id=native.model_id,
                time_step=timedelta(days=1),
                status="active",
            )
        )
        station = conn.execute(
            sa.select(stations.c.network, stations.c.tenant_id).where(
                stations.c.id == native.station_id
            )
        ).one()
        token_id = uuid4()
        pepper = "overview-test-pepper"
        conn.execute(
            sa.insert(access_tokens).values(
                id=token_id,
                token_hash=hash_token("overview-secret", pepper=pepper),
                key_prefix="overview",
                name="overview-test",
                role="reviewer",
                tenant_id=station.tenant_id,
                pepper_version=1,
                expires_at=datetime(2099, 1, 1, tzinfo=UTC),
                created_at=native.issued_at,
            )
        )
        conn.execute(
            sa.insert(access_token_stations).values(
                token_id=token_id, station_id=native.station_id
            )
        )
        previous = app.dependency_overrides.copy()
        previous_pepper = getattr(app.state, "access_token_pepper", None)
        app.state.access_token_pepper = pepper
        app.dependency_overrides[get_connection] = lambda: conn
        app.dependency_overrides[get_observation_clock] = lambda: native.issued_at
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        client = TestClient(
            app,
            raise_server_exceptions=False,
            headers={"Authorization": "Bearer overview.overview-secret"},
        )
        try:
            yield conn, client, native, ordinary, station.network, token_id
        finally:
            client.close()
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
            app.state.access_token_pepper = previous_pepper
            transaction.rollback()


class TestOverviewMetadata:
    @pytest.mark.parametrize("role", ["reviewer", "admin", "consumer"])
    @pytest.mark.parametrize("datum", [None, 450.0])
    def test_datum_preserves_disclosure(
        self, overview_case: tuple, role: str, datum: float | None
    ) -> None:
        conn, client, native, _, network, token = overview_case
        conn.execute(sa.text("RESET ROLE"))
        conn.execute(
            sa.update(stations)
            .where(stations.c.id == native.station_id)
            .values(water_level_datum_masl=datum)
        )
        conn.execute(
            sa.update(access_tokens)
            .where(access_tokens.c.id == token)
            .values(
                role=role,
                tenant_id=None
                if role == "admin"
                else sa.select(stations.c.tenant_id)
                .where(stations.c.id == native.station_id)
                .scalar_subquery(),
            )
        )
        if role == "admin":
            conn.execute(
                sa.delete(access_token_stations).where(
                    access_token_stations.c.token_id == token
                )
            )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        response = client.get(f"/api/v1/networks/{network}/overview")
        assert response.status_code == 200, response.text
        metadata = next(
            item["water_level_datum"]
            for item in response.json()["items"]
            if item["station_id"] == str(native.station_id)
        )
        assert metadata == (
            {"availability": "not_disclosed"}
            if role == "consumer"
            else {"availability": "disclosed", "value_masl": datum}
        )

    @pytest.mark.parametrize("change", ["datum", "threshold"])
    def test_metadata_change_invalidates_etag(
        self, overview_case: tuple, change: str
    ) -> None:
        conn, client, native, _, network, _ = overview_case
        path = f"/api/v1/networks/{network}/overview"
        before = client.get(path)
        assert before.status_code == 200, before.text
        conn.execute(sa.text("RESET ROLE"))
        if change == "datum":
            conn.execute(
                sa.update(stations)
                .where(stations.c.id == native.station_id)
                .values(water_level_datum_masl=1234.5)
            )
        else:
            conn.execute(
                sa.delete(station_thresholds).where(
                    station_thresholds.c.station_id == native.station_id
                )
            )
            conn.execute(
                sa.insert(station_thresholds).values(
                    station_id=native.station_id,
                    parameter="water_level",
                    danger_level="warning",
                    value=2.5,
                    source="authority",
                )
            )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        after = client.get(path, headers={"If-None-Match": before.headers["etag"]})
        assert after.status_code == 200, after.text
        assert after.headers["etag"] != before.headers["etag"]
        assert (
            after.json()["items"][0]["water_level"]
            == before.json()["items"][0]["water_level"]
        )

    def test_thresholds_match_detail_and_are_sorted(self, overview_case: tuple) -> None:
        conn, client, native, _, network, _ = overview_case
        conn.execute(sa.text("RESET ROLE"))
        conn.execute(
            sa.delete(station_thresholds).where(
                station_thresholds.c.station_id == native.station_id
            )
        )
        conn.execute(
            sa.insert(station_thresholds),
            [
                dict(
                    station_id=native.station_id,
                    parameter=parameter,
                    danger_level=level,
                    value=value,
                    source="authority",
                )
                for parameter, level, value in [
                    ("water_level", "warning", 2.0),
                    ("water_level", "danger", 3.0),
                    ("discharge", "warning", 50.0),
                ]
            ],
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        response = client.get(f"/api/v1/networks/{network}/overview")
        assert response.status_code == 200, response.text
        rows = response.json()["items"][0]["thresholds"]
        detail = client.get(f"/api/v1/stations/{native.station_id}")
        assert detail.status_code == 200, detail.text
        assert rows == sorted(
            detail.json()["thresholds"],
            key=lambda row: (row["parameter"], row["danger_level"], row["source"]),
        )

    def test_role_change_invalidates_datum_representation(
        self, overview_case: tuple
    ) -> None:
        conn, client, _, _, network, token = overview_case
        path = f"/api/v1/networks/{network}/overview"
        conn.execute(sa.text("RESET ROLE"))
        conn.execute(
            sa.update(access_tokens)
            .where(access_tokens.c.id == token)
            .values(role="consumer")
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        consumer = client.get(path)
        assert consumer.status_code == 200, consumer.text
        assert consumer.json()["items"][0]["water_level_datum"] == {
            "availability": "not_disclosed"
        }
        conn.execute(sa.text("RESET ROLE"))
        conn.execute(
            sa.update(access_tokens)
            .where(access_tokens.c.id == token)
            .values(role="reviewer")
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        reviewer = client.get(path, headers={"If-None-Match": consumer.headers["etag"]})
        assert reviewer.status_code == 200, reviewer.text
        assert (
            reviewer.json()["items"][0]["water_level_datum"]["availability"]
            == "disclosed"
        )
        assert reviewer.headers["etag"] != consumer.headers["etag"]

    def test_empty_thresholds_are_explicit(self, overview_case: tuple) -> None:
        conn, client, native, _, network, _ = overview_case
        conn.execute(sa.text("RESET ROLE"))
        conn.execute(
            sa.delete(station_thresholds).where(
                station_thresholds.c.station_id == native.station_id
            )
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        response = client.get(f"/api/v1/networks/{network}/overview")
        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["thresholds"] == []

    def test_failed_threshold_read_is_not_empty_success(
        self, overview_case: tuple
    ) -> None:
        conn, client, _, _, network, _ = overview_case

        def fail_threshold_read(
            connection: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            if "FROM station_thresholds" in statement:
                raise sa.exc.OperationalError(
                    "test threshold read", None, RuntimeError("unavailable")
                )

        sa.event.listen(conn, "before_cursor_execute", fail_threshold_read)
        try:
            response = client.get(f"/api/v1/networks/{network}/overview")
            assert response.status_code == 503, response.text
            assert response.headers["cache-control"] == "no-store"
            assert "etag" not in response.headers
        finally:
            sa.event.remove(conn, "before_cursor_execute", fail_threshold_read)


class TestNetworkOverviewNativeRole:
    def test_scoped_provisional_nonempty_and_conditional(
        self, overview_case: tuple
    ) -> None:
        _, client, native, _, network, _ = overview_case
        path = f"/api/v1/networks/{network}/overview?data_use=provisional_rating"
        response = client.get(path)
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert len(item["water_level"]["hourly"]) == 72
        assert item["water_level"]["latest"]["sample"] is not None
        assert item["discharge"]["latest"]["sample"] is None
        assert item["forecasts"][0]["forecast_id"] == str(native.id)
        assert item["forecasts"][0]["peak"] is not None
        tag = response.headers["etag"]
        app.dependency_overrides[get_observation_clock] = lambda: ensure_utc(
            native.issued_at + timedelta(minutes=1)
        )
        checked = client.get(path, headers={"If-None-Match": tag})
        assert checked.status_code == 304, checked.text
        assert checked.content == b""

    @pytest.mark.parametrize("change", ["disable", "expire", "revoke"])
    def test_real_auth_revocation_before_304(
        self, overview_case: tuple, change: str
    ) -> None:
        conn, client, native, _, network, token = overview_case
        path = f"/api/v1/networks/{network}/overview"
        first = client.get(path)
        assert first.status_code == 200, first.text
        conn.execute(sa.text("RESET ROLE"))
        if change == "disable":
            conn.execute(
                sa.update(access_tokens)
                .where(access_tokens.c.id == token)
                .values(disabled_at=native.issued_at)
            )
        elif change == "expire":
            conn.execute(
                sa.update(access_tokens)
                .where(access_tokens.c.id == token)
                .values(expires_at=native.issued_at)
            )
        else:
            conn.execute(
                sa.delete(access_token_stations).where(
                    access_token_stations.c.token_id == token
                )
            )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        response = client.get(path, headers={"If-None-Match": first.headers["etag"]})
        assert response.status_code == (404 if change == "revoke" else 401), (
            response.text
        )
        assert response.headers["cache-control"] == "no-store"
        assert "etag" not in response.headers


class TestOverviewIsolationAndCors:
    def test_protected_supplied_discharge_is_detail_equivalent_only(
        self, overview_case: tuple
    ) -> None:
        import random

        from sapphire_flow.store.observation_store import PgObservationStore
        from sapphire_flow.types.dhm_delivery import DELIVERY_ID
        from sapphire_flow.types.enums import ObservationSource, QcStatus
        from tests.conftest import make_observation

        conn, client, native, _, network, token = overview_case
        conn.execute(sa.text("RESET ROLE"))
        supplied = replace(
            make_observation(
                station_id=native.station_id,
                timestamp=native.issued_at,
                value=25,
                rng=random.Random(811),
            ),
            source=ObservationSource.MANUAL_IMPORT,
            delivery_id=DELIVERY_ID,
        )
        failed = replace(
            make_observation(
                station_id=native.station_id,
                timestamp=ensure_utc(native.issued_at + timedelta(minutes=1)),
                value=99,
                qc_status=QcStatus.QC_FAILED,
                rng=random.Random(812),
            ),
            source=ObservationSource.MANUAL_IMPORT,
            delivery_id=DELIVERY_ID,
        )
        PgObservationStore(conn).store_observations([supplied, failed])
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        app.dependency_overrides[get_observation_clock] = lambda: ensure_utc(
            native.issued_at + timedelta(minutes=2)
        )
        path = f"/api/v1/networks/{network}/overview"
        reviewer = client.get(path)
        assert reviewer.status_code == 200, reviewer.text
        q = reviewer.json()["items"][0]["discharge"]
        assert q["latest"]["sample"]["source"] == "supplied"
        assert q["latest"]["sample"]["value"] == 25
        assert q["newer_failed"] is True
        conn.execute(sa.text("RESET ROLE"))
        conn.execute(
            sa.update(access_tokens)
            .where(access_tokens.c.id == token)
            .values(role="consumer")
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        consumer = client.get(path, headers={"If-None-Match": reviewer.headers["etag"]})
        assert consumer.status_code == 200, consumer.text
        q = consumer.json()["items"][0]["discharge"]
        assert q["latest"]["sample"] is None
        assert q["latest_failed_at"] is None
        assert all(slot["sample"] is None for slot in q["hourly"])
        denied = client.get(
            path + "?data_use=provisional_rating", headers={"If-None-Match": "*"}
        )
        assert denied.status_code == 403
        assert denied.headers["cache-control"] == "no-store"

    def test_route_only_cors_and_negotiated_gzip(self, overview_case: tuple) -> None:
        from sapphire_flow.api.cors import ScopedCorsMiddleware

        _, client, _, _, network, _ = overview_case
        wrapped = TestClient(
            ScopedCorsMiddleware(
                app,
                consumer_origins=["https://dashboard.example"],
                human_dashboard_origin=None,
            ),
            headers=client.headers,
            raise_server_exceptions=False,
        )
        try:
            path = f"/api/v1/networks/{network}/overview"
            headers = {
                "Origin": "https://dashboard.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization,If-None-Match",
            }
            preflight = wrapped.options(path, headers=headers)
            assert preflight.status_code == 200
            assert preflight.headers["cache-control"] == "no-store"
            legacy = wrapped.options("/api/v1/stations", headers=headers)
            assert legacy.status_code == 400
            first = wrapped.get(
                path,
                headers={
                    "Origin": "https://dashboard.example",
                    "Accept-Encoding": "gzip",
                },
            )
            assert first.status_code == 200, first.text
            assert first.headers["content-encoding"] == "gzip"
            assert int(first.headers["content-length"]) < len(first.content)
            checked = wrapped.get(
                path,
                headers={
                    "Origin": "https://dashboard.example",
                    "If-None-Match": first.headers["etag"],
                },
            )
            assert checked.status_code == 304
            assert checked.headers["access-control-expose-headers"] == "ETag, Date"
            assert set(checked.headers["vary"].split(", ")) >= {
                "Authorization",
                "Origin",
                "Accept-Encoding",
            }
        finally:
            wrapped.close()

    def test_hour_transition_invalidates_without_inserts(
        self, overview_case: tuple
    ) -> None:
        _, client, native, _, network, _ = overview_case
        path = f"/api/v1/networks/{network}/overview"
        first = client.get(path)
        assert first.status_code == 200
        app.dependency_overrides[get_observation_clock] = lambda: ensure_utc(
            native.issued_at + timedelta(hours=1)
        )
        second = client.get(path, headers={"If-None-Match": first.headers["etag"]})
        assert second.status_code == 200
        assert second.headers["etag"] != first.headers["etag"]


@pytest.fixture
def daily_overview_case(
    overview_case: tuple, request: pytest.FixtureRequest
) -> Iterator[tuple]:
    import json

    from sapphire_flow.api.deps import get_deployment_config
    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.config.evaluation_forecasts import EvaluationForecastsConfig
    from sapphire_flow.db.metadata import (
        daily_forecast_attempts,
        model_artifacts,
        station_groups,
    )
    from sapphire_flow.store.daily_forecast_documents import (
        contracts_json,
        outcomes_json,
    )
    from sapphire_flow.types.daily_forecast_outcome import (
        DailyMemberOutcome,
        DailyMemberState,
        DailyOutputReference,
        DailyOutputSource,
    )
    from sapphire_flow.types.daily_output import DailyOutputContract
    from sapphire_flow.types.enums import QcStatus
    from sapphire_flow.types.forecast_reference import ForecastOutputKey
    from sapphire_flow.types.ids import ForecastId
    from tests.integration.store.test_forecast_store import _seed_artifact, _seed_model

    conn, client, native, ordinary, network, token = overview_case
    conn.execute(sa.text("RESET ROLE"))
    tenant = conn.scalar(
        sa.select(stations.c.tenant_id).where(stations.c.id == ordinary.station_id)
    )
    model = _seed_model(conn, "cmal_small")
    group = uuid4()
    conn.execute(
        sa.insert(station_groups).values(id=group, tenant_id=tenant, name=str(group))
    )
    artifact = _seed_artifact(conn, ordinary.station_id, model)
    conn.execute(
        sa.update(model_artifacts)
        .where(model_artifacts.c.id == artifact)
        .values(station_id=None, group_id=group)
    )
    record = replace(
        ordinary,
        id=ForecastId(uuid4()),
        model_id=model,
        model_artifact_id=artifact,
        qc_status=QcStatus.QC_PASSED,
        ensemble=replace(ordinary.ensemble, model_id=model),
    )
    PgForecastStore(conn, transaction_factory=savepoint_factory(conn)).store_forecast(
        record
    )
    conn.execute(
        sa.insert(model_assignments).values(
            station_id=record.station_id,
            model_id=model,
            time_step=timedelta(days=1),
            status="active",
        )
    )
    contract = DailyOutputContract(
        parameter="discharge",
        units="m³/s",
        representation=record.representation,
        time_step=timedelta(days=1),
        first_lead=timedelta(0),
        horizon_steps=10,
        series_keys=(0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95),
    )
    reference = DailyOutputReference(
        key=ForecastOutputKey(
            station_id=record.station_id,
            model_id=model,
            issued_at=record.issued_at,
            parameter="discharge",
        ),
        source=DailyOutputSource.FORECAST,
        output_id=record.id,
    )
    if getattr(request, "param", "accepted") == "rejected":
        from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
        from tests.integration.store.test_rejected_forecast_store import _entry

        entry = _entry(
            station_id=record.station_id, model_id=model, issued_at=record.issued_at
        )
        entry = replace(
            entry,
            payload=replace(entry.payload, model_artifact_id=artifact, group_id=group),
        )
        rejected_ids = PgRejectedForecastStore(
            conn, transaction_factory=savepoint_factory(conn)
        ).write_committed(entry)
        reference = replace(
            reference,
            source=DailyOutputSource.REJECTED,
            output_id=next(iter(rejected_ids.values())),
        )
    member = DailyMemberOutcome(
        station_id=record.station_id,
        required_parameters=("discharge",),
        state=DailyMemberState.PRODUCED,
        references=(reference,),
    )
    attempt = uuid4()
    conn.execute(
        sa.insert(daily_forecast_attempts).values(
            id=attempt,
            tenant_id=tenant,
            deployment="test",
            group_id=group,
            model_id=model,
            data_use="standard",
            issued_at=record.issued_at,
            trigger_kind="scheduled",
            scheduled_tick=record.issued_at,
            ordinal=1,
            owner_id=uuid4(),
            started_at=record.issued_at,
            finished_at=record.issued_at,
            state="complete",
            artifact_id=artifact,
            configuration_sha256="a" * 64,
            roster=[
                {
                    "station_id": str(record.station_id),
                    "required_parameters": ["discharge"],
                }
            ],
            output_contracts=json.loads(contracts_json((contract,))),
            summary=json.loads(outcomes_json((member,))),
        )
    )
    config = DeploymentConfig(
        max_retention_days=2193,
        evaluation_forecasts=EvaluationForecastsConfig(
            tenant_id=tenant, deployment="test", group_ids=(group,)
        ),
    )
    app.dependency_overrides[get_deployment_config] = lambda: config
    conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
    yield conn, client, record, ordinary, network, token, tenant, attempt, member


class TestDailyOverviewAuthority:
    @pytest.mark.parametrize("role", ["consumer", "reviewer", "admin"])
    def test_publication_intersection_before_daily_values(
        self, daily_overview_case: tuple, role: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.api.publication_gate import (
            PublicationGate,
            get_publication_gate,
        )
        from sapphire_flow.db.metadata import forecast_publication_selections

        conn, client, record, other, network, token, tenant, _, _ = daily_overview_case
        original = PgForecastStore.fetch_bounded_forecasts_by_ids
        selected: list = []

        def capture(store: PgForecastStore, ids: list) -> dict:
            selected.extend(ids)
            return original(store, ids)

        monkeypatch.setattr(PgForecastStore, "fetch_bounded_forecasts_by_ids", capture)
        conn.execute(sa.text("RESET ROLE"))
        if role == "admin":
            conn.execute(
                sa.delete(access_token_stations).where(
                    access_token_stations.c.token_id == token
                )
            )
        conn.execute(
            sa.update(access_tokens)
            .where(access_tokens.c.id == token)
            .values(role=role, tenant_id=None if role == "admin" else tenant)
        )
        conn.execute(
            sa.insert(forecast_publication_selections).values(
                tenant_id=tenant,
                station_id=record.station_id,
                parameter="discharge",
                issued_at=record.issued_at,
                selected_forecast_id=other.id,
                version=1,
            )
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        app.dependency_overrides[get_publication_gate] = lambda: PublicationGate(
            active_tenant_ids=frozenset({tenant})
        )
        path = f"/api/v1/networks/{network}/overview"
        first = client.get(path)
        assert first.status_code == 200, first.text
        daily = next(
            f
            for item in first.json()["items"]
            for f in item["forecasts"]
            if f["model_id"] == "cmal_small"
        )
        assert daily["availability"] == "withheld_or_ineligible"
        assert daily["forecast_id"] is None and daily["peak"] is None
        assert record.id not in selected
        conn.execute(sa.text("RESET ROLE"))
        conn.execute(
            sa.update(forecast_publication_selections)
            .where(forecast_publication_selections.c.station_id == record.station_id)
            .values(selected_forecast_id=record.id, version=2)
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        second = client.get(path, headers={"If-None-Match": first.headers["etag"]})
        assert second.status_code == 200, second.text
        daily = next(
            f
            for item in second.json()["items"]
            for f in item["forecasts"]
            if f["model_id"] == "cmal_small"
        )
        assert daily["forecast_id"] == str(record.id)
        assert daily["peak"] is not None
        assert record.id in selected
        selected.clear()
        conn.execute(sa.text("RESET ROLE"))
        conn.execute(
            sa.update(forecast_publication_selections)
            .where(forecast_publication_selections.c.station_id == record.station_id)
            .values(selected_forecast_id=None, version=3)
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        withdrawn = client.get(path, headers={"If-None-Match": second.headers["etag"]})
        assert withdrawn.status_code == 200
        assert not selected
        assert all(
            f["forecast_id"] is None
            for item in withdrawn.json()["items"]
            for f in item["forecasts"]
        )


class TestRejectedDailyOverview:
    @pytest.mark.parametrize("daily_overview_case", ["rejected"], indirect=True)
    def test_rejected_reference_never_decodes_or_falls_back(
        self, daily_overview_case: tuple, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, client, record, _, network, _, _, _, _ = daily_overview_case
        original = PgForecastStore.fetch_bounded_forecasts_by_ids
        selected = []

        def capture(store: PgForecastStore, ids: list) -> dict:
            selected.extend(ids)
            return original(store, ids)

        monkeypatch.setattr(PgForecastStore, "fetch_bounded_forecasts_by_ids", capture)
        response = client.get(f"/api/v1/networks/{network}/overview")
        assert response.status_code == 200, response.text
        daily = next(
            f
            for item in response.json()["items"]
            for f in item["forecasts"]
            if f["model_id"] == "cmal_small"
        )
        assert daily["availability"] == "withheld_or_ineligible"
        assert daily["peak"] is None and daily["forecast_id"] is None
        assert record.id not in selected


class TestOverviewNumericPurposeEligibility:
    @pytest.mark.parametrize("overview_case", ["unchecked"], indirect=True)
    def test_unchecked_provisional_forecast_keeps_honest_label(
        self, overview_case: tuple
    ) -> None:
        _, client, _, _, network, _ = overview_case
        response = client.get(
            f"/api/v1/networks/{network}/overview?data_use=provisional_rating"
        )
        assert response.status_code == 200, response.text
        output = response.json()["items"][0]["forecasts"][0]
        assert output["qc_status"] == "qc_unchecked"
        assert output["peak"] is not None


class TestOverviewTimeAndDataValidators:
    def test_late_partial_hour_sample_changes_validator(
        self, overview_case: tuple
    ) -> None:
        import random

        from sapphire_flow.store.observation_store import PgObservationStore
        from sapphire_flow.types.enums import QcStatus
        from tests.conftest import make_observation

        conn, client, native, _, network, _ = overview_case
        path = f"/api/v1/networks/{network}/overview"
        first = client.get(path)
        assert first.status_code == 200
        conn.execute(sa.text("RESET ROLE"))
        timestamp = ensure_utc(native.issued_at + timedelta(seconds=30))
        PgObservationStore(conn).store_observations(
            [
                make_observation(
                    station_id=native.station_id,
                    parameter="water_level",
                    timestamp=timestamp,
                    value=-0.5,
                    qc_status=QcStatus.QC_UNCHECKED,
                    rng=random.Random(991),
                )
            ]
        )
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        app.dependency_overrides[get_observation_clock] = lambda: ensure_utc(
            native.issued_at + timedelta(minutes=1)
        )
        second = client.get(path, headers={"If-None-Match": first.headers["etag"]})
        assert second.status_code == 200, second.text
        body = second.json()["items"][0]["water_level"]
        assert body["latest"]["sample"]["value"] == -0.5
        assert body["latest"]["sample"]["qc_status"] == "qc_unchecked"
        assert body["hourly"] == first.json()["items"][0]["water_level"]["hourly"]
        assert second.headers["etag"] != first.headers["etag"]
