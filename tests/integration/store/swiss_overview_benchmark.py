import cProfile
import gzip
import os
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from statistics import quantiles
from time import perf_counter
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

import polars as pl
import pytest
import sqlalchemy as sa

from sapphire_flow.api.publication_gate import PublicationGate
from sapphire_flow.api.routes import api_swiss_overview as route
from sapphire_flow.api.security import Principal
from sapphire_flow.config.swiss_overview import SwissOverviewConfig
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rejected_forecast_store import PgRejectedForecastStore
from sapphire_flow.store.station_group_store import PgStationGroupStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.daily_forecast_claim import (
    DailyClaim,
    DailyClaimKey,
    FrozenDailyAssignment,
)
from sapphire_flow.types.daily_forecast_outcome import (
    DailyMemberOutcome,
    DailyMemberState,
    DailyOutputReference,
    DailyOutputSource,
)
from sapphire_flow.types.daily_input_provenance import DailyInputProvenance
from sapphire_flow.types.daily_output import DailyOutputContract, DailyOutputEvidence
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import AccessTokenRole, EnsembleRepresentation, QcStatus
from sapphire_flow.types.evaluation_forecast import EvaluationReadSettings
from sapphire_flow.types.forecast_reference import ForecastOutputKey
from sapphire_flow.types.ids import AccessTokenId, StationId
from sapphire_flow.types.swiss_overview import SwissRosterEntry
from tests.conftest import make_forecast_ensemble, make_observation, make_station_config
from tests.fakes.bafu_archive import write_raw_evidence
from tests.integration.store.test_forecast_store import (
    _make_forecast,  # pyright: ignore[reportPrivateUsage]
)
from tests.swiss_overview_benchmark_diagnostics import record_then_assert_budgets
from tests.unit.services.forecast_lab.test_bafu_archive import (
    _rows_from_fixture,  # pyright: ignore[reportPrivateUsage]
    _write_run,  # pyright: ignore[reportPrivateUsage]
)


