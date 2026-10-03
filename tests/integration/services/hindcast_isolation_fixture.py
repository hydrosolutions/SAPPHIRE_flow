from __future__ import annotations

import os
import random
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from testcontainers.postgres import PostgresContainer

from sapphire_flow.adapters import forecast_interface as fi
from sapphire_flow.db import metadata as db
from sapphire_flow.flows._db import make_pg_stores
from sapphire_flow.services.hindcast import run_group_hindcast, run_station_hindcast
from sapphire_flow.services.model_registry import build_station_code_resolver
from sapphire_flow.services.provisional_discharge import convert_provisional_discharge
from sapphire_flow.store.hindcast_store import PgHindcastStore
from sapphire_flow.store.provisional_discharge_store import PgProvisionalDischargeStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ObservationSource,
    SpatialRepresentation,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.ids import (
    ArtifactId,
    MeasurementFeedEvidenceId,
    ModelId,
    ObservationId,
    RatingCurveId,
    RatingReferenceProofId,
    StationGroupId,
    StationId,
)
from sapphire_flow.types.rating_reference import (
    canonical_content,
    content_digest,
    curve_snapshot,
    measurement_snapshot,
)
from sapphire_flow.types.station import StationWeatherSource
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID
from tests.conftest import (
    make_observation,
    make_raw_historical_forcing,
    make_station_config,
)
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource
from tests.fakes.fake_fi_models import ReferenceFIArtifact, ReferenceFIForecastModel
from tests.integration.db.test_role_bootstrap import (
    _RoleBootstrapHarness,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.ops.mixed_restore_fixture import (
    cleanup_owned,
    record_resources,
    start_owned,
)
from tests.integration.store.test_provisional_discharge_store import (
    permit_fixture,
    seed_reference,
)
from tests.unit.services.test_provisional_discharge import NOW, inputs

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.store.basin_store import PgBasinStore
    from sapphire_flow.store.observation_store import PgObservationStore
    from sapphire_flow.store.station_group_store import PgStationGroupStore
    from sapphire_flow.store.station_store import PgStationStore
    from sapphire_flow.types.forecast import HindcastForecast
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.rating_curve import RatingCurve
    from sapphire_flow.types.rating_reference import RatingReferenceProof
    from sapphire_flow.types.training import HindcastStepResult

STEP = timedelta(hours=1)
T = ensure_utc(NOW - 4 * STEP)
CLOCK = ensure_utc(T + 8 * STEP)
START = ensure_utc(T - 3 * STEP)
END = ensure_utc(T + 4 * STEP)
SID_A = StationId(UUID(int=31000))
SID_B = StationId(UUID(int=32000))
GROUP = StationGroupId(UUID(int=33000))
MODEL = ModelId("hindcast_isolation")
ARTIFACT = ArtifactId(UUID(int=34000))
RUN = UUID(int=35000)
PROVISIONAL_TIMES = tuple(
    sorted(
        {
            ensure_utc(T + timedelta(minutes=i))
            for i in (-150, -90, -60, -30, 30, 90, 150)
        }
        | {
            ensure_utc(T + index * STEP - timedelta(minutes=7, seconds=30))
            for index in range(4)
        }
    )
)


@dataclass(frozen=True, kw_only=True, slots=True)
class Stores:
    obs: PgObservationStore
    station: PgStationStore
    basin: PgBasinStore
    group: PgStationGroupStore


def factory(conn: sa.Connection) -> Stores:
    stores = make_pg_stores(conn)
    return Stores(
        obs=cast("PgObservationStore", stores["obs_store"]),
        station=cast("PgStationStore", stores["station_store"]),
        basin=cast("PgBasinStore", stores["basin_store"]),
        group=cast("PgStationGroupStore", stores["group_store"]),
    )


@pytest.fixture(scope="module")
def owned_harness(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[_RoleBootstrapHarness]:
    directory = tmp_path_factory.mktemp("hindcast-isolation-owned")
    record = directory / "resources.json"
    owned: list[PostgresContainer] = []
    engines: list[sa.Engine] = []
    ids: list[str] = []
    errors: list[Exception] = []
    prior = os.environ.get("DATABASE_URL")
    try:
        postgres = start_owned(
            owned,
            PostgresContainer(
                "postgis/postgis:16-3.4",
                username="test",
                password="test",
                dbname="sapphire",
            ),
        )
        container_id = postgres.get_wrapped_container().id
        assert isinstance(container_id, str)
        ids.append(container_id)
        record_resources(record, ids, "created", errors)
        url = postgres.get_connection_url().replace("+psycopg2", "+psycopg")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)
        engines.append(engine)
        from alembic.config import Config

        from alembic import command

        command.upgrade(Config("alembic.ini"), "head")
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(sa.text("CREATE DATABASE prefect"))
        harness = _RoleBootstrapHarness(postgres, engine)
        result = harness.run_bootstrap("hindcast-api", "hindcast-worker")
        assert result.returncode == 0, result.stderr
        yield harness
    finally:
        if prior is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prior
        cleanup_owned(owned, engines, ids, record, sys.exc_info()[1], errors)


def ordinary_value(sid: StationId, quarter: int, delta: float = 0.0) -> float:
    value = 4.0 if quarter == 0 else 10.0 + quarter + (20.0 if sid == SID_B else 0.0)
    return value + (delta if sid == SID_A and quarter == 8 else 0.0)


@dataclass(frozen=True, kw_only=True, slots=True)
class Case:
    sid: StationId
    curve: RatingCurve
    proof: RatingReferenceProof


def seed_case(
    conn: sa.Connection,
    sid: StationId,
    history: Literal["ordinary", "absent"],
    delta: float,
) -> Case:
    stores = factory(conn)
    station = make_station_config(
        station_id=sid, code=f"hindcast-{sid.int}", rng=random.Random(sid.int)
    )
    stores.station.store_station(station)
    stores.station.store_weather_source(
        StationWeatherSource(
            station_id=sid,
            nwp_source="smn",
            extraction_type=SpatialRepresentation.POINT,
            status=WeatherSourceStatus.ACTIVE,
            role=WeatherSourceRole.REANALYSIS,
        )
    )
    _, template, _, proof = inputs()
    curve = replace(
        template,
        id=RatingCurveId(UUID(int=sid.int + 1)),
        station_id=sid,
        valid_from=ensure_utc(T - timedelta(days=365)),
        valid_to=ensure_utc(T - timedelta(hours=2, minutes=40)),
        points=[
            {"water_level": 1.0, "discharge": 4.0},
            {"water_level": 2.0, "discharge": 10000.0},
        ],
    )
    PgRatingCurveStore(conn).store_rating_curve(curve)
    proof = replace(
        proof,
        id=RatingReferenceProofId(UUID(int=sid.int + 2)),
        tenant_id=station.tenant_id,
        station_id=sid,
        rating_curve_id=curve.id,
        curve=curve_snapshot(curve),
    )
    seed_reference(conn, db.rating_reference_proofs, proof)
    if history == "ordinary":
        ordinary: list[Observation] = []
        for quarter in range(40):
            ts = ensure_utc(START + timedelta(minutes=15 * quarter))
            row = make_observation(
                station_id=sid,
                timestamp=ts,
                value=ordinary_value(sid, quarter, delta),
                rng=random.Random(sid.int + quarter),
            )
            row = replace(
                row,
                id=ObservationId(UUID(int=sid.int + 100 + quarter)),
                source=ObservationSource.RATING_CURVE_DERIVED
                if quarter == 0
                else ObservationSource.MANUAL_IMPORT,
                rating_curve_id=curve.id if quarter == 0 else None,
                rating_curve_correction_version="fixture-v1" if quarter == 0 else None,
            )
            ordinary.append(row)
        assert curve.valid_to is not None
        assert ordinary[0].timestamp < curve.valid_to
        stores.obs.store_observations(ordinary)
    return Case(sid=sid, curve=curve, proof=proof)


def seed_provisional(conn: sa.Connection, case: Case) -> None:
    template, _, feed, _ = inputs()
    store = PgProvisionalDischargeStore(conn)
    expected: list[dict[str, object]] = []
    for index, timestamp in enumerate(PROVISIONAL_TIMES):
        obs = replace(
            template,
            id=ObservationId(UUID(int=case.sid.int + 200 + index)),
            station_id=case.sid,
            timestamp=timestamp,
            value=2.0,
        )
        factory(conn).obs.store_observations([obs])
        evidence = replace(
            feed,
            id=MeasurementFeedEvidenceId(UUID(int=case.sid.int + 300 + index)),
            tenant_id=case.proof.tenant_id,
            station_id=case.sid,
            observation_id=obs.id,
            measurement=measurement_snapshot(obs),
        )
        seed_reference(conn, db.measurement_feed_evidence, evidence)
        result = convert_provisional_discharge(
            observation=obs,
            curves=[case.curve],
            feed_evidence=evidence,
            reference_proof=case.proof,
            at=CLOCK,
        )
        store.store_provisional_discharge(result, captured_at=CLOCK)
        assert result.discharge == 10000.0
        feed_content = canonical_content(asdict(evidence))
        proof_content = canonical_content(asdict(case.proof))
        expected.append(
            {
                "timestamp": timestamp,
                "value": 2.0,
                "discharge": 10000.0,
                "observation_id": obs.id,
                "station_id": case.sid,
                "tenant_id": case.proof.tenant_id,
                "rating_curve_id": case.curve.id,
                "feed_evidence_id": evidence.id,
                "reference_proof_id": case.proof.id,
                "content": result.content,
                "fingerprint": result.fingerprint,
                "feed_content": feed_content,
                "feed_fingerprint": content_digest(feed_content),
                "proof_content": proof_content,
                "proof_fingerprint": content_digest(proof_content),
            }
        )
    p, o, f, r = (
        db.provisional_discharges,
        db.observations,
        db.measurement_feed_evidence,
        db.rating_reference_proofs,
    )
    rows = (
        conn.execute(
            sa.select(
                o.c.timestamp,
                o.c.value,
                p.c.discharge,
                p.c.observation_id,
                p.c.station_id,
                p.c.tenant_id,
                p.c.rating_curve_id,
                p.c.feed_evidence_id,
                p.c.reference_proof_id,
                p.c.content,
                p.c.fingerprint,
                f.c.content.label("feed_content"),
                f.c.fingerprint.label("feed_fingerprint"),
                r.c.content.label("proof_content"),
                r.c.fingerprint.label("proof_fingerprint"),
            )
            .select_from(
                p.join(o, p.c.observation_id == o.c.id)
                .join(f, p.c.feed_evidence_id == f.c.id)
                .join(r, p.c.reference_proof_id == r.c.id)
            )
            .where(p.c.station_id == case.sid)
            .order_by(o.c.timestamp)
        )
        .mappings()
        .all()
    )
    assert [dict(row) for row in rows] == expected
    for index in range(4):
        issue = T + index * STEP
        ordinary_times = (
            conn.execute(
                sa.select(o.c.timestamp).where(
                    o.c.station_id == case.sid,
                    o.c.parameter == "discharge",
                    o.c.timestamp >= issue - 3 * STEP,
                    o.c.timestamp < issue,
                )
            )
            .scalars()
            .all()
        )
        if ordinary_times:  # Other controls deliberately have no ordinary history.
            latest = max(ordinary_times)
            assert any(latest < row["timestamp"] < issue for row in rows)
            assert any(row["timestamp"] == latest for row in rows) or any(
                row["timestamp"] in ordinary_times for row in rows
            )
            assert any(issue - 3 * STEP <= row["timestamp"] < latest for row in rows)


class RecordingModel(ReferenceFIForecastModel):
    def __init__(self, scope: fi.FIArtifactScope) -> None:
        super().__init__(artifact_scope=scope, deterministic=True)
        self.calls: list[tuple[datetime, fi.ModelInputs]] = []

    def predict(
        self,
        artifact: ReferenceFIArtifact,
        *,
        inputs: fi.ModelInputs,
        issue_datetime: datetime,
        rng: random.Random,
    ) -> fi.ModelResult:
        self.calls.append((issue_datetime, inputs))
        return super().predict(
            artifact, inputs=inputs, issue_datetime=issue_datetime, rng=rng
        )


@contextmanager
def worker_transaction(
    conn: sa.Connection, writes: list[str]
) -> Iterator[sa.Connection]:
    with conn.begin_nested():
        role = conn.scalar(sa.text("SELECT current_user"))
        assert role == "sapphire_worker"
        writes.append(role)
        yield conn


@dataclass(frozen=True, kw_only=True, slots=True)
class Outcome:
    scope: fi.FIArtifactScope
    calls: list[tuple[datetime, fi.ModelInputs]]
    steps: dict[StationId, list[HindcastStepResult]]
    forecasts: list[HindcastForecast]
    writes: list[str]


def run_case(
    harness: _RoleBootstrapHarness,
    scope: fi.FIArtifactScope,
    *,
    provisional: Literal["present", "absent"],
    absent: frozenset[StationId] = frozenset(),
    delta: float = 0.0,
    aligned_store_check: Literal["check", "skip"] = "skip",
) -> Outcome:
    station_ids = (SID_A,) if scope is fi.FIArtifactScope.STATION else (SID_A, SID_B)
    with harness.owner_engine.connect() as conn, conn.begin():
        try:
            if provisional == "present":
                permit_fixture(conn, DEFAULT_TENANT_ID)
            for sid in station_ids:
                case = seed_case(
                    conn, sid, "absent" if sid in absent else "ordinary", delta
                )
                if provisional == "present":
                    seed_provisional(conn, case)
            conn.execute(
                sa.insert(db.station_groups).values(
                    id=GROUP,
                    name="hindcast group",
                    tenant_id=DEFAULT_TENANT_ID,
                    created_at=CLOCK,
                )
            )
            conn.execute(
                sa.insert(db.station_group_members),
                [
                    {
                        "group_id": GROUP,
                        "station_id": sid,
                        "tenant_id": DEFAULT_TENANT_ID,
                        "created_at": CLOCK,
                    }
                    for sid in station_ids
                ],
            )
            conn.execute(
                sa.insert(db.models).values(
                    id=MODEL,
                    display_name="Hindcast isolation",
                    description="Synthetic hindcast isolation canary",
                    artifact_scope=scope.value,
                )
            )
            conn.execute(
                sa.insert(db.model_artifacts).values(
                    id=ARTIFACT,
                    model_id=MODEL,
                    station_id=SID_A if scope is fi.FIArtifactScope.STATION else None,
                    group_id=GROUP if scope is fi.FIArtifactScope.GROUP else None,
                    status="active",
                    artifact_path="unused-injected-fi-artifact",
                    sha256_hash="",
                    training_period_start=START - timedelta(days=2),
                    training_period_end=START - timedelta(days=1),
                    trained_at=START,
                )
            )
            conn.execute(sa.text("SET LOCAL ROLE sapphire_worker"))
            assert conn.scalar(sa.text("SELECT current_user")) == "sapphire_worker"
            with (
                pytest.raises(DBAPIError, match="permission denied"),
                conn.begin_nested(),
            ):
                conn.execute(sa.select(db.provisional_discharges))
            stores = factory(conn)
            model = RecordingModel(scope)
            adapter = fi.adapt_if_fi(
                model, station_code_resolver=build_station_code_resolver(stores.station)
            )
            assert isinstance(adapter, fi.ForecastInterfaceAdapter)
            assert adapter.data_requirements.lookback_steps == 3
            assert adapter.data_requirements.forecast_horizon_steps == 3
            assert adapter.data_requirements.supported_time_steps == frozenset({STEP})
            forcing = FakeWeatherReanalysisSource()
            forcing.set_records(
                [
                    make_raw_historical_forcing(
                        station_id=sid,
                        parameter="precipitation_forecast",
                        source="smn",
                        spatial_type=SpatialRepresentation.POINT,
                        valid_time=ensure_utc(START + quarter * timedelta(minutes=15)),
                        value=3.0,
                        rng=random.Random(sid.int + quarter),
                    )
                    for sid in station_ids
                    for quarter in range(44)
                ]
            )
            writes: list[str] = []
            writer = PgHindcastStore(
                conn, transaction_factory=lambda: worker_transaction(conn, writes)
            )
            if scope is fi.FIArtifactScope.STATION:
                steps = {
                    SID_A: run_station_hindcast(
                        station_id=SID_A,
                        model=adapter,
                        artifact=ReferenceFIArtifact(bias=10.0),
                        model_id=MODEL,
                        artifact_id=ARTIFACT,
                        period_start=T,
                        period_end=END,
                        time_step=STEP,
                        forcing_source=forcing,
                        obs_store=stores.obs,
                        hindcast_store=writer,
                        station_store=stores.station,
                        basin_store=stores.basin,
                        clock=lambda: CLOCK,
                        rng=random.Random(19),
                        hindcast_run_id=RUN,
                        lookback_steps=3,
                    )
                }
            else:
                group = stores.group.fetch_group(GROUP)
                assert group is not None and group.station_ids == frozenset(station_ids)
                steps = run_group_hindcast(
                    group=group,
                    model=adapter,
                    artifact=ReferenceFIArtifact(bias=10.0),
                    model_id=MODEL,
                    artifact_id=ARTIFACT,
                    period_start=T,
                    period_end=END,
                    time_step=STEP,
                    forcing_source=forcing,
                    obs_store=stores.obs,
                    hindcast_store=writer,
                    station_store=stores.station,
                    basin_store=stores.basin,
                    clock=lambda: CLOCK,
                    rng=random.Random(19),
                    hindcast_run_id=RUN,
                    lookback_steps=3,
                )
            forecasts = [
                forecast
                for sid in station_ids
                for forecast in writer.fetch_hindcasts(
                    sid, MODEL, T, END, hindcast_run_id=RUN, parameter="discharge"
                )
            ]
            if aligned_store_check == "check":
                import polars as pl

                original = forecasts[0]
                aligned = replace(
                    original,
                    ensemble=replace(
                        original.ensemble,
                        values=original.ensemble.values.with_columns(
                            (pl.col("valid_time") - STEP).alias("valid_time")
                        ),
                    ),
                )
                assert (
                    aligned.ensemble.values["valid_time"].min() == aligned.hindcast_step
                )
                with conn.begin_nested() as savepoint:
                    writer.store_hindcast(aligned)
                    stored = writer.fetch_hindcasts(
                        aligned.station_id,
                        MODEL,
                        T,
                        END,
                        hindcast_run_id=RUN,
                        parameter="discharge",
                    )
                    matched = [
                        row
                        for row in stored
                        if row.hindcast_step == aligned.hindcast_step
                    ]
                    assert len(matched) == 1
                    assert matched[0].ensemble.values.equals(aligned.ensemble.values)
                    savepoint.rollback()
            return Outcome(
                scope=scope,
                calls=model.calls,
                steps=steps,
                forecasts=forecasts,
                writes=writes,
            )
        finally:
            conn.rollback()
