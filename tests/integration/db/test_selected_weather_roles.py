from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest
import sqlalchemy as sa

from sapphire_flow.api.weather_evidence import read_weather_summary
from sapphire_flow.store.forecast_store import PgForecastStore
from tests.integration.db.test_role_bootstrap import role_harness as role_harness
from tests.integration.store.test_forecast_store import savepoint_factory
from tests.integration.store.test_native_rating_forecasts import native_pair
from tests.integration.store.test_selected_weather_evidence import selected

if TYPE_CHECKING:
    from tests.integration.db.test_role_bootstrap import _RoleBootstrapHarness


@pytest.mark.parametrize("role", ["sapphire_api", "sapphire_worker"])
@pytest.mark.parametrize("purpose", ["standard", "provisional_rating"])
def test_runtime_roles_read_selected_parent_and_evidence(
    role_harness: _RoleBootstrapHarness,
    role: Literal["sapphire_api", "sapphire_worker"],
    purpose: Literal["standard", "provisional_rating"],
) -> None:
    bootstrap = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert bootstrap.returncode == 0, bootstrap.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin():
        ordinary, provisional = native_pair(conn)
        forecast = selected(ordinary if purpose == "standard" else provisional)
        store = PgForecastStore(
            conn,
            data_use=forecast.data_use,
            transaction_factory=savepoint_factory(conn),
        )
        store.store_forecast(forecast)
        conn.execute(sa.text(f"SET LOCAL ROLE {role}"))
        loaded = store.fetch_forecast(forecast.id)
        assert loaded is not None and loaded.weather_evidence_version == 1
        summary = read_weather_summary(
            version=loaded.weather_evidence_version,
            forecast_id=loaded.id,
            station_id=loaded.station_id,
            model_id=loaded.model_id,
            issue_time=loaded.issued_at,
            data_use=loaded.data_use,
            store=store,
        )
        assert summary is not None and summary.station_id == str(loaded.station_id)
        if role == "sapphire_worker":
            assert store.store_forecast(forecast) == forecast.id
        conn.rollback()


@pytest.mark.parametrize(
    ("role", "statement"),
    [
        ("sapphire_api", "SELECT * FROM operational_weather_cache"),
        ("sapphire_worker", "DELETE FROM operational_weather_cache WHERE false"),
    ],
)
def test_cache_privileges_remain_narrow(
    role_harness: _RoleBootstrapHarness,
    role: Literal["sapphire_api", "sapphire_worker"],
    statement: str,
) -> None:
    bootstrap = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert bootstrap.returncode == 0, bootstrap.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin():
        conn.execute(sa.text(f"SET LOCAL ROLE {role}"))
        with pytest.raises(sa.exc.DBAPIError, match="permission denied"):
            conn.execute(sa.text(statement))
        conn.rollback()


@pytest.mark.parametrize("marker", [None, 1])
def test_worker_marker_mutation_refused_but_status_transition_allowed(
    role_harness: _RoleBootstrapHarness,
    marker: int | None,
) -> None:
    bootstrap = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert bootstrap.returncode == 0, bootstrap.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin():
        ordinary, _ = native_pair(conn)
        forecast = selected(ordinary) if marker == 1 else ordinary
        store = PgForecastStore(conn, transaction_factory=savepoint_factory(conn))
        store.store_forecast(forecast)
        conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
        with (
            pytest.raises(sa.exc.DBAPIError, match="is immutable"),
            conn.begin_nested(),
        ):
            conn.execute(
                sa.text(
                    "UPDATE forecasts SET weather_evidence_version=:marker WHERE id=:id"
                ),
                {"marker": None if marker == 1 else 1, "id": forecast.id},
            )
        conn.execute(
            sa.text("UPDATE forecasts SET status='superseded' WHERE id=:id"),
            {"id": forecast.id},
        )
        row = conn.execute(
            sa.text(
                "SELECT status, weather_evidence_version FROM forecasts WHERE id=:id"
            ),
            {"id": forecast.id},
        ).one()
        assert tuple(row) == ("superseded", marker)
        conn.rollback()


@pytest.mark.parametrize("purpose", ["standard", "provisional_rating"])
def test_actual_api_role_serves_selected_json_and_html(
    role_harness: _RoleBootstrapHarness,
    purpose: Literal["standard", "provisional_rating"],
) -> None:
    from tests.integration.api.test_dashboard_forecasts import (
        _client,
        app_overrides_clear,
    )

    bootstrap = role_harness.run_bootstrap("api-fixture", "worker-fixture")
    assert bootstrap.returncode == 0, bootstrap.stderr
    with role_harness.owner_engine.connect() as conn, conn.begin():
        ordinary, provisional = native_pair(conn)
        forecast = selected(ordinary if purpose == "standard" else provisional)
        PgForecastStore(
            conn,
            data_use=forecast.data_use,
            transaction_factory=savepoint_factory(conn),
        ).store_forecast(forecast)
        conn.execute(sa.text("SET LOCAL ROLE sapphire_api"))
        client = _client(conn)
        try:
            response = client.get(
                f"/api/v1/forecasts/{forecast.id}/data.json?data_use={purpose}"
            )
            html = client.get(f"/forecasts/{forecast.id}/?data_use={purpose}")
        finally:
            app_overrides_clear()
        assert response.status_code == 200
        assert response.json()["weather_use"]["station_id"] == str(forecast.station_id)
        assert html.status_code == 200
        conn.rollback()
