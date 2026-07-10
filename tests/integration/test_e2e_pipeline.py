"""End-to-end pipeline test: onboard → train → hindcast → skill → forecast → API."""

from __future__ import annotations

import math
import os
import random
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import polars as pl
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.config.forecast_qc_rules import _default_swiss_forecast_qc_rules
from sapphire_flow.config.qc_rules import _default_swiss_qc_rules
from sapphire_flow.services.forecast_qc import ForecastOutputQualityChecker
from sapphire_flow.services.onboarding import _run_onboarding
from sapphire_flow.services.training_data import assemble_station_training_data
from sapphire_flow.store.basin_store import PgBasinStore
from sapphire_flow.store.clim_baseline_store import PgClimBaselineStore
from sapphire_flow.store.flow_regime_config_store import PgFlowRegimeConfigStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.hindcast_store import PgHindcastStore
from sapphire_flow.store.historical_forcing_store import PgHistoricalForcingStore
from sapphire_flow.store.model_artifact_store import PgModelArtifactStore
from sapphire_flow.store.model_store import PgModelStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.skill_store import PgSkillStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.tools.record_fixtures import parse_stations_toml
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    NwpCycleSource,
    QcStatus,
    StationStatus,
)
from sapphire_flow.types.ids import ModelId
from tests.fakes.fake_adapters import FakeWeatherReanalysisSource

_FIXTURE_DIR = Path("tests/fixtures/reference")


@contextmanager
def savepoint_txn(conn: sa.Connection):  # type: ignore[return]
    with conn.begin_nested():
        yield conn


def savepoint_factory(conn: sa.Connection):
    return lambda: savepoint_txn(conn)


_STATIONS_TOML = _FIXTURE_DIR / "stations.toml"
_OBSERVATIONS_PARQUET = _FIXTURE_DIR / "bafu_observations.parquet"

# All observation data spans 2023-01-01 to 2024-12-31.
# Onboarding stores and QC-checks the full 2-year range.
# Training window: year 1 (2023). Hindcast window: year 2 (2024-01 → 2024-12-01).
_OBS_START = ensure_utc(datetime(2023, 1, 1, tzinfo=UTC))
_OBS_END = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_TRAIN_START = ensure_utc(datetime(2023, 1, 1, tzinfo=UTC))
_TRAIN_END = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))
_FORECAST_ISSUE = ensure_utc(datetime(2024, 11, 30, tzinfo=UTC))

_N_MEMBERS = 50
_HORIZON = 5


def _clock() -> object:
    return ensure_utc(datetime.now(UTC))


# ---------------------------------------------------------------------------
# Dedicated e2e fixture — commits persist across all steps
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def e2e_engine(tmp_path: Path):
    """Dedicated PostGIS container for e2e test — commits persist across steps."""
    from alembic.config import Config
    from testcontainers.postgres import PostgresContainer

    from alembic import command

    with PostgresContainer(
        image="postgis/postgis:16-3.4",
        username="test",
        password="test",
        dbname="sapphire_e2e",
    ) as pg:
        url = pg.get_connection_url().replace("+psycopg2", "+psycopg")
        os.environ["DATABASE_URL"] = url
        engine = sa.create_engine(url)

        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(alembic_cfg, "head")

        yield engine, tmp_path

        engine.dispose()


# ---------------------------------------------------------------------------
# Helper: load observations from parquet into RawObservation list
# ---------------------------------------------------------------------------


def _load_raw_observations(station_configs):
    """Return dict[StationId, list[RawObservation]] from parquet fixture."""
    from sapphire_flow.types.enums import ObservationSource
    from sapphire_flow.types.observation import RawObservation

    df = pl.read_parquet(_OBSERVATIONS_PARQUET)
    code_to_cfg = {sc.code: sc for sc in station_configs}

    obs_by_station: dict = {}
    for row in df.iter_rows(named=True):
        code = row["station_code"]
        cfg = code_to_cfg.get(code)
        if cfg is None:
            continue
        sid = cfg.id
        obs = RawObservation(
            station_id=sid,
            timestamp=row["timestamp"],
            parameter=row["parameter"],
            value=row["value"],
            source=ObservationSource(row["source"]),
            rating_curve_id=None,
            rating_curve_correction_version=None,
        )
        obs_by_station.setdefault(sid, []).append(obs)

    return obs_by_station


