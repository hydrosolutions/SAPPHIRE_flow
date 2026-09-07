"""Plan 235 D2 — LOCKED end-to-end coverage for the three raw-SQL API
readers (readers 6, 7, 8, 9 in the plan's audit table:
`api/routes/models.py`'s `model_detail` skill scores/diagrams and
`model_skill_chart_json`, `api/routes/stations.py`'s station-detail skill
summary). These bypass `store.skill_store` entirely, reading reflected
tables directly — a store-only fix would leave the whole API serving mixed
generations. Drives the REAL FastAPI app via `TestClient`, exactly like
`tests/integration/api/test_access_token_auth.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Generator

from sapphire_flow.api import app
from sapphire_flow.api.deps import get_connection
from sapphire_flow.db.metadata import model_artifacts, models, stations
from sapphire_flow.store.skill_store import PgSkillStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import SkillSource
from sapphire_flow.types.ids import ArtifactId, ModelId, StationId
from sapphire_flow.types.skill import SkillDiagram, SkillScore
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
_T_OLD = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_T_NEW = ensure_utc(datetime(2025, 6, 1, tzinfo=UTC))


@pytest.fixture(autouse=True)
def _reset_reflected() -> Generator[None, None, None]:
    """Reset the module-level reflected-schema singleton around each test
    (mirrors `test_dashboard_forecasts.py`)."""
    import sapphire_flow.api.routes.tables as tables_mod

    tables_mod._reflected = None
    yield
    tables_mod._reflected = None


@pytest.fixture
def client(db_connection: sa.Connection) -> Generator[TestClient, None, None]:
    from uuid import UUID

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
    # models_router/stations_router are admin-gated (Plan 147 Slice C, R3) —
    # these tests exercise generation selection, not auth.
    app.dependency_overrides[require_admin] = lambda: admin_principal
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.pop(get_connection, None)
    app.dependency_overrides.pop(require_admin, None)


def _seed_station(conn: sa.Connection) -> StationId:
    sid = StationId(uuid.uuid4())
    conn.execute(
        sa.insert(stations).values(
            id=sid,
            code=f"GEN-API-{sid.hex[:6]}",
            name="Generation API Test Station",
            location="SRID=4326;POINT(8.5 47.4)",
            station_kind="river",
            network="bafu",
            timezone="Europe/Zurich",
            measured_parameters=["discharge"],
            ownership="own",
            tenant_id=DEFAULT_TENANT_ID,
        )
    )
    return sid


def _seed_model_and_artifact(
    conn: sa.Connection, station_id: StationId
) -> tuple[ModelId, ArtifactId]:
    mid = ModelId(f"gen_api_model_{uuid.uuid4().hex[:8]}")
    conn.execute(
        sa.insert(models).values(
            id=mid,
            display_name="Generation API Test Model",
            artifact_scope="station",
            description="Integration test",
        )
    )
    aid = ArtifactId(uuid.uuid4())
    conn.execute(
        sa.insert(model_artifacts).values(
            id=aid,
            model_id=mid,
            station_id=station_id,
            group_id=None,
            status="active",
            artifact_path=f"artifacts/{aid}.bin",
            sha256_hash="",
            training_period_start=_T_OLD,
            training_period_end=_T_NEW,
            trained_at=_T_NEW,
            promoted_at=_T_NEW,
            promoted_by=None,
            superseded_at=None,
            created_at=_T_NEW,
        )
    )
    return mid, aid


def _seed_two_generations(
    conn: sa.Connection,
    *,
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
) -> None:
    """Publishes a STALE generation (score 40.0000 / an old diagram) and
    then a NEWER one (score 0.4000) for the identical (station, model,
    parameter, skill_source) scope — mirroring the corrected-recompute
    scenario every reader must resolve consistently.
    """
    store = PgSkillStore(conn)

    gen_old = uuid.uuid4()
    store.store_skill_scores(
        [
            SkillScore(
                id=uuid.uuid4(),
                station_id=station_id,
                model_id=model_id,
                parameter="discharge",
                model_artifact_id=artifact_id,
                skill_source=SkillSource.HINDCAST_NWP_ARCHIVE,
                forcing_type=None,
                computation_version=2,
                computed_at=_NOW,
                lead_time_hours=24,
                season=None,
                flow_regime=None,
                flow_regime_config_id=None,
                metric="mae",
                score=40.0,
                sample_size=10,
                freshness=_current_freshness(),
                eval_period_start=_T_OLD,
                eval_period_end=_T_NEW,
                created_at=_NOW,
                generation_id=gen_old,
            )
        ]
    )
    store.store_skill_diagrams(
        [
            SkillDiagram(
                id=uuid.uuid4(),
                station_id=station_id,
                model_id=model_id,
                parameter="discharge",
                model_artifact_id=artifact_id,
                skill_source=SkillSource.HINDCAST_NWP_ARCHIVE,
                computation_version=2,
                lead_time_hours=24,
                season=None,
                flow_regime=None,
                flow_regime_config_id=None,
                diagram_type="roc",
                threshold_level=None,
                data={"x": [0.0], "y": [0.0]},
                eval_period_start=_T_OLD,
                eval_period_end=_T_NEW,
                created_at=_NOW,
                generation_id=gen_old,
            )
        ]
    )
    store.publish_generation(
        generation_id=gen_old,
        station_id=station_id,
        model_id=model_id,
        model_artifact_id=artifact_id,
        parameter="discharge",
        skill_source=SkillSource.HINDCAST_NWP_ARCHIVE,
        forcing_type=None,
        computation_version=2,
        published_at=_T_OLD,
        score_count=1,
        diagram_count=1,
    )

    gen_new = uuid.uuid4()
    store.store_skill_scores(
        [
            SkillScore(
                id=uuid.uuid4(),
                station_id=station_id,
                model_id=model_id,
                parameter="discharge",
                model_artifact_id=artifact_id,
                skill_source=SkillSource.HINDCAST_NWP_ARCHIVE,
                forcing_type=None,
                computation_version=2,
                computed_at=_NOW,
                lead_time_hours=24,
                season=None,
                flow_regime=None,
                flow_regime_config_id=None,
                metric="mae",
                score=0.4,
                sample_size=10,
                freshness=_current_freshness(),
                eval_period_start=_T_OLD,
                eval_period_end=_T_NEW,
                created_at=_NOW,
                generation_id=gen_new,
            )
        ]
    )
    store.store_skill_diagrams(
        [
            SkillDiagram(
                id=uuid.uuid4(),
                station_id=station_id,
                model_id=model_id,
                parameter="discharge",
                model_artifact_id=artifact_id,
                skill_source=SkillSource.HINDCAST_NWP_ARCHIVE,
                computation_version=2,
                lead_time_hours=24,
                season=None,
                flow_regime=None,
                flow_regime_config_id=None,
                diagram_type="reliability",
                threshold_level=None,
                data={"x": [1.0], "y": [1.0]},
                eval_period_start=_T_OLD,
                eval_period_end=_T_NEW,
                created_at=_NOW,
                generation_id=gen_new,
            )
        ]
    )
    store.publish_generation(
        generation_id=gen_new,
        station_id=station_id,
        model_id=model_id,
        model_artifact_id=artifact_id,
        parameter="discharge",
        skill_source=SkillSource.HINDCAST_NWP_ARCHIVE,
        forcing_type=None,
        computation_version=2,
        published_at=_T_NEW,
        score_count=1,
        diagram_count=1,
    )


def _current_freshness():  # noqa: ANN202
    from sapphire_flow.types.enums import SkillFreshness

    return SkillFreshness.CURRENT


class TestModelSkillChartJsonUsesNewestGeneration:
    """Reader 9 (`api/routes/models.py:177`): was `freshness == "current"`
    — a THIRD, independent "what is current" rule (D2)."""

    def test_returns_only_newest_generation_score(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid, aid = _seed_model_and_artifact(db_connection, sid)
        _seed_two_generations(
            db_connection, station_id=sid, model_id=mid, artifact_id=aid
        )

        resp = client.get(f"/api/v1/models/{mid}/skill-chart.json")
        assert resp.status_code == 200
        body = resp.json()
        mae_series = [s for s in body["series"] if s["metric"] == "mae"]
        assert len(mae_series) == 1
        assert mae_series[0]["scores"] == [0.4]


class TestModelDetailUsesNewestGeneration:
    """Readers 6 and 7 (`api/routes/models.py:126,141`): were unfiltered —
    served every generation ever computed for this artifact at once."""

    def test_skill_scores_and_diagrams_show_only_newest_generation(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid, aid = _seed_model_and_artifact(db_connection, sid)
        _seed_two_generations(
            db_connection, station_id=sid, model_id=mid, artifact_id=aid
        )

        resp = client.get(f"/models/{mid}/")
        assert resp.status_code == 200
        html = resp.text
        assert "0.4000" in html, "the newest generation's score must render"
        assert "40.0000" not in html, (
            "the superseded generation's score must not render"
        )
        assert "reliability" in html, "the newest generation's diagram must render"
        # Fixer round (minor): the previous version of this test only
        # asserted the NEW diagram type appears — never that the
        # superseded generation's OWN diagram type (`roc`, seeded by
        # `_seed_two_generations`) disappears.
        assert "roc" not in html, "the superseded generation's diagram must not render"


class TestStationDetailSkillSummaryUsesNewestGeneration:
    """Reader 8 (`api/routes/stations.py:363`): was `freshness ==
    'current'` — a separate, incompatible "what is current" rule."""

    def test_skill_summary_averages_only_newest_generation(
        self, client: TestClient, db_connection: sa.Connection
    ) -> None:
        sid = _seed_station(db_connection)
        mid, aid = _seed_model_and_artifact(db_connection, sid)
        _seed_two_generations(
            db_connection, station_id=sid, model_id=mid, artifact_id=aid
        )

        resp = client.get(f"/stations/{sid}/")
        assert resp.status_code == 200
        html = resp.text
        assert "0.4" in html
        assert "40.0" not in html
