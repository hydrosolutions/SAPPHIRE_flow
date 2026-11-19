from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolCreate
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from sapphire_flow.api.security import load_access_token_pepper
from sapphire_flow.cli.access_tokens import create_token
from sapphire_flow.config.deployment_identity import load_deployment_identity_config
from sapphire_flow.db.engine import create_engine_from_env
from sapphire_flow.logging import configure_cli_logging
from sapphire_flow.services.deployment_smoke import (
    CYCLE,
    MARKER,
    MODEL_ID,
    POOL,
    RESOURCE_DIR,
    RESULT,
    SmokeSetup,
    fixture_model,
    read_setup,
    require_smoke_environment,
    verify_resources,
)
from sapphire_flow.services.model_import import import_external_artifact
from sapphire_flow.services.model_onboarding import create_station_assignment
from sapphire_flow.services.model_registry import register_models
from sapphire_flow.services.write_principal import resolve_run_principal
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.audited_writer import AuditedWriter
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.model_store import PgModelStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.pipeline_health_store import PgPipelineHealthStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.store.weather_forecast_store import PgWeatherForecastStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    AccessTokenRole,
    PipelineCheckType,
    QcStatus,
    SpatialRepresentation,
    StationStatus,
    WeatherSourceRole,
    WeatherSourceStatus,
)
from sapphire_flow.types.station import StationWeatherSource

if TYPE_CHECKING:
    import sqlalchemy as sa

    from sapphire_flow.types.ids import StationId


def _stage(name: str) -> None:
    sys.stderr.write(json.dumps({"smoke_stage": name}) + "\n")


