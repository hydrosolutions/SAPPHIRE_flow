from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa

from sapphire_flow.db.metadata import model_artifacts, models, stations
from sapphire_flow.store.hindcast_store import PgHindcastStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import EnsembleRepresentation, ForcingType
from sapphire_flow.types.forecast import HindcastForecast
from sapphire_flow.types.ids import ArtifactId, HindcastForecastId, ModelId, StationId
from tests.conftest import make_forecast_ensemble

_T0 = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_T1 = ensure_utc(datetime(2025, 6, 1, tzinfo=UTC))
_T2 = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return ensure_utc(datetime(year, month, day, hour, tzinfo=UTC))


def _seed_station(conn: sa.Connection) -> StationId:
    sid = StationId(uuid4())
    conn.execute(
        sa.insert(stations).values(
            id=sid,
            code=f"HC-{sid.hex[:6]}",
            name="Hindcast Test Station",
            location="SRID=4326;POINT(8.5 47.4)",
            station_kind="river",
            network="bafu",
            timezone="Europe/Zurich",
            measured_parameters=["discharge"],
            ownership="own",
        )
    )
    return sid


def _seed_model(conn: sa.Connection) -> ModelId:
    mid = ModelId(f"hc_model_{uuid.uuid4().hex[:8]}")
    conn.execute(
        sa.insert(models).values(
            id=mid,
            display_name="Hindcast Test Model",
            artifact_scope="station",
            description="Integration test hindcast",
        )
    )
    return mid


def _seed_artifact(
    conn: sa.Connection, model_id: ModelId, station_id: StationId
) -> ArtifactId:
    aid = ArtifactId(uuid4())
    conn.execute(
        sa.insert(model_artifacts).values(
            id=aid,
            model_id=model_id,
            station_id=station_id,
            group_id=None,
            status="active",
            artifact_path=f"artifacts/{aid}.bin",
            sha256_hash="",
            training_period_start=_T0,
            training_period_end=_T1,
            trained_at=_T2,
        )
    )
    return aid


def _make_hindcast(
    station_id: StationId,
    model_id: ModelId,
    artifact_id: ArtifactId,
    *,
    hindcast_step: object | None = None,
    forcing_type: ForcingType = ForcingType.NWP_ARCHIVE,
    hindcast_run_id: UUID | None = None,
    representation: EnsembleRepresentation = EnsembleRepresentation.MEMBERS,
    n_members: int = 3,
    n_steps: int = 5,
) -> HindcastForecast:
    step = hindcast_step if hindcast_step is not None else _utc(2025, 3, 1)
    run_id = hindcast_run_id or uuid4()
    ensemble = make_forecast_ensemble(
        station_id=station_id,
        representation=representation,
        n_members=n_members,
        n_steps=n_steps,
    )
    return HindcastForecast(
        id=HindcastForecastId(uuid4()),
        station_id=station_id,
        model_id=model_id,
        model_artifact_id=artifact_id,
        hindcast_step=step,  # type: ignore[arg-type]
        forcing_type=forcing_type,
        representation=representation,
        hindcast_run_id=run_id,
        ensemble=ensemble,
        created_at=_T0,
    )


