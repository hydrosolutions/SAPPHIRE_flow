"""Plan 316 T3 — the published observed series shows only QC-cleared readings.

Plan 272 D5 assigns the dashboard/API observed series **"No — exclude"** for
``QC_UNCHECKED``. The filter behind it lists what to REJECT, so every status
added since was published by default — which is how an unexamined reading
reached the series unmarked.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from sapphire_flow.db.metadata import hindcast_forecasts, hindcast_values, models
from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ModelArtifactStatus, QcStatus
from sapphire_flow.types.ids import ModelId
from tests.conftest import make_observation, make_station_config

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from sapphire_flow.types.ids import StationId

_STEP = ensure_utc(datetime(2025, 3, 1, tzinfo=UTC))
_MODEL_ID = ModelId("linreg_v1")
_PARAMETER = "discharge"


@pytest.fixture(autouse=True)
def _reset_reflected() -> Generator[None, None, None]:
    import sapphire_flow.api.routes.tables as tables_mod

    tables_mod._reflected = None
    yield
    tables_mod._reflected = None


def _seed(conn: sa.Connection, artifact_dir: Path) -> StationId:
    """One hindcast step (the endpoint short-circuits without one) plus three
    observations — one per QC status the filter has to distinguish."""
    station = make_station_config(rng=random.Random(316))
    PgStationStore(conn).store_station(station)
    conn.execute(
        sa.insert(models).values(
            id=str(_MODEL_ID),
            display_name="Linear Regression v1",
            artifact_scope="station",
            description="test",
            created_at=_STEP,
        )
    )
    artifact_id, _ = PgModelArtifactStore(conn, artifact_dir).store_artifact(
        _MODEL_ID,
        b"artifact",
        _STEP - timedelta(days=30),
        _STEP - timedelta(days=1),
        _STEP - timedelta(days=1),
        station_id=station.id,
        status=ModelArtifactStatus.ACTIVE,
    )
    hindcast_id = uuid4()
    conn.execute(
        sa.insert(hindcast_forecasts).values(
            id=hindcast_id,
            station_id=station.id,
            model_id=str(_MODEL_ID),
            model_artifact_id=artifact_id,
            hindcast_step=_STEP,
            forcing_type="reanalysis",
            representation="members",
            hindcast_run_id=uuid4(),
            parameter=_PARAMETER,
            units="m³/s",
            created_at=_STEP,
        )
    )
    conn.execute(
        sa.insert(hindcast_values).values(
            id=uuid4(),
            hindcast_forecast_id=hindcast_id,
            hindcast_step=_STEP,
            valid_time=_STEP + timedelta(days=1),
            lead_time_hours=24,
            member_id=0,
            value=12.0,
        )
    )

    rng = random.Random(7)
    PgObservationStore(conn).store_observations(
        [
            make_observation(
                station_id=station.id,
                parameter=_PARAMETER,
                value=value,
                timestamp=ensure_utc(_STEP + timedelta(hours=hour)),
                qc_status=qc_status,
                rng=rng,
            )
            for hour, value, qc_status in (
                (1, 11.0, QcStatus.QC_PASSED),
                (2, 22.0, QcStatus.QC_FAILED),
                (3, 33.0, QcStatus.QC_UNCHECKED),
            )
        ]
    )
    return station.id


def _observed_values(
    db_connection: sa.Connection, station_id: StationId
) -> list[float]:
    from uuid import UUID

    from sapphire_flow.api import app
    from sapphire_flow.api.deps import get_connection
    from sapphire_flow.api.security import Principal, require_admin
    from sapphire_flow.types.enums import AccessTokenRole
    from sapphire_flow.types.ids import AccessTokenId

    app.dependency_overrides[get_connection] = lambda: db_connection
    # The legacy dashboard routes are admin-gated (Plan 147 Slice C); this
    # test exercises the data shape, not auth.
    app.dependency_overrides[require_admin] = lambda: Principal(
        token_id=AccessTokenId(UUID("00000000-0000-0000-0000-0000000000ad")),
        role=AccessTokenRole.ADMIN,
        tenant_id=None,
        station_ids=frozenset(),
    )
    try:
        response = TestClient(app, raise_server_exceptions=True).get(
            f"/api/v1/stations/{station_id}/hindcasts.json",
            params={
                "parameter": _PARAMETER,
                "start": _STEP.isoformat(),
                "end": (_STEP + timedelta(days=2)).isoformat(),
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    return list(response.json()["observed"]["values"])


class TestPublishedObservedSeries:
    def test_unchecked_reading_is_not_published(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        station_id = _seed(db_connection, tmp_path)

        assert 33.0 not in _observed_values(db_connection, station_id)

    def test_passed_is_published_and_failed_is_not(
        self, db_connection: sa.Connection, tmp_path: Path
    ) -> None:
        """The two statuses the filter already handled behave as before."""
        station_id = _seed(db_connection, tmp_path)

        values = _observed_values(db_connection, station_id)

        assert 11.0 in values
        assert 22.0 not in values
