from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from sapphire_flow.db.metadata import forecast_values, forecasts, models
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from tests.conftest import make_station_config

if TYPE_CHECKING:
    from collections.abc import Generator

    from sapphire_flow.types.ids import StationId

_NOW = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_ISSUED_AT = ensure_utc(datetime(2025, 1, 2, 12, tzinfo=UTC))


@pytest.fixture(autouse=True)
def _reset_reflected() -> Generator[None, None, None]:
    """Reset the module-level reflected-schema singleton around each test."""
    import sapphire_flow.api.routes.tables as tables_mod

    tables_mod._reflected = None
    yield
    tables_mod._reflected = None


def _seed_station(conn: sa.Connection) -> StationId:
    station = make_station_config(rng=random.Random(96))
    PgStationStore(conn).store_station(station)
    return station.id


def _seed_model(conn: sa.Connection, model_id: str = "linreg_v1") -> str:
    conn.execute(
        sa.insert(models).values(
            id=model_id,
            display_name="Linear Regression v1",
            artifact_scope="station",
            description="Test model",
            created_at=_NOW,
        )
    )
    return model_id


def _seed_forecast(
    conn: sa.Connection,
    *,
    representation: str,
) -> str:
    station_id = _seed_station(conn)
    model_id = _seed_model(conn)
    forecast_id = uuid4()
    conn.execute(
        sa.insert(forecasts).values(
            id=forecast_id,
            station_id=station_id,
            model_id=model_id,
            model_artifact_id=None,
            issued_at=_ISSUED_AT,
            representation=representation,
            status="raw",
            parameter="discharge",
            units="m³/s",
            qc_status="raw",
        )
    )
    # Two lead times starting at 1h so valid_times[0] is strictly after issued_at.
    for lead in (1, 24):
        valid_time = _ISSUED_AT + timedelta(hours=lead)
        if representation == "quantiles":
            member_kwargs: dict[str, object] = {"member_id": None, "quantile": 0.5}
        else:
            member_kwargs = {"member_id": 0, "quantile": None}
        conn.execute(
            sa.insert(forecast_values).values(
                id=uuid4(),
                forecast_id=forecast_id,
                issued_at=_ISSUED_AT,
                valid_time=valid_time,
                lead_time_hours=lead,
                value=1.5 + lead,
                **member_kwargs,
            )
        )
    return str(forecast_id)


def _client(db_connection: sa.Connection) -> TestClient:
    from uuid import UUID

    from sapphire_flow.api import app
    from sapphire_flow.api.deps import get_connection
    from sapphire_flow.api.security import Principal, require_admin
    from sapphire_flow.types.enums import AccessTokenRole
    from sapphire_flow.types.ids import AccessTokenId

    def _override_conn() -> Generator[sa.Connection, None, None]:
        yield db_connection

    admin_principal = Principal(
        token_id=AccessTokenId(UUID("00000000-0000-0000-0000-0000000000ad")),
        role=AccessTokenRole.ADMIN,
        tenant_id=None,
        station_ids=frozenset(),
    )

    app.dependency_overrides[get_connection] = _override_conn
    # forecasts_router (legacy `.json` export + HTML page) is admin-gated
    # (Plan 147 Slice C, R3) — these tests exercise the data shape, not auth.
    app.dependency_overrides[require_admin] = lambda: admin_principal
    return TestClient(app, raise_server_exceptions=True)


class TestForecastDataJson:
    @pytest.mark.parametrize(
        ("representation", "series_key"),
        [("members", "members"), ("quantiles", "quantiles")],
    )
    def test_emits_valid_times_units_and_issued_at(
        self,
        db_connection: sa.Connection,
        representation: str,
        series_key: str,
    ) -> None:
        forecast_id = _seed_forecast(db_connection, representation=representation)
        client = _client(db_connection)
        try:
            resp = client.get(f"/api/v1/forecasts/{forecast_id}/data.json")
        finally:
            app_overrides_clear()
        assert resp.status_code == 200
        data = resp.json()

        assert data["units"] == "m³/s"
        assert data["issued_at"] == _ISSUED_AT.isoformat()

        series = next(iter(data[series_key].values()))
        assert series["valid_times"], "per-series valid_times must be non-empty"
        # ISO 8601 strings, one per lead time, aligned with lead_times / values
        assert len(series["valid_times"]) == len(series["lead_times"])
        assert len(series["valid_times"]) == len(series["values"])
        for vt in series["valid_times"]:
            datetime.fromisoformat(vt)

    def test_issued_at_strictly_before_first_valid_time(
        self, db_connection: sa.Connection
    ) -> None:
        forecast_id = _seed_forecast(db_connection, representation="members")
        client = _client(db_connection)
        try:
            resp = client.get(f"/api/v1/forecasts/{forecast_id}/data.json")
        finally:
            app_overrides_clear()
        data = resp.json()
        issued = datetime.fromisoformat(data["issued_at"])
        series = next(iter(data["members"].values()))
        first_valid = datetime.fromisoformat(series["valid_times"][0])
        assert issued < first_valid

    def test_unknown_forecast_returns_empty_payload(
        self, db_connection: sa.Connection
    ) -> None:
        # Ensure schema is reflectable by touching a real table first.
        _seed_station(db_connection)
        client = _client(db_connection)
        try:
            resp = client.get(f"/api/v1/forecasts/{uuid4()}/data.json")
        finally:
            app_overrides_clear()
        assert resp.status_code == 200
        assert resp.json() == {"lead_times": [], "members": {}}


class TestForecastDetailPage:
    def test_renders_chart_div_and_data_json_url(
        self, db_connection: sa.Connection
    ) -> None:
        forecast_id = _seed_forecast(db_connection, representation="members")
        client = _client(db_connection)
        try:
            resp = client.get(f"/forecasts/{forecast_id}/")
        finally:
            app_overrides_clear()
        assert resp.status_code == 200
        html = resp.text
        assert 'id="ensemble-chart"' in html
        assert f"/api/v1/forecasts/{forecast_id}/data.json" in html


def app_overrides_clear() -> None:
    from sapphire_flow.api import app
    from sapphire_flow.api.deps import get_connection
    from sapphire_flow.api.security import require_admin

    app.dependency_overrides.pop(get_connection, None)
    app.dependency_overrides.pop(require_admin, None)