def run_swiss_overview_benchmark(
    *,
    shape: Literal["stress_members", "production_quantiles"],
    isolated_committed_db_engine: sa.Engine,
    assignment: tuple[DailyClaimKey, FrozenDailyAssignment],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key, frozen = assignment
    now = ensure_utc(key.context.issue_time + timedelta(hours=1, minutes=30))
    production = shape == "production_quantiles"
    levels = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
    steps = 10 if production else 72
    step = timedelta(days=1) if production else timedelta(hours=1)
    contract = DailyOutputContract(
        parameter="discharge",
        units="m³/s",
        representation=(
            EnsembleRepresentation.QUANTILES
            if production
            else EnsembleRepresentation.MEMBERS
        ),
        time_step=step,
        first_lead=timedelta(0) if production else step,
        horizon_steps=steps,
        series_keys=levels if production else tuple(float(i) for i in range(20)),
    )
    # Both shapes share a committed database; station codes are globally unique.
    station_code_offset = 0 if production else 134
    stations = [
        make_station_config(
            station_id=StationId(uuid4()),
            code=f"overview-{i + station_code_offset}",
            tenant_id=key.tenant_id,
        )
        for i in range(134)
    ]
    with isolated_committed_db_engine.begin() as conn:
        for station in stations:
            PgStationStore(conn).store_station(station)
            PgObservationStore(conn).store_observations(
                [
                    replace(
                        make_observation(
                            station_id=station.id,
                            timestamp=ensure_utc(now - timedelta(hours=h)),
                            qc_status=QcStatus.QC_PASSED,
                        ),
                        id=uuid4(),
                    )
                    for h in range(5)
                ]
            )
    members = tuple(
        DailyMemberOutcome(
            station_id=s.id,
            required_parameters=("discharge",),
            state=DailyMemberState.PENDING,
        )
        for s in stations
    )
    frozen = replace(frozen, members=members, output_contracts=(contract,))
    daily = PgDailyForecastStore(isolated_committed_db_engine.begin)
    claim = daily.claim(
        key,
        frozen,
        attempt_id=uuid4(),
        owner_id=uuid4(),
        started_at=key.context.issue_time,
    )
    assert isinstance(claim, DailyClaim)
    outcomes: list[DailyMemberOutcome] = []
    evidence: list[DailyOutputEvidence] = []
    with isolated_committed_db_engine.begin() as conn:
        for member in members:
            ensemble = make_forecast_ensemble(
                station_id=member.station_id,
                model_id=key.model_id,
                n_steps=72,
                n_members=20,
            )
            ensemble = replace(
                ensemble,
                issued_at=key.context.issue_time,
                values=ensemble.values.with_columns(
                    pl.col("valid_time") + (key.context.issue_time - ensemble.issued_at)
                ),
            )
            if production:
                ensemble = ForecastEnsemble.from_quantiles(
                    station_id=member.station_id,
                    model_id=key.model_id,
                    issued_at=key.context.issue_time,
                    parameter="discharge",
                    units="m³/s",
                    time_step=step,
                    values=pl.DataFrame(
                        [
                            {
                                "valid_time": key.context.issue_time + step * lead,
                                "quantile": q,
                                "value": (
                                    10.123456789012345
                                    + lead * 1.2345678901234567
                                    + q * 0.9876543210987654
                                ),
                            }
                            for lead in range(steps)
                            for q in levels
                        ]
                    ).with_columns(pl.col("valid_time").cast(pl.Datetime("us", "UTC"))),
                )
            forecast = replace(
                _make_forecast(member.station_id, key.model_id, frozen.artifact_id),
                ensemble=ensemble,
                representation=ensemble.representation,
                issued_at=key.context.issue_time,
                qc_status=QcStatus.QC_PASSED,
            )
            PgForecastStore(conn).store_forecast(forecast)
            ref = DailyOutputReference(
                key=ForecastOutputKey(
                    station_id=member.station_id,
                    model_id=key.model_id,
                    issued_at=key.context.issue_time,
                    parameter="discharge",
                ),
                source=DailyOutputSource.FORECAST,
                output_id=forecast.id,
            )
            provenance = DailyInputProvenance(
                nwp_source=forecast.nwp_cycle_source,
                nwp_reference_time=forecast.nwp_cycle_reference_time,
                observation_staleness_hours=forecast.observation_staleness_hours,
            )
            outcomes.append(
                replace(
                    member,
                    state=DailyMemberState.PRODUCED,
                    references=(ref,),
                    provenance=provenance,
                )
            )
            evidence.append(
                DailyOutputEvidence(
                    reference=ref,
                    ensemble=ensemble,
                    qc_status=QcStatus.QC_PASSED,
                    qc_flags=(),
                    provenance=provenance,
                )
            )
    daily.finalize(
        claim,
        outcomes=tuple(outcomes),
        evidence=tuple(evidence),
        finished_at=key.context.issue_time,
    )
    # 54 scoped BAFU files, each with the existing full envelope representation.
    for station in stations[:54]:
        rows = _rows_from_fixture(
            station.code,
            issued_at=key.context.issue_time,
            produced_at=key.context.issue_time,
        )
        shift = key.context.issue_time - rows[0]["valid_time"]
        for row in rows:
            row["valid_time"] += shift
        _write_run(tmp_path, station.code, key.context.issue_time, rows)
        write_raw_evidence(tmp_path, station.code, key.context.issue_time, rows)
    # Names-only historical/unscoped files: never opened by the reader.
    history_count = 36944 - 54 if production else 0
    for index in range(history_count):
        variant = "q" if index < 27885 - 54 else "p"
        code = stations[index % 54].code if index % 2 else f"unscoped-{index}"
        stamp = (key.context.issue_time - timedelta(days=30, minutes=index)).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        (tmp_path / "parsed" / f"{code}_{variant}_forecast_{stamp}.parquet").touch()
    roster = tuple(
        SwissRosterEntry(station_id=s.id, network=s.network, code=s.code)
        for s in stations
    )
    monkeypatch.setattr(route, "load_swiss_roster", lambda _: roster)
    settings = EvaluationReadSettings(
        tenant_id=key.tenant_id,
        deployment=key.deployment,
        group_ids=(key.group_id,),
        daily_model_id=key.model_id,
        parameters=("discharge",),
    )
    config = SimpleNamespace(
        swiss_overview=SwissOverviewConfig(roster_count=134, roster_sha256="a" * 64),
        evaluation_forecasts=SimpleNamespace(to_domain=lambda: settings),
        bafu_forecast_archive_path=tmp_path,
    )
    principal = Principal(
        token_id=AccessTokenId(uuid4()),
        role=AccessTokenRole.REVIEWER,
        tenant_id=key.tenant_id,
        station_ids=frozenset(s.id for s in stations),
    )
    archive_enumerations: list[Path] = []
    original_iterdir = Path.iterdir

    def listing(path: Path) -> Iterator[Path]:
        if path == tmp_path / "parsed":
            archive_enumerations.append(path)
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", listing)
    measurements: list[float] = []
    counts: list[int] = []
    route_times: list[float] = []
    serialization_times: list[float] = []
    profile_dir = os.environ.get("SWISS_OVERVIEW_PROFILE_DIR")
    assert not (production and profile_dir), "Production samples must be unprofiled"
    iterations = 3 if profile_dir or not production else 10
    with isolated_committed_db_engine.begin() as conn:
        queries: list[str] = []

        def before_execute(
            _conn: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _many: object,
        ) -> None:
            queries.append(statement)

        sa.event.listen(conn, "before_cursor_execute", before_execute)
        stores = dict(
            station_store=PgStationStore(conn),
            obs_store=PgObservationStore(conn),
            forecast_store=PgForecastStore(conn),
            rejected_forecast_store=PgRejectedForecastStore(
                conn, transaction_factory=None
            ),
            group_store=PgStationGroupStore(conn),
        )
        request_daily = PgDailyForecastStore(lambda: nullcontext(conn))
        for iteration in range(iterations):
            profiler = cProfile.Profile() if profile_dir else None
            if profiler:
                profiler.enable()
            queries.clear()
            start = perf_counter()
            response = route.get_swiss_overview(
                principal=principal,
                stores=stores,
                config=config,
                daily=request_daily,
                now=now,
                gate=PublicationGate(),
            )
            route_end = perf_counter()
            payload = response.model_dump_json().encode()
            measurements.append(perf_counter() - start)
            end = perf_counter()
            route_times.append(route_end - start)
            serialization_times.append(end - route_end)
            counts.append(len(queries))
            if profiler:
                profiler.disable()
                profiler.dump_stats(str(Path(profile_dir) / f"stress-{iteration}.prof"))
        sa.event.remove(conn, "before_cursor_execute", before_execute)
    p95_seconds = quantiles(measurements, n=100, method="inclusive")[94]
    result: dict[str, object] = {
        "case": shape,
        "profiled": bool(profile_dir),
        "stations": 134,
        "members": None if production else 20,
        "quantiles": list(levels) if production else None,
        "time_step_seconds": step.total_seconds(),
        "horizon_steps": steps,
        "forecast_value_rows": 134 * steps * (7 if production else 20),
        "observation_rows": 670,
        "route_seconds": route_times,
        "serialization_seconds": serialization_times,
        "bafu_files": 54,
        "archive_names": 54 + history_count,
        "archive_enumerations": len(archive_enumerations),
        "raw_evidence_files": 54,
        "samples_seconds": measurements,
        "p95_seconds": p95_seconds,
        "queries": counts,
        "latency_target_met": p95_seconds < 1.0 if production else None,
        "raw_payload_target_met": len(payload) <= 393_216,
        "raw_bytes": len(payload),
        "gzip_bytes": len(gzip.compress(payload)),
    }
    output = os.environ.get("SWISS_OVERVIEW_BENCHMARK_OUTPUT")
    record_then_assert_budgets(result, Path(output) if output else None)

    assert len(archive_enumerations) == iterations
    assert response.station_count == 134
    assert all(item.forecast.state == "available" for item in response.items)
    assert all(item.forecast.peak_value is not None for item in response.items)
    if production:
        assert all(
            item.forecast.series is not None
            and item.forecast.series.state == "available"
            and len(item.forecast.series.points) == 10
            for item in response.items
        )
    else:
        assert all(
            item.forecast.series is not None
            and item.forecast.series.state == "unavailable"
            and item.forecast.series.reason == "point_limit"
            for item in response.items
        )
    assert all(
        item.observation.latest is not None
        and len(item.observation.hourly) == 3
        and all(hour.sample is not None for hour in item.observation.hourly)
        for item in response.items
    )
    assert (
        sum(item.bafu_comparison.state == "available" for item in response.items) == 54
    )