# ---------------------------------------------------------------------------
# Main e2e test
# ---------------------------------------------------------------------------


class TestE2ePipeline:
    # Slow in GitHub-hosted CI (>10 min); runs on nightly schedule.
    # See run 24733223436 for 2026-04-21 timeout.
    @pytest.mark.slow
    def test_full_pipeline(self, e2e_engine, tmp_path: Path) -> None:  # type: ignore[override]
        engine, artifact_dir = e2e_engine

        # -----------------------------------------------------------------------
        # Setup: stores, models, stations, config
        # -----------------------------------------------------------------------
        t_setup = time.perf_counter()

        deployment_cfg = DeploymentConfig(
            max_retention_days=1000,
            enable_forecast_alerts=False,
        )
        qc_rules = _default_swiss_qc_rules()
        forecast_qc_rules = _default_swiss_forecast_qc_rules()
        rng = random.Random(42)

        # Load + patch stations: set forecast_targets from measured_parameters
        station_configs_raw = parse_stations_toml(_STATIONS_TOML)
        station_configs = [
            replace(
                s,
                forecast_targets=frozenset({"discharge"}),
            )
            for s in station_configs_raw
        ]
        assert len(station_configs) == 7

        # Load observations from parquet
        obs_by_station = _load_raw_observations(station_configs)
        assert len(obs_by_station) == 7

        import structlog

        log = structlog.get_logger("e2e")
        log.info(
            "e2e.setup_complete",
            duration_ms=round((time.perf_counter() - t_setup) * 1000, 1),
        )

        # -----------------------------------------------------------------------
        # Step 1 — Onboard stations (store, QC, baselines, flow regime, train)
        # -----------------------------------------------------------------------
        t1 = time.perf_counter()

        with engine.begin() as conn:
            basin_store = PgBasinStore(conn)
            station_store = PgStationStore(conn)
            obs_store = PgObservationStore(conn)
            forcing_store = PgHistoricalForcingStore(conn)
            baseline_store = PgClimBaselineStore(conn)
            flow_regime_store = PgFlowRegimeConfigStore(conn)
            model_store = PgModelStore(conn)
            artifact_store = PgModelArtifactStore(conn, artifact_dir)
            group_store = PgStationGroupStore(
                conn, transaction_factory=savepoint_factory(conn)
            )
            hindcast_store = PgHindcastStore(
                conn, transaction_factory=savepoint_factory(conn)
            )
            skill_store = PgSkillStore(conn)

            result = _run_onboarding(
                stations=station_configs,
                basins=[],
                obs_by_station=obs_by_station,
                forcing_by_station={},
                basin_store=basin_store,
                station_store=station_store,
                obs_store=obs_store,
                forcing_store=forcing_store,
                baseline_store=baseline_store,
                flow_regime_store=flow_regime_store,
                qc_rules=qc_rules,
                clock=_clock,
                # Run QC over the full 2-year span so year-2 observations are also
                # QC-flagged and available for hindcast in step 3.
                start_utc=_OBS_START,
                end_utc=_OBS_END,
                model_store=model_store,
                artifact_store=artifact_store,
                group_store=group_store,
                hindcast_store=hindcast_store,
                skill_store=skill_store,
                forcing_source=FakeWeatherReanalysisSource([]),
                deployment_config=deployment_cfg,
            )

        log.info(
            "e2e.step1_onboarding_complete",
            stations_created=result.stations_created,
            observations_imported=result.observations_imported,
            qc_passed=result.observations_qc_passed,
            qc_suspect=result.observations_qc_suspect,
            baselines=result.baselines_computed,
            models_trained=result.models_trained,
            errors=result.errors,
            duration_ms=round((time.perf_counter() - t1) * 1000, 1),
        )

        assert result.errors == [], f"Onboarding errors: {result.errors}"
        assert result.stations_created == 7
        assert result.observations_imported > 0
        assert result.observations_qc_passed + result.observations_qc_suspect > 0
        assert result.baselines_computed > 0

        # -----------------------------------------------------------------------
        # Step 2 — Training: assemble training data + train model for each station
        # -----------------------------------------------------------------------
        t2 = time.perf_counter()

        from sapphire_flow.services.model_registry import (
            discover_models,
            register_models,
        )
        from sapphire_flow.services.training import promote_artifact

        models_discovered = discover_models()
        assert "linear_regression_daily" in {str(mid) for mid in models_discovered}, (
            "LinearRegressionDaily not discovered"
        )
        lrd_id = ModelId("linear_regression_daily")
        lrd_model = models_discovered[lrd_id]
        time_step = timedelta(hours=24)

        artifacts_trained: dict = {}

        with engine.begin() as conn:
            station_store = PgStationStore(conn)
            obs_store = PgObservationStore(conn)
            basin_store = PgBasinStore(conn)
            artifact_store = PgModelArtifactStore(conn, artifact_dir)
            model_store = PgModelStore(conn)

            register_models(models_discovered, model_store, _clock)

            for sc in station_configs:
                sid = sc.id
                training_data = assemble_station_training_data(
                    station_id=sid,
                    model=lrd_model,
                    period_start=_TRAIN_START,
                    period_end=_TRAIN_END,
                    time_step=time_step,
                    forcing_source=FakeWeatherReanalysisSource([]),
                    obs_store=obs_store,
                    basin_store=basin_store,
                    station_store=station_store,
                )
                if training_data is None:
                    continue

                artifact = lrd_model.train(training_data, {}, rng)
                artifact_bytes = lrd_model.serialize_artifact(artifact)

                aid, _ = artifact_store.store_artifact(
                    lrd_id,
                    artifact_bytes,
                    _TRAIN_START,
                    _TRAIN_END,
                    ensure_utc(datetime.now(UTC)),
                    station_id=sid,
                )
                promote_artifact(
                    artifact_store=artifact_store,
                    model_id=lrd_id,
                    new_id=aid,
                    station_id=sid,
                )
                artifacts_trained[sid] = aid

            # Transition stations to OPERATIONAL
            for sc in station_configs:
                station_store.update_station_status(sc.id, StationStatus.OPERATIONAL)

        log.info(
            "e2e.step2_training_complete",
            stations_trained=len(artifacts_trained),
            duration_ms=round((time.perf_counter() - t2) * 1000, 1),
        )

        assert len(artifacts_trained) == 7, (
            f"Expected 7 trained stations, got {len(artifacts_trained)}"
        )

        # Verify ACTIVE artifacts exist in DB
        with engine.connect() as conn:
            artifact_store = PgModelArtifactStore(conn, artifact_dir)
            for sc in station_configs:
                active = artifact_store.fetch_active_artifact_for_station(sc.id, lrd_id)
                assert active is not None, f"No active artifact for station {sc.code}"

        # -----------------------------------------------------------------------
        # Step 3 — Hindcast over the second year
        # -----------------------------------------------------------------------
        t3 = time.perf_counter()

        from sapphire_flow.services.hindcast import run_station_hindcast

        hindcast_start = ensure_utc(datetime(2024, 1, 1, tzinfo=UTC))
        hindcast_end = ensure_utc(datetime(2024, 12, 1, tzinfo=UTC))
        hindcast_run_id = uuid4()

        total_hindcast_steps = 0

        with engine.begin() as conn:
            station_store = PgStationStore(conn)
            obs_store = PgObservationStore(conn)
            basin_store = PgBasinStore(conn)
            artifact_store = PgModelArtifactStore(conn, artifact_dir)
            hindcast_store = PgHindcastStore(
                conn, transaction_factory=savepoint_factory(conn)
            )

            for sc in station_configs:
                sid = sc.id
                artifact_result = artifact_store.fetch_active_artifact_for_station(
                    sid, lrd_id
                )
                if artifact_result is None:
                    continue
                aid, artifact_bytes = artifact_result
                artifact = lrd_model.deserialize_artifact(artifact_bytes)

                results = run_station_hindcast(
                    model=lrd_model,
                    artifact=artifact,
                    station_id=sid,
                    model_id=lrd_id,
                    artifact_id=aid,
                    period_start=hindcast_start,
                    period_end=hindcast_end,
                    time_step=time_step,
                    forcing_source=FakeWeatherReanalysisSource([]),
                    obs_store=obs_store,
                    hindcast_store=hindcast_store,
                    station_store=station_store,
                    basin_store=basin_store,
                    clock=_clock,
                    rng=rng,
                    hindcast_run_id=hindcast_run_id,
                )
                successes = sum(1 for r in results if r.success)
                total_hindcast_steps += successes

        log.info(
            "e2e.step3_hindcast_complete",
            total_steps=total_hindcast_steps,
            duration_ms=round((time.perf_counter() - t3) * 1000, 1),
        )

        assert total_hindcast_steps > 0, "No successful hindcast steps"

        # Verify hindcast values are non-negative
        with engine.connect() as conn:
            hindcast_store = PgHindcastStore(conn)
            for sc in station_configs:
                hcasts = hindcast_store.fetch_hindcasts(
                    station_id=sc.id,
                    model_id=lrd_id,
                    start=hindcast_start,
                    end=hindcast_end,
                )
                if not hcasts:
                    continue
                for hc in hcasts[:3]:  # spot-check first 3
                    vals = hc.ensemble.values["value"].to_list()
                    assert all(v >= 0.0 for v in vals), (
                        f"Negative hindcast value for station {sc.code}"
                    )
                    # Check member count
                    members = hc.ensemble.values["member_id"].n_unique()
                    assert members == _N_MEMBERS, (
                        f"Expected {_N_MEMBERS} members, got {members}"
                    )

        # -----------------------------------------------------------------------
        # Step 4 — Compute skill scores
        # -----------------------------------------------------------------------
        t4 = time.perf_counter()

        from uuid import uuid4 as _uuid4

        from sapphire_flow.services.skill.service import compute_skill_for_station
        from sapphire_flow.types.enums import ForcingType, SkillSource

        total_skill_scores = 0

        with engine.begin() as conn:
            obs_store = PgObservationStore(conn)
            hindcast_store = PgHindcastStore(conn)
            skill_store = PgSkillStore(conn)
            flow_regime_store = PgFlowRegimeConfigStore(conn)
            artifact_store = PgModelArtifactStore(conn, artifact_dir)

            for sc in station_configs:
                sid = sc.id
                artifact_result = artifact_store.fetch_active_artifact_for_station(
                    sid, lrd_id
                )
                if artifact_result is None:
                    continue
                aid = artifact_result[0]

                hindcasts = hindcast_store.fetch_hindcasts(
                    station_id=sid,
                    model_id=lrd_id,
                    start=hindcast_start,
                    end=hindcast_end,
                )
                if not hindcasts:
                    continue

                observations = obs_store.fetch_observations(
                    station_id=sid,
                    parameter="discharge",
                    start=hindcast_start,
                    end=hindcast_end,
                    qc_status=QcStatus.QC_PASSED,
                )
                if not observations:
                    continue

                flow_regime = flow_regime_store.fetch_latest(
                    station_id=sid,
                    parameter="discharge",
                )

                scores, diagrams = compute_skill_for_station(
                    station_id=sid,
                    model_id=lrd_id,
                    artifact_id=aid,
                    hindcasts=hindcasts,
                    observations=observations,
                    thresholds=[],
                    flow_regime_config=flow_regime,
                    seasons=[],
                    skill_source=SkillSource.HINDCAST_REANALYSIS,
                    forcing_type=ForcingType.REANALYSIS,
                    clock=_clock,
                    uuid_factory=_uuid4,
                    parameter="discharge",
                )

                if scores:
                    skill_store.store_skill_scores(scores)
                    total_skill_scores += len(scores)
                if diagrams:
                    skill_store.store_skill_diagrams(diagrams)

        log.info(
            "e2e.step4_skill_complete",
            total_scores=total_skill_scores,
            duration_ms=round((time.perf_counter() - t4) * 1000, 1),
        )

        assert total_skill_scores > 0, "No skill scores computed"

        # Verify expected metrics exist in DB for at least one station
        with engine.connect() as conn:
            skill_store = PgSkillStore(conn)
            artifact_store = PgModelArtifactStore(conn, artifact_dir)
            sc = station_configs[0]
            active = artifact_store.fetch_active_artifact(lrd_id, station_id=sc.id)
            assert active is not None, f"No active artifact for {sc.code}"
            active_artifact_id = active[0]
            all_scores = skill_store.fetch_skill_scores(
                model_id=lrd_id,
                model_artifact_id=active_artifact_id,
            )
            assert all_scores, f"No skill scores for station {sc.code}"
            metric_names = {s.metric for s in all_scores}
            for expected_metric in ("crps", "nse", "kge", "pbias", "mae"):
                assert expected_metric in metric_names, (
                    f"Missing metric '{expected_metric}' for {sc.code}. "
                    f"Got: {sorted(metric_names)}"
                )
            # Sanity checks on values
            crps_scores = [
                s.score
                for s in all_scores
                if s.metric == "crps" and s.season is None and s.flow_regime is None
            ]
            assert any(v > 0 for v in crps_scores), "CRPS should be > 0"
            pbias_scores = [
                s.score
                for s in all_scores
                if s.metric == "pbias" and s.season is None and s.flow_regime is None
            ]
            assert all(math.isfinite(v) for v in pbias_scores), (
                "PBIAS contains non-finite values"
            )
            nse_scores = [
                s.score
                for s in all_scores
                if s.metric == "nse" and s.season is None and s.flow_regime is None
            ]
            assert all(v > -1.0 for v in nse_scores), "NSE below -1.0 (catastrophic)"

        # -----------------------------------------------------------------------
        # Step 5 — Run operational forecast
        # -----------------------------------------------------------------------
        t5 = time.perf_counter()

        from sapphire_flow.services.operational_inputs import (
            OperationalInputMetadata,
        )
        from sapphire_flow.services.run_station_forecast import run_station_forecast
        from sapphire_flow.types.enums import WarmUpSource
        from sapphire_flow.types.model import StationInputData, StationModelInputs

        qc_checker = ForecastOutputQualityChecker()
        forecast_stored_count = 0

        with engine.begin() as conn:
            station_store = PgStationStore(conn)
            obs_store = PgObservationStore(conn)
            basin_store = PgBasinStore(conn)
            artifact_store = PgModelArtifactStore(conn, artifact_dir)
            forecast_store = PgForecastStore(
                conn, transaction_factory=savepoint_factory(conn)
            )
            baseline_store = PgClimBaselineStore(conn)

            for sc in station_configs:
                sid = sc.id
                # Fetch QC-passed observations for the lookback window
                lookback_start = ensure_utc(
                    _FORECAST_ISSUE
                    - lrd_model.data_requirements.lookback_steps * time_step
                )
                observations = obs_store.fetch_observations(
                    station_id=sid,
                    parameter="discharge",
                    start=lookback_start,
                    end=_FORECAST_ISSUE,
                    qc_status=QcStatus.QC_PASSED,
                )
                if not observations:
                    continue

                # Build past_targets DataFrame
                rows = [
                    {"timestamp": o.timestamp, "discharge": o.value}
                    for o in observations
                ]
                past_targets_df = pl.DataFrame(rows)
                from sapphire_flow.services.training_data import resample_to_time_step

                past_targets_df = resample_to_time_step(past_targets_df, time_step)

                inputs = StationModelInputs(
                    station_id=sid,
                    data=StationInputData(
                        past_targets=past_targets_df,
                        past_dynamic=pl.DataFrame(
                            schema={"timestamp": pl.Datetime("us", "UTC")}
                        ),
                        future_dynamic=pl.DataFrame(
                            schema={"timestamp": pl.Datetime("us", "UTC")}
                        ),
                        static=None,
                    ),
                    issue_time=_FORECAST_ISSUE,
                    forecast_horizon_steps=_HORIZON,
                    time_step=time_step,
                )
                input_meta = OperationalInputMetadata(
                    warm_up_source=WarmUpSource.COLD_START,
                    warm_up_state_age_hours=None,
                    observation_staleness_hours=0.0,
                    prior_state=None,
                    nwp_age_hours=0.0,
                )

                all_assignments = station_store.fetch_model_assignments(sid)
                # Only use LRD assignment for this step to get member-based forecasts.
                # Climatology and persistence produce quantile ensembles which the
                # verification below doesn't handle.
                assignments = [a for a in all_assignments if a.model_id == lrd_id]
                if not assignments:
                    # create a synthetic assignment for the model
                    from sapphire_flow.types.enums import ModelAssignmentStatus
                    from sapphire_flow.types.station import ModelAssignment

                    assignments = [
                        ModelAssignment(
                            station_id=sid,
                            model_id=lrd_id,
                            time_step=time_step,
                            status=ModelAssignmentStatus.ACTIVE,
                            priority=0,
                            created_at=ensure_utc(datetime.now(UTC)),
                        )
                    ]

                baselines = baseline_store.fetch_baselines(sid, "discharge")

                result_fc = run_station_forecast(
                    station_id=sid,
                    inputs=inputs,
                    input_metadata=input_meta,
                    assignments=assignments,
                    models=models_discovered,
                    artifact_store=artifact_store,
                    qc_checker=qc_checker,
                    qc_rules=forecast_qc_rules,
                    qc_overrides=[],
                    baselines=baselines,
                    nwp_cycle_reference_time=_FORECAST_ISSUE,
                    nwp_cycle_source=NwpCycleSource.PRIMARY,
                    config=deployment_cfg,
                    clock=_clock,
                    id_gen=uuid4,
                    rng=rng,
                )

                if result_fc is not None:
                    for forecast in result_fc.forecasts:
                        forecast_store.store_forecast(forecast)
                        forecast_stored_count += 1

        log.info(
            "e2e.step5_forecast_complete",
            forecasts_stored=forecast_stored_count,
            duration_ms=round((time.perf_counter() - t5) * 1000, 1),
        )

        assert forecast_stored_count > 0, "No forecasts stored"

        # Verify forecasts are physically plausible
        with engine.connect() as conn:
            forecast_store = PgForecastStore(conn)
            for sc in station_configs:
                summaries, total = forecast_store.fetch_forecast_summaries(
                    sc.id,
                    start=ensure_utc(datetime(2024, 11, 1, tzinfo=UTC)),
                    end=ensure_utc(datetime(2025, 1, 1, tzinfo=UTC)),
                )
                if not summaries:
                    continue
                # Get a full forecast for spot-checking
                fc_id = summaries[0].id
                fc = forecast_store.fetch_forecast(fc_id)
                if fc is None:
                    continue
                vals = fc.ensemble.values["value"].to_list()
                assert all(0 <= v < 10_000 for v in vals), (
                    f"Forecast values out of range for {sc.code}: "
                    f"min={min(vals):.1f}, max={max(vals):.1f}"
                )
                members = fc.ensemble.values["member_id"].n_unique()
                assert members == _N_MEMBERS

        # -----------------------------------------------------------------------
        # Step 6 — Query API
        # -----------------------------------------------------------------------
        t6 = time.perf_counter()

        from sapphire_flow.api import app
        from sapphire_flow.api.deps import get_connection, get_connection_rw, get_stores

        # Wire real stores from our e2e engine.
        # Override get_connection/get_connection_rw; get_stores chains on get_connection
        # automatically via Depends, so no separate override needed.
        def _override_connection():
            with engine.connect() as conn:
                yield conn

        def _override_connection_rw():
            with engine.begin() as conn:
                yield conn

        app.dependency_overrides[get_connection] = _override_connection
        app.dependency_overrides[get_connection_rw] = _override_connection_rw

        try:
            with TestClient(app, raise_server_exceptions=True) as client:
                # GET /api/v1/health
                resp = client.get("/api/v1/health")
                assert resp.status_code == 200, f"Health check failed: {resp.text}"
                health_data = resp.json()
                assert health_data["status"] == "ok"

                # GET /api/v1/stations → 7 stations
                resp = client.get("/api/v1/stations?limit=20")
                assert resp.status_code == 200, f"List stations failed: {resp.text}"
                stations_data = resp.json()
                assert stations_data["total"] == 7, (
                    f"Expected 7 stations, got {stations_data['total']}"
                )
                station_ids = [s["id"] for s in stations_data["items"]]
                assert len(station_ids) == 7

                # GET /api/v1/stations/{id} → station detail
                first_station_id = station_ids[0]
                resp = client.get(f"/api/v1/stations/{first_station_id}")
                assert resp.status_code == 200, f"Get station failed: {resp.text}"
                detail = resp.json()
                assert detail["id"] == first_station_id
                assert detail["network"] == "BAFU"
                assert detail["station_status"] == "operational"

                # GET /api/v1/stations/{id}/observations — real QC-passed data
                resp = client.get(
                    f"/api/v1/stations/{first_station_id}/observations",
                    params={
                        "parameter": "discharge",
                        "start": "2023-06-01T00:00:00Z",
                        "end": "2023-06-08T00:00:00Z",
                        "qc_status": "qc_passed",
                    },
                )
                assert resp.status_code == 200, f"List observations failed: {resp.text}"
                obs_data = resp.json()
                assert len(obs_data) > 0, "Expected non-empty observations"
                for o in obs_data[:3]:
                    assert o["parameter"] == "discharge"
                    assert o["value"] is not None
                    assert o["qc_status"] == "qc_passed"

                # GET /api/v1/stations/{id}/forecasts
                resp = client.get(
                    f"/api/v1/stations/{first_station_id}/forecasts",
                    params={
                        "start": "2024-11-01T00:00:00Z",
                        "end": "2025-01-01T00:00:00Z",
                    },
                )
                assert resp.status_code == 200, f"List forecasts failed: {resp.text}"
                resp.json()
                # At least one of the stations should have a forecast
                # (test the station that actually had observations + a trained model)

        finally:
            app.dependency_overrides.pop(get_connection, None)
            app.dependency_overrides.pop(get_connection_rw, None)
            app.dependency_overrides.pop(get_stores, None)

        log.info(
            "e2e.step6_api_complete",
            duration_ms=round((time.perf_counter() - t6) * 1000, 1),
        )

        # -----------------------------------------------------------------------
        # Summary + performance baseline (§A3 / §E7)
        # -----------------------------------------------------------------------
        total_ms = round((time.perf_counter() - t_setup) * 1000, 1)
        log.info(
            "e2e.pipeline_complete",
            total_duration_ms=total_ms,
            stations=7,
            observations_imported=result.observations_imported,
            models_trained=len(artifacts_trained),
            hindcast_steps=total_hindcast_steps,
            skill_scores=total_skill_scores,
            forecasts_stored=forecast_stored_count,
        )

        # Performance baseline: write on first run, compare on subsequent
        import json

        baseline_path = Path("tests/fixtures/reference/performance_baseline.json")
        current_timings = {
            "step1_onboarding_ms": round((t2 - t1) * 1000, 1),
            "step2_training_ms": round((t3 - t2) * 1000, 1),
            "step3_hindcast_ms": round((t4 - t3) * 1000, 1),
            "step4_skill_ms": round((t5 - t4) * 1000, 1),
            "step5_forecast_ms": round((t6 - t5) * 1000, 1),
            "total_ms": total_ms,
        }

        if not baseline_path.exists():
            baseline_path.write_text(json.dumps(current_timings, indent=2) + "\n")
            log.warning(
                "e2e.baseline_created",
                path=str(baseline_path),
                message="Commit to repo before CI can compare",
            )
        else:
            baseline = json.loads(baseline_path.read_text())
            for step, current_val in current_timings.items():
                base_val = baseline.get(step)
                if base_val and base_val > 0:
                    regression = (current_val - base_val) / base_val
                    if regression > 0.5:
                        log.warning(
                            "e2e.performance_regression",
                            step=step,
                            baseline_ms=base_val,
                            current_ms=current_val,
                            regression_pct=round(regression * 100, 1),
                        )