class TestStoreAndFetch:
    def test_store_and_fetch(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(db_connection)

        step = _utc(2025, 3, 1)
        hindcast = _make_hindcast(sid, mid, aid, hindcast_step=step)
        returned_id = store.store_hindcast(hindcast)

        assert returned_id == hindcast.id

        results = store.fetch_hindcasts(sid, mid, _utc(2025, 2, 28), _utc(2025, 3, 2))
        assert len(results) == 1
        fetched = results[0]

        assert fetched.id == hindcast.id
        assert fetched.station_id == sid
        assert fetched.model_id == mid
        assert fetched.model_artifact_id == aid
        assert fetched.hindcast_step == step
        assert fetched.forcing_type == ForcingType.NWP_ARCHIVE
        assert fetched.representation == EnsembleRepresentation.MEMBERS
        assert fetched.hindcast_run_id == hindcast.hindcast_run_id
        assert fetched.ensemble.parameter == hindcast.ensemble.parameter
        assert fetched.ensemble.units == hindcast.ensemble.units
        assert not fetched.ensemble.values.is_empty()


class TestFetchHalfOpenRange:
    def test_half_open_range(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(db_connection)
        run_id = uuid4()

        step_before = _utc(2025, 4, 30)
        step_start = _utc(2025, 5, 1)
        step_mid = _utc(2025, 5, 2)
        step_end = _utc(2025, 5, 3)

        for step in [step_before, step_start, step_mid, step_end]:
            store.store_hindcast(
                _make_hindcast(
                    sid, mid, aid, hindcast_step=step, hindcast_run_id=run_id
                )
            )

        results = store.fetch_hindcasts(sid, mid, step_start, step_end)
        fetched_steps = {h.hindcast_step for h in results}

        assert step_before not in fetched_steps
        assert step_start in fetched_steps
        assert step_mid in fetched_steps
        assert step_end not in fetched_steps


class TestFetchWithForcingTypeFilter:
    def test_forcing_type_filter(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(db_connection)

        step = _utc(2025, 6, 1)
        run_id = uuid4()

        store.store_hindcast(
            _make_hindcast(
                sid,
                mid,
                aid,
                hindcast_step=step,
                forcing_type=ForcingType.NWP_ARCHIVE,
                hindcast_run_id=run_id,
            )
        )
        store.store_hindcast(
            _make_hindcast(
                sid,
                mid,
                aid,
                hindcast_step=step,
                forcing_type=ForcingType.REANALYSIS,
                hindcast_run_id=run_id,
            )
        )

        nwp_results = store.fetch_hindcasts(
            sid,
            mid,
            _utc(2025, 5, 31),
            _utc(2025, 6, 2),
            forcing_type=ForcingType.NWP_ARCHIVE,
        )
        assert len(nwp_results) == 1
        assert nwp_results[0].forcing_type == ForcingType.NWP_ARCHIVE

        rean_results = store.fetch_hindcasts(
            sid,
            mid,
            _utc(2025, 5, 31),
            _utc(2025, 6, 2),
            forcing_type=ForcingType.REANALYSIS,
        )
        assert len(rean_results) == 1
        assert rean_results[0].forcing_type == ForcingType.REANALYSIS

        all_results = store.fetch_hindcasts(
            sid, mid, _utc(2025, 5, 31), _utc(2025, 6, 2)
        )
        assert len(all_results) == 2


class TestFetchWithRunIdFilter:
    def test_run_id_filter(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        aid = _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(db_connection)

        step = _utc(2025, 7, 1)
        run_a = uuid4()
        run_b = uuid4()

        store.store_hindcast(
            _make_hindcast(sid, mid, aid, hindcast_step=step, hindcast_run_id=run_a)
        )
        store.store_hindcast(
            _make_hindcast(sid, mid, aid, hindcast_step=step, hindcast_run_id=run_b)
        )

        results_a = store.fetch_hindcasts(
            sid, mid, _utc(2025, 6, 30), _utc(2025, 7, 2), hindcast_run_id=run_a
        )
        assert len(results_a) == 1
        assert results_a[0].hindcast_run_id == run_a

        results_b = store.fetch_hindcasts(
            sid, mid, _utc(2025, 6, 30), _utc(2025, 7, 2), hindcast_run_id=run_b
        )
        assert len(results_b) == 1
        assert results_b[0].hindcast_run_id == run_b


class TestFetchEmpty:
    def test_fetch_returns_empty_list(self, db_connection: sa.Connection) -> None:
        sid = _seed_station(db_connection)
        mid = _seed_model(db_connection)
        _seed_artifact(db_connection, mid, sid)
        store = PgHindcastStore(db_connection)

        results = store.fetch_hindcasts(sid, mid, _utc(2024, 1, 1), _utc(2024, 12, 31))
        assert results == []