def _command(module: str, args: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        stage = (
            "station_creation"
            if module.endswith("create_station")
            else "observation_loader_qc"
        )
        sys.stderr.write(json.dumps({"stage": stage, "status": "failed"}) + "\n")
        raise ValueError("smoke setup command failed")


def _station(conn: sa.Connection, station_id: UUID | None = None) -> StationId:
    stations = PgStationStore(conn).fetch_all_stations()
    if len(stations) != 1:
        raise ValueError("smoke database must contain exactly one station")
    station = stations[0]
    tenant = PgTenantStore(conn).fetch_tenant_by_code("smoke")
    if tenant is None or (
        station.code,
        station.network,
        station.timezone,
        station.tenant_id,
    ) != ("SYN001", "synthetic", "UTC", tenant.id):
        raise ValueError("smoke station identity mismatch")
    if station_id is not None and station.id != station_id:
        raise ValueError("smoke station differs from setup evidence")
    return station.id


def _qualified_observations(conn: sa.Connection, station_id: StationId) -> None:
    observations = PgObservationStore(conn).fetch_observations(
        station_id, "discharge", CYCLE - timedelta(hours=72), CYCLE
    )
    if len(observations) != 72 or any(
        o.qc_status is not QcStatus.QC_PASSED or o.qc_flags for o in observations
    ):
        raise ValueError("smoke observations did not pass normal loader QC")
    if [o.timestamp for o in observations] != [
        CYCLE - timedelta(hours=n) for n in range(72, 0, -1)
    ]:
        raise ValueError("smoke observation coverage is incomplete")
    if [o.value for o in observations[-3:]] != [20.214988, 19.970200, 19.737592]:
        raise ValueError("smoke model lookback does not match fixture")


def setup() -> dict[str, object]:
    _stage("setup_identity")
    run_id = require_smoke_environment("owner")
    verify_resources()
    if MARKER.exists() or RESULT.exists():
        raise ValueError("smoke setup refuses an existing run")
    engine = create_engine_from_env()
    with engine.connect() as conn:
        if PgStationStore(conn).fetch_all_stations():
            raise ValueError("smoke setup requires an empty station database")
    scenario = json.loads((RESOURCE_DIR / "scenario.json").read_text())
    _stage("station_creation")
    _command(
        "sapphire_flow.cli.create_station",
        [
            "--tenant",
            "smoke",
            "--apply",
            *[
                part
                for key, value in scenario.items()
                for part in ("--" + key.replace("_", "-"), str(value))
            ],
        ],
    )
    _stage("observation_loader_qc")
    _command(
        "sapphire_flow.cli.load_station_observations",
        [
            "discharge",
            "--tenant",
            "smoke",
            "--station-code",
            "SYN001",
            "--network",
            "synthetic",
            "--csv",
            str(RESOURCE_DIR / "discharge_hourly.csv"),
            "--time-column",
            "time",
            "--value-column",
            "value",
            "--zone",
            "UTC",
            "--as-of",
            "2025-01-03",
            "--strict",
            "--apply",
        ],
    )
    model = fixture_model()
    _stage("observation_qualification")
    with engine.begin() as conn:
        sid = _station(conn)
        _qualified_observations(conn, sid)
        if PgForecastStore(conn).fetch_forecasts_for_cycle(CYCLE):
            raise ValueError("smoke setup requires no existing forecast")
        principal = resolve_run_principal(
            PgTenantStore(conn),
            load_deployment_identity_config(RESOURCE_DIR / "qc-rules.toml"),
            tenant_code="smoke",
        )
        register_models({MODEL_ID: model}, PgModelStore(conn), lambda: CYCLE)
    _stage("artifact_import")
    config_hash = model.config_hash
    if config_hash is None:
        raise ValueError("smoke artifact config hash is missing")
    with engine.connect() as conn:
        artifact_id = import_external_artifact(
            model=model,
            model_id=MODEL_ID,
            artifact_bytes=(RESOURCE_DIR / "artifact.json").read_bytes(),
            trained_at=CYCLE,
            training_period_start=CYCLE - timedelta(hours=72),
            training_period_end=CYCLE,
            expected_config_hash=config_hash,
            clock=lambda: CYCLE,
            station_id=sid,
            station_store=PgStationStore(conn),
            principal=principal,
            audit_log_store=PgAuditLogStore(conn),
            audited_writer=AuditedWriter(begin=engine.begin),
            imported_by="deployment-smoke",
            notes="Synthetic constants; no scientific training or agency data.",
        )
    _stage("assignment_activation")
    with engine.begin() as conn:
        stations = PgStationStore(conn)
        create_station_assignment(
            sid,
            MODEL_ID,
            timedelta(hours=1),
            1,
            stations,
            lambda: CYCLE,
            principal=principal,
            audit_log_store=PgAuditLogStore(conn),
            audit_rejection=False,
        )
        stations.store_weather_source(
            StationWeatherSource(
                station_id=sid,
                nwp_source="icon_ch2_eps",
                extraction_type=SpatialRepresentation.POINT,
                role=WeatherSourceRole.FORECAST,
                status=WeatherSourceStatus.ACTIVE,
            )
        )
        stations.update_station_status(sid, StationStatus.OPERATIONAL)
    marker = SmokeSetup(
        run_id=run_id, station_id=sid, artifact_id=artifact_id, observations_passed=72
    )
    MARKER.write_text(marker.model_dump_json() + "\n")
    engine.dispose()
    _stage("prefect_registration")
    asyncio.run(_register())
    return {"setup": "passed", "station_id": str(sid), "observations_passed": 72}


async def _register() -> None:
    from sapphire_flow.flows.deployment_smoke import deployment_smoke_flow

    async with get_client() as client:
        await client.create_work_pool(WorkPoolCreate(name=POOL, type="process"))
    sourced = await deployment_smoke_flow.afrom_source(
        source="/app",
        entrypoint="src/sapphire_flow/flows/deployment_smoke.py:deployment_smoke_flow",
    )
    await sourced.adeploy(
        name=POOL,
        work_pool_name=POOL,
        build=False,
        push=False,
        print_next_steps=False,
        concurrency_limit=1,
    )


def trigger() -> dict[str, object]:
    run_id = require_smoke_environment("api")
    setup_record = read_setup(run_id)
    with create_engine_from_env().connect() as conn:
        sid = _station(conn, setup_record.station_id)
        if PgForecastStore(conn).fetch_forecasts_for_cycle(CYCLE, sid):
            raise ValueError("smoke refuses a pre-existing forecast")
    with get_client(sync_client=True) as client:
        deployment = client.read_deployment_by_name(f"{POOL}/{POOL}")
        workers = client.read_workers_for_work_pool(POOL)
        if not any(w.status.value == "ONLINE" for w in workers):
            raise ValueError("smoke worker is not online")
        run = client.create_flow_run_from_deployment(
            deployment.id, parameters={"run_id": run_id}, idempotency_key=run_id
        )
    return {"parent_flow_run_id": str(run.id)}


def status(parent_id: UUID) -> dict[str, object]:
    run_id = require_smoke_environment("api")
    with get_client(sync_client=True) as client:
        run = client.read_flow_run(parent_id)
        if run.parameters != {"run_id": run_id} or run.work_pool_name != POOL:
            raise ValueError("smoke Prefect identity mismatch")
        return {"state": run.state_type.value if run.state_type else "PENDING"}


def evidence(parent_id: UUID) -> dict[str, object]:
    run_id = require_smoke_environment("api")
    setup_record = read_setup(run_id)
    evidence_record = json.loads(RESULT.read_text())
    if evidence_record["run_id"] != run_id or evidence_record[
        "parent_flow_run_id"
    ] != str(parent_id):
        raise ValueError("smoke result identity mismatch")
    with get_client(sync_client=True) as client:
        parent = client.read_flow_run(parent_id)
        child = client.read_flow_run(UUID(evidence_record["child_flow_run_id"]))
        if (
            parent.state is None
            or not parent.state.is_completed()
            or not parent.infrastructure_pid
            or parent.work_pool_name != POOL
        ):
            raise ValueError("smoke parent was not completed by its process worker")
        if (
            child.state is None
            or not child.state.is_completed()
            or child.parent_task_run_id is None
        ):
            raise ValueError("smoke nested forecast flow did not complete")
        if (
            client.read_task_run(child.parent_task_run_id).flow_run_id != parent_id
            or client.read_flow(child.flow_id).name != "forecast-cycle"
        ):
            raise ValueError("smoke forecast flow is not a child of this run")
    with create_engine_from_env().connect() as conn:
        sid = _station(conn, setup_record.station_id)
        _qualified_observations(conn, sid)
        forecasts = PgForecastStore(conn).fetch_forecasts_for_cycle(CYCLE, sid)
        if (
            len(forecasts) != 1
            or forecasts[0].model_id != MODEL_ID
            or forecasts[0].model_artifact_id != setup_record.artifact_id
        ):
            raise ValueError("smoke forecast identity mismatch")
        weather = PgWeatherForecastStore(conn).fetch_weather_forecasts(
            sid, "icon_ch2_eps", CYCLE
        )
        if [
            (r.valid_time, r.member_id, r.value)
            for r in sorted(weather, key=lambda r: r.valid_time)
        ] != [
            (CYCLE + timedelta(hours=h), 0, v)
            for h, v in ((1, 1.0), (2, 2.0), (3, 4.0))
        ]:
            raise ValueError(
                "smoke weather was not persisted through the forecast flow"
            )
        health = PgPipelineHealthStore(conn).fetch_recent()
        warnings = [
            h for h in health if h.status.value != "ok" and h.cycle_time == CYCLE
        ]
        if (
            len(warnings) != 1
            or warnings[0].check_type is not PipelineCheckType.ALERT_SUPPRESSED_FALLBACK
            or cast("dict[str, object]", warnings[0].detail)  # pyright: ignore[reportUnknownMemberType]
            != {
                "alert_eligibility": ["no_event_information"],
                "parameter": ["discharge"],
            }
        ):
            raise ValueError("smoke cycle has unexpected health reasons")
        evidence_record.update(
            forecast_id=str(forecasts[0].id),
            artifact_id=str(setup_record.artifact_id),
            observations_passed=72,
            weather_records=3,
        )
    return evidence_record


def token() -> dict[str, str]:
    run_id = require_smoke_environment("api")
    setup_record = read_setup(run_id)
    now = ensure_utc(datetime.now(UTC))
    with create_engine_from_env().begin() as conn:
        sid = _station(conn, setup_record.station_id)
        tenant = PgTenantStore(conn).fetch_tenant_by_code("smoke")
        if tenant is None:
            raise ValueError("smoke tenant is missing")
        raw = create_token(
            conn,
            name="disposable-smoke",
            role=AccessTokenRole.CONSUMER,
            tenant_id=tenant.id,
            tenant_code="smoke",
            station_ids=frozenset({sid}),
            expires_at=now + timedelta(minutes=10),
            now=now,
            pepper=load_access_token_pepper(),
        )
    # Only returned over captured exec stdout to the host, never a flow result or log.
    return {"token": raw}


class ApiProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    forecast_id: UUID
    station_id: UUID
    token: SecretStr = Field(min_length=1, max_length=1024)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _http_json(path: str, raw_token: str | None) -> tuple[int, Any]:
    headers = {"Authorization": "Bearer " + raw_token} if raw_token else {}
    request = urllib.request.Request("http://api:8000" + path, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=10) as response:
            content = response.read(65537)
            if len(content) > 65536:
                raise ValueError("smoke API response exceeds size limit")
            return response.status, json.loads(content)
    except urllib.error.HTTPError as exc:
        # Error bodies are not evidence and may contain arbitrary server text.
        return exc.code, None


def api_probe() -> dict[str, object]:
    run_id = require_smoke_environment("worker")
    setup_record = read_setup(run_id)
    content = sys.stdin.read(4097)
    if len(content) > 4096:
        raise ValueError("smoke API request exceeds size limit")
    request = ApiProbeRequest.model_validate_json(content)
    if request.station_id != setup_record.station_id:
        raise ValueError("smoke API station differs from setup")
    forecast_path = f"/api/v1/forecasts/{request.forecast_id}"
    unauthorized, _ = _http_json(forecast_path, None)
    forecast_status, forecast = _http_json(
        forecast_path, request.token.get_secret_value()
    )
    station_status, station = _http_json(
        f"/api/v1/stations/{request.station_id}", request.token.get_secret_value()
    )
    return {
        "unauthorized_status": unauthorized,
        "forecast_status": forecast_status,
        "forecast": forecast,
        "station_status": station_status,
        "station": station,
    }


def main(argv: list[str] | None = None) -> int:
    configure_cli_logging()
    parser = argparse.ArgumentParser(
        description="Internal disposable smoke setup and evidence commands"
    )
    parser.add_argument(
        "command",
        choices=["setup", "trigger", "status", "evidence", "token", "api-probe"],
    )
    parser.add_argument("--parent-id", type=UUID)
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            result = setup()
        elif args.command == "trigger":
            result = trigger()
        elif args.command == "token":
            result = token()
        elif args.command == "api-probe":
            result = api_probe()
        else:
            if args.parent_id is None:
                raise ValueError("smoke parent id is required")
            result = (
                status(args.parent_id)
                if args.command == "status"
                else evidence(args.parent_id)
            )
    except Exception:
        sys.stderr.write("smoke application stage failed\n")
        return 1
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
