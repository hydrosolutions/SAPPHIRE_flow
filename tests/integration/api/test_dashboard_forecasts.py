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
    status: str = "raw",
    station_id: StationId | None = None,
    model_id: str | None = None,
    issued_at: object | None = None,
) -> str:
    station_id = station_id if station_id is not None else _seed_station(conn)
    model_id = model_id if model_id is not None else _seed_model(conn)
    forecast_id = uuid4()
    issued = issued_at if issued_at is not None else _ISSUED_AT
    conn.execute(
        sa.insert(forecasts).values(
            id=forecast_id,
            station_id=station_id,
            model_id=model_id,
            model_artifact_id=None,
            issued_at=issued,
            representation=representation,
            status=status,
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


def _table_list_row(html: str, table_name: str) -> str:
    """The ONE row of `/tables/` describing ``table_name`` — its count cell is
    otherwise indistinguishable from every other table's."""
    anchor = f'href="/tables/{table_name}/"'
    start = html.index(anchor)
    return html[start : html.index("</tr>", start)]


def _card(html: str, heading: str) -> str:
    """The ONE dashboard card under ``heading``.

    🔴 Required, not tidiness: the dashboard renders a card per entity and they
    all use the same markup, so searching the whole page for a rendered number
    proves nothing about WHICH card produced it. A fixture that seeds four
    models makes the models card render the same `4` the forecast total does.
    """
    start = html.index(f"<h3>{heading}</h3>")
    return html[start : html.index("</div>", start)]


class TestSupersededForecastsOnTheAdminSurfaces:
    """Plan 328 T3 — the HISTORICAL readers, asserted individually.

    ⛔ A disposition recorded in a document is a claim, not a test. Each
    reader below is one the inventory says keeps superseded rows; if any of
    them silently started filtering, a replaced forecast — and the permanent
    evidence attached to it — would stop being reachable through the surface
    an operator actually uses.
    """

    def _seed_pair(self, conn: sa.Connection) -> tuple[str, str]:
        """A superseded row and a current one, under DISTINCT natural keys so
        the partial unique index admits both (the pair a real supersession
        leaves shares a key; here the point is what the READERS show)."""
        station_id = _seed_station(conn)
        superseded = _seed_forecast(
            conn,
            representation="members",
            status="superseded",
            station_id=station_id,
            model_id=_seed_model(conn, "linreg_superseded"),
        )
        current = _seed_forecast(
            conn,
            representation="members",
            status="raw",
            station_id=station_id,
            model_id=_seed_model(conn, "linreg_current"),
        )
        return superseded, current

    def test_the_admin_list_shows_the_superseded_row_with_its_status(
        self, db_connection: sa.Connection
    ) -> None:
        superseded, current = self._seed_pair(db_connection)
        client = _client(db_connection)
        try:
            resp = client.get("/forecasts/")
        finally:
            app_overrides_clear()

        assert resp.status_code == 200
        assert superseded in resp.text, "the operator's record browser must show it"
        assert current in resp.text
        assert "superseded" in resp.text, "and must render its status"

    def test_the_admin_detail_page_serves_a_superseded_forecast(
        self, db_connection: sa.Connection
    ) -> None:
        superseded, _ = self._seed_pair(db_connection)
        client = _client(db_connection)
        try:
            resp = client.get(f"/forecasts/{superseded}/")
        finally:
            app_overrides_clear()

        assert resp.status_code == 200
        assert "superseded" in resp.text

    def test_data_json_still_serves_a_superseded_forecasts_values(
        self, db_connection: sa.Connection
    ) -> None:
        """Its values are what the retained evidence is evidence OF."""
        superseded, _ = self._seed_pair(db_connection)
        client = _client(db_connection)
        try:
            resp = client.get(f"/api/v1/forecasts/{superseded}/data.json")
        finally:
            app_overrides_clear()

        assert resp.status_code == 200
        assert resp.json()["members"], "a superseded forecast keeps its values"

    def test_the_table_index_counts_the_superseded_row(
        self, db_connection: sa.Connection
    ) -> None:
        """`/tables/` runs its OWN count per table (`tables.py::table_list`).

        ⛔ Asserted on its own row, and on the COUNT: row presence on a
        different page says nothing about this query."""
        self._seed_pair(db_connection)
        client = _client(db_connection)
        try:
            resp = client.get("/tables/")
        finally:
            app_overrides_clear()

        assert resp.status_code == 200
        row = _table_list_row(resp.text, "forecasts")
        assert ">2<" in row.replace(" ", ""), (
            "the index count is over ALL rows — 2, not the 1 a filter gives"
        )

    def test_the_table_detail_counts_and_lists_the_superseded_row(
        self, db_connection: sa.Connection
    ) -> None:
        """`/tables/forecasts/` runs a count AND a row select
        (`tables.py::table_detail`), independent of the index's."""
        superseded, current = self._seed_pair(db_connection)
        client = _client(db_connection)
        try:
            resp = client.get("/tables/forecasts/")
        finally:
            app_overrides_clear()

        assert resp.status_code == 200
        assert "2 rows total" in resp.text, "the detail count is its own query"
        assert superseded in resp.text
        assert current in resp.text
        assert "superseded" in resp.text

    def test_the_rows_partial_lists_the_superseded_row(
        self, db_connection: sa.Connection
    ) -> None:
        """`/tables/forecasts/rows` is the htmx partial — a THIRD independent
        select (`tables.py::table_rows_partial`).

        ⚠️ Its count feeds only `has_next`, so with a page of 50 it is not
        observable in the rendered output; row visibility is what this path
        can honestly assert."""
        superseded, current = self._seed_pair(db_connection)
        client = _client(db_connection)
        try:
            resp = client.get("/tables/forecasts/rows")
        finally:
            app_overrides_clear()

        assert resp.status_code == 200
        assert superseded in resp.text
        assert current in resp.text
        assert "superseded" in resp.text

    def test_the_dashboard_totals_count_it_and_the_breakdown_separates_it(
        self, db_connection: sa.Connection
    ) -> None:
        """⚠️ TWO distinct readers in one page, and this asserts BOTH: the
        total count and the latest `issued_at` run over ALL rows, while the
        `GROUP BY status` breakdown gives superseded rows their own bucket.

        🔑 The counts are deliberately asymmetric (3 superseded, 1 current) so
        no assertion can pass on the wrong bucket, and the LATEST row is a
        superseded one — a filtered total would report the earlier time.
        """
        station_id = _seed_station(db_connection)
        later = ensure_utc(_ISSUED_AT + timedelta(days=1))
        for index in range(3):
            _seed_forecast(
                db_connection,
                representation="members",
                status="superseded",
                station_id=station_id,
                model_id=_seed_model(db_connection, f"linreg_old_{index}"),
                # The newest row overall is a SUPERSEDED one.
                issued_at=later if index == 0 else _ISSUED_AT,
            )
        _seed_forecast(
            db_connection,
            representation="members",
            status="raw",
            station_id=station_id,
            model_id=_seed_model(db_connection, "linreg_live"),
        )

        client = _client(db_connection)
        try:
            resp = client.get("/")
        finally:
            app_overrides_clear()

        assert resp.status_code == 200
        # ⛔ Scoped to the Forecasts card. The page-wide search this replaced
        # was vacuous: the fixture seeds FOUR models, so the models card
        # renders the same `4` and filtering the forecast total to 1 still
        # left a matching string somewhere on the page.
        card = _card(resp.text, "Forecasts")
        # The GROUP BY status breakdown: its own bucket, its own count.
        assert "superseded: 3" in card
        assert "raw: 1" in card
        # The TOTAL is over all rows — 4, not the 1 a filtered reader gives.
        assert '<p style="font-size:2rem; margin:0;">4</p>' in card
        # ...and so is the latest issue time.
        assert f"latest: {later.strftime('%Y-%m-%d %H:%M')}" in card
