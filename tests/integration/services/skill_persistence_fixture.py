from __future__ import annotations

import dataclasses
import os
import random
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import UUID

import polars as pl
import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.exc import DBAPIError
from testcontainers.postgres import PostgresContainer

from alembic import command
from sapphire_flow.db import metadata as db
from sapphire_flow.flows._db import make_pg_stores
from sapphire_flow.store.hindcast_store import PgHindcastStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import StationThreshold
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForcingType,
    ObservationSource,
    ThresholdSource,
)
from sapphire_flow.types.forecast import HindcastForecast
from sapphire_flow.types.ids import ArtifactId, HindcastForecastId, ModelId, StationId
from tests.conftest import make_observation, make_station_config
from tests.integration.db.test_role_bootstrap import (
    _RoleBootstrapHarness,  # pyright: ignore[reportPrivateUsage] -- reuse real role bootstrap
)
from tests.integration.ops.mixed_restore_fixture import (
    cleanup_owned,
    record_resources,
    start_owned,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.store.flow_regime_config_store import PgFlowRegimeConfigStore
    from sapphire_flow.store.observation_store import PgObservationStore
    from sapphire_flow.store.skill_store import PgSkillStore
    from sapphire_flow.store.station_store import PgStationStore
    from sapphire_flow.types.observation import Observation

T = ensure_utc(datetime(2026, 1, 10, tzinfo=UTC))
C = ensure_utc(T + timedelta(hours=40))
SID = StationId(UUID(int=61000))
MID = ModelId("skill_isolation_a")
AID = ArtifactId(UUID(int=62001))
RUN = UUID(int=63001)


@dataclass(frozen=True, kw_only=True, slots=True)
class SkillPersistenceCase:
    connection: sa.Connection
    skill_store: PgSkillStore
    hindcast_store: PgHindcastStore
    obs_store: PgObservationStore
    station_store: PgStationStore
    flow_regime_store: PgFlowRegimeConfigStore


@contextmanager
def seed_transaction(conn: sa.Connection) -> Iterator[sa.Connection]:
    with conn.begin_nested():
        yield conn


def seed_base(conn: sa.Connection) -> dict[str, object]:
    stores = make_pg_stores(conn)
    stations = cast("PgStationStore", stores["station_store"])
    stations.store_station(
        make_station_config(
            station_id=SID, code="skill-isolation", rng=random.Random(61000)
        )
    )
    conn.execute(
        sa.insert(db.models).values(
            id=MID,
            display_name="Synthetic skill diagnostic",
            artifact_scope="station",
            description="Development-only rollback probe, no model execution",
        )
    )
    conn.execute(
        sa.insert(db.model_artifacts).values(
            id=AID,
            model_id=MID,
            station_id=SID,
            group_id=None,
            status="active",
            artifact_path="unused-synthetic-artifact",
            sha256_hash="",
            training_period_start=T - timedelta(days=2),
            training_period_end=T - timedelta(days=1),
            trained_at=T - timedelta(days=1),
        )
    )
    return stores


def seed_task(conn: sa.Connection, stores: dict[str, object]) -> None:
    stations = cast("PgStationStore", stores["station_store"])
    observations = cast("PgObservationStore", stores["obs_store"])
    # INFERRED labels a deliberately synthetic test threshold, not an official warning.
    threshold = StationThreshold(
        station_id=SID,
        danger_level="2",
        parameter="discharge",
        value=19.0,
        source=ThresholdSource.INFERRED,
        created_at=C,
        updated_at=C,
    )
    stations.store_thresholds([threshold])
    writer = PgHindcastStore(conn, transaction_factory=lambda: seed_transaction(conn))
    rows: list[Observation] = []
    for i, q in enumerate((10.0, 22.0)):
        issue = ensure_utc(T + timedelta(hours=2 * i))
        valid = ensure_utc(issue + timedelta(hours=1))
        rows.extend(
            dataclasses.replace(
                make_observation(
                    station_id=SID,
                    timestamp=ensure_utc(valid + timedelta(minutes=15 * j)),
                    value=q + offset,
                    rng=random.Random(100 * i + j),
                ),
                source=ObservationSource.MANUAL_IMPORT,
                created_at=C,
            )
            for j, offset in enumerate((-3.0, -1.0, 1.0, 3.0))
        )
        ensemble = ForecastEnsemble.from_members(
            station_id=SID,
            issued_at=issue,
            parameter="discharge",
            units="m³/s",
            time_step=timedelta(hours=1),
            values=pl.DataFrame(
                {
                    "valid_time": [valid, valid],
                    "member_id": [0, 1],
                    "value": [q + 1, q + 1],
                }
            ),
            model_id=MID,
        )
        hc = HindcastForecast(
            id=HindcastForecastId(UUID(int=67000 + i)),
            station_id=SID,
            model_id=MID,
            model_artifact_id=AID,
            hindcast_step=issue,
            forcing_type=ForcingType.REANALYSIS,
            representation=EnsembleRepresentation.MEMBERS,
            hindcast_run_id=RUN,
            ensemble=ensemble,
            created_at=C,
        )
        writer.store_hindcast(hc)
    observations.store_observations(rows)


@pytest.fixture(scope="session")
def skill_persistence_engine(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[sa.Engine]:
    directory = Path(
        os.environ.get(
            "SKILL_PERSISTENCE_EVIDENCE_DIR",
            str(tmp_path_factory.mktemp("skill-persistence-owned")),
        )
    )
    directory.mkdir(parents=True, exist_ok=True)
    resource = directory / "owned-resources.json"
    owned: list[PostgresContainer] = []
    engines: list[sa.Engine] = []
    ids: list[str] = []
    evidence_errors: list[Exception] = []
    original: BaseException | None = None
    prior_url = os.environ.get("DATABASE_URL")
    try:
        pg = start_owned(
            owned,
            PostgresContainer(
                "postgis/postgis:16-3.4",
                username="test",
                password="test",
                dbname="sapphire",
            ),
        )
        cid = pg.get_wrapped_container().id
        assert isinstance(cid, str)
        ids.append(cid)
        record_resources(resource, ids, "created", evidence_errors)
        url = pg.get_connection_url().replace("+psycopg2", "+psycopg")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)
        engines.append(engine)
        command.upgrade(Config("alembic.ini"), "head")
        # Infrastructure provisioning only; skill tests never use AUTOCOMMIT.
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(sa.text("CREATE DATABASE prefect"))
        result = _RoleBootstrapHarness(pg, engine).run_bootstrap(
            "skill-test-api", "skill-test-worker"
        )
        if result.returncode:
            raise RuntimeError(
                "Disposable skill role bootstrap failed: " + result.stderr
            )
        yield engine
    except BaseException as exc:
        original = exc
        raise
    finally:
        if prior_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prior_url
        cleanup_owned(owned, engines, ids, resource, original, evidence_errors)


@pytest.fixture
def skill_persistence_case(
    skill_persistence_engine: sa.Engine,
) -> Iterator[SkillPersistenceCase]:
    with skill_persistence_engine.connect() as conn:
        outer = conn.begin()
        try:
            stores = seed_base(conn)
            seed_task(conn, stores)
            assert (
                conn.scalar(
                    sa.select(sa.func.count()).select_from(db.hindcast_forecasts)
                )
                == 2
            )
            assert (
                conn.scalar(sa.select(sa.func.count()).select_from(db.hindcast_values))
                == 4
            )
            assert (
                conn.scalar(sa.select(sa.func.count()).select_from(db.observations))
                == 8
            )
            conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
            assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
            with (
                pytest.raises(DBAPIError, match="permission denied"),
                conn.begin_nested(),
            ):
                conn.execute(sa.select(db.provisional_discharges))
            worker = make_pg_stores(conn)
            yield SkillPersistenceCase(
                connection=conn,
                skill_store=cast("PgSkillStore", worker["skill_store"]),
                hindcast_store=cast("PgHindcastStore", worker["hindcast_store"]),
                obs_store=cast("PgObservationStore", worker["obs_store"]),
                station_store=cast("PgStationStore", worker["station_store"]),
                flow_regime_store=cast(
                    "PgFlowRegimeConfigStore", worker["flow_regime_store"]
                ),
            )
        finally:
            outer.rollback()
