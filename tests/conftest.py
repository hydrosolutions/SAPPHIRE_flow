from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import polars as pl
import pytest

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import (
    GeoCoord,
)
from sapphire_flow.types.enums import (
    AlertSource,
    AlertStatus,
    EnsembleRepresentation,
    ForeignForecastStatus,
    ModelArtifactStatus,
    ObservationSource,
    QcStatus,
    RegulationType,
    SpatialRepresentation,
    StationKind,
    StationOwnership,
    StationStatus,
)
from sapphire_flow.types.ids import (
    AlertId,
    ArtifactId,
    BasinId,
    ForeignForecastId,
    HistoricalForcingId,
    ModelId,
    ObservationId,
    StationId,
)

if TYPE_CHECKING:
    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.types.alert import Alert
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.forecast import ForeignForecast
    from sapphire_flow.types.historical_forcing import (
        HistoricalForcingRecord,
        RawHistoricalForcing,
    )
    from sapphire_flow.types.model import ModelArtifactRecord
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.station import StationConfig
    from sapphire_flow.types.weather import PointForecast
    from tests.fakes.fake_clock import FakeClock

_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_RNG_SEED = 42


def _utc(year: int = 2025, month: int = 1, day: int = 1, hour: int = 0) -> UtcDatetime:
    return ensure_utc(datetime(year, month, day, hour, tzinfo=UTC))


def _uuid(rng: random.Random) -> UUID:
    return UUID(int=rng.getrandbits(128), version=4)


@pytest.fixture
def fake_clock() -> FakeClock:
    """Returns a FakeClock fixed at _EPOCH for deterministic tests."""
    from tests.fakes.fake_clock import FakeClock as _FakeClock

    return _FakeClock(_EPOCH)


def make_station_config(
    *,
    station_id: StationId | None = None,
    code: str = "TEST-001",
    name: str = "Test Station",
    lon: float = 8.5,
    lat: float = 47.4,
    station_kind: StationKind = StationKind.RIVER,
    basin_id: BasinId | None = None,
    tz: str = "Europe/Zurich",
    regulation_type: RegulationType | None = None,
    forecast_target: str | None = "discharge",
    measured_parameters: frozenset[str] | None = None,
    station_status: StationStatus = StationStatus.OPERATIONAL,
    network: str = "bafu",
    ownership: StationOwnership = StationOwnership.OWN,
    wigos_id: str | None = None,
    rng: random.Random | None = None,
) -> StationConfig:
    from sapphire_flow.types.station import StationConfig

    rng = rng or random.Random(_RNG_SEED)
    sid = station_id or StationId(_uuid(rng))
    now = _EPOCH
    return StationConfig(
        id=sid,
        code=code,
        name=name,
        location=GeoCoord(lon=lon, lat=lat),
        station_kind=station_kind,
        basin_id=basin_id,
        timezone=tz,
        regulation_type=regulation_type,
        forecast_target=forecast_target,
        measured_parameters=measured_parameters or frozenset({"discharge"}),
        station_status=station_status,
        created_at=now,
        updated_at=now,
        network=network,
        ownership=ownership,
        wigos_id=wigos_id,
    )


def make_observation(
    *,
    station_id: StationId | None = None,
    parameter: str = "discharge",
    value: float | None = None,
    timestamp: UtcDatetime | None = None,
    qc_status: QcStatus = QcStatus.QC_PASSED,
    rng: random.Random | None = None,
) -> Observation:
    from sapphire_flow.types.observation import Observation

    rng = rng or random.Random(_RNG_SEED)
    sid = station_id or StationId(_uuid(rng))
    if value is None:
        resolved_value: float | None = (
            None if qc_status == QcStatus.MISSING else rng.uniform(0.5, 100.0)
        )
    else:
        resolved_value = value
    return Observation(
        id=ObservationId(_uuid(rng)),
        station_id=sid,
        timestamp=timestamp or _EPOCH,
        parameter=parameter,
        value=resolved_value,
        source=ObservationSource.MEASURED,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=qc_status,
        qc_flags=[],
        qc_rule_version=None,
        created_at=_EPOCH,
    )


def make_observations(
    n: int = 10,
    *,
    station_id: StationId | None = None,
    parameter: str = "discharge",
    start: UtcDatetime | None = None,
    interval: timedelta = timedelta(hours=1),
    rng: random.Random | None = None,
) -> list[Observation]:
    rng = rng or random.Random(_RNG_SEED)
    sid = station_id or StationId(_uuid(rng))
    t = start or _EPOCH
    result = []
    for _ in range(n):
        result.append(
            make_observation(station_id=sid, parameter=parameter, timestamp=t, rng=rng)
        )
        t = ensure_utc(
            datetime.fromtimestamp(t.timestamp() + interval.total_seconds(), tz=UTC)
        )
    return result


def make_nwp_forecast(
    station_ids: list[StationId] | None = None,
    *,
    n_members: int = 3,
    n_steps: int = 5,
    cycle_time: UtcDatetime | None = None,
    rng: random.Random | None = None,
) -> dict[StationId, PointForecast]:
    from sapphire_flow.types.weather import PointForecast

    rng = rng or random.Random(_RNG_SEED)
    sids = station_ids or [StationId(_uuid(rng))]
    ct = cycle_time or _EPOCH
    result = {}
    for sid in sids:
        rows = []
        for step in range(n_steps):
            vt = ensure_utc(
                datetime.fromtimestamp(ct.timestamp() + (step + 1) * 3600, tz=UTC)
            )
            for m in range(n_members):
                rows.append(
                    {
                        "valid_time": vt,
                        "parameter": "precipitation",
                        "member_id": m,
                        "value": rng.uniform(0, 10),
                    }
                )
        df = pl.DataFrame(rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC"))
        )
        result[sid] = PointForecast(nwp_source="icon_ch2_eps", cycle_time=ct, values=df)
    return result


def make_forecast_ensemble(
    *,
    station_id: StationId | None = None,
    representation: EnsembleRepresentation = EnsembleRepresentation.MEMBERS,
    n_members: int = 21,
    n_steps: int = 120,
    parameter: str = "discharge",
    rng: random.Random | None = None,
) -> ForecastEnsemble:
    from sapphire_flow.types.ensemble import ForecastEnsemble

    rng = rng or random.Random(_RNG_SEED)
    sid = station_id or StationId(_uuid(rng))
    issued = _EPOCH
    time_step = timedelta(hours=1)

    if representation == EnsembleRepresentation.MEMBERS:
        rows = []
        for step in range(n_steps):
            vt = ensure_utc(
                datetime.fromtimestamp(issued.timestamp() + (step + 1) * 3600, tz=UTC)
            )
            for m in range(n_members):
                rows.append(
                    {"valid_time": vt, "member_id": m, "value": rng.uniform(1.0, 50.0)}
                )
        df = pl.DataFrame(rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
            pl.col("member_id").cast(pl.Int32),
        )
        return ForecastEnsemble.from_members(
            station_id=sid,
            issued_at=issued,
            parameter=parameter,
            units="m3/s",
            time_step=time_step,
            values=df,
        )
    else:
        quantile_levels = [0.02, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.98]
        rows = []
        for step in range(n_steps):
            vt = ensure_utc(
                datetime.fromtimestamp(issued.timestamp() + (step + 1) * 3600, tz=UTC)
            )
            for q in quantile_levels:
                rows.append(
                    {"valid_time": vt, "quantile": q, "value": rng.uniform(1.0, 50.0)}
                )
        df = pl.DataFrame(rows).with_columns(
            pl.col("valid_time").cast(pl.Datetime("us", "UTC"))
        )
        return ForecastEnsemble.from_quantiles(
            station_id=sid,
            issued_at=issued,
            parameter=parameter,
            units="m3/s",
            time_step=time_step,
            values=df,
        )


def make_deployment_config(**overrides: object) -> DeploymentConfig:
    from sapphire_flow.config.deployment import DeploymentConfig

    defaults = {"max_retention_days": 3650}
    defaults.update(overrides)
    return DeploymentConfig(**defaults)


def make_alert(
    *,
    station_id: StationId | None = None,
    source: AlertSource = AlertSource.FORECAST,
    alert_level: str = "Moderate",
    status: AlertStatus = AlertStatus.RAISED,
    rng: random.Random | None = None,
) -> Alert:
    from sapphire_flow.types.alert import Alert

    rng = rng or random.Random(_RNG_SEED)
    sid = station_id or StationId(_uuid(rng))
    return Alert(
        id=AlertId(_uuid(rng)),
        station_id=sid,
        source=source,
        alert_level=alert_level,
        status=status,
        trigger_probability=0.6,
        trigger_value=150.0,
        triggered_at=_EPOCH,
        acknowledged_at=None,
        acknowledged_by=None,
        resolved_at=None,
        first_detected_at=None,
        notified_at=None,
        created_at=_EPOCH,
    )


def make_foreign_forecast(
    *,
    station_id: StationId | None = None,
    upstream_instance_url: str = "https://sapphire.example.gov",
    representation: EnsembleRepresentation = EnsembleRepresentation.MEMBERS,
    n_members: int = 21,
    n_steps: int = 120,
    rng: random.Random | None = None,
) -> ForeignForecast:
    from sapphire_flow.types.forecast import ForeignForecast

    rng = rng or random.Random(_RNG_SEED)
    sid = station_id or StationId(_uuid(rng))
    ensemble = make_forecast_ensemble(
        station_id=sid,
        representation=representation,
        n_members=n_members,
        n_steps=n_steps,
        rng=rng,
    )
    return ForeignForecast(
        id=ForeignForecastId(_uuid(rng)),
        station_id=sid,
        upstream_instance_url=upstream_instance_url,
        upstream_station_id=str(_uuid(rng)),
        upstream_forecast_id=str(_uuid(rng)),
        issued_at=_EPOCH,
        valid_from=_EPOCH,
        valid_to=_utc(2025, 1, 6),
        representation=representation,
        status=ForeignForecastStatus.PUBLISHED,
        ensemble=ensemble,
        fetched_at=_EPOCH,
        created_at=_EPOCH,
    )


def make_model_artifact_record(
    *,
    model_id: ModelId | None = None,
    station_id: StationId | None = None,
    status: ModelArtifactStatus = ModelArtifactStatus.ACTIVE,
    rng: random.Random | None = None,
) -> ModelArtifactRecord:
    from sapphire_flow.types.model import ModelArtifactRecord

    rng = rng or random.Random(_RNG_SEED)
    return ModelArtifactRecord(
        id=ArtifactId(_uuid(rng)),
        model_id=model_id or ModelId("test_model"),
        station_id=station_id or StationId(_uuid(rng)),
        group_id=None,
        status=status,
        artifact_path="artifacts/test.bin",
        training_period_start=_utc(2020, 1, 1),
        training_period_end=_utc(2024, 12, 31),
        trained_at=_EPOCH,
        promoted_at=_EPOCH if status == ModelArtifactStatus.ACTIVE else None,
        promoted_by=None,
        superseded_at=None,
        created_at=_EPOCH,
    )


def make_historical_forcing_record(
    *,
    station_id: StationId | None = None,
    source: str = "camels-ch",
    version: str = "1.0",
    valid_time: datetime | None = None,
    parameter: str = "precipitation",
    spatial_type: SpatialRepresentation = SpatialRepresentation.BASIN_AVERAGE,
    band_id: int | None = None,
    member_id: int | None = None,
    value: float = 5.0,
    rng: random.Random | None = None,
) -> HistoricalForcingRecord:
    from sapphire_flow.types.historical_forcing import HistoricalForcingRecord

    rng = rng or random.Random(_RNG_SEED)
    return HistoricalForcingRecord(
        id=HistoricalForcingId(_uuid(rng)),
        station_id=station_id or StationId(_uuid(rng)),
        source=source,
        version=version,
        valid_time=ensure_utc(valid_time or datetime(2026, 1, 15, 12, 0, tzinfo=UTC)),
        parameter=parameter,
        spatial_type=spatial_type,
        band_id=band_id,
        member_id=member_id,
        value=value,
        created_at=_EPOCH,
    )


def make_raw_historical_forcing(
    *,
    station_id: StationId | None = None,
    source: str = "camels-ch",
    version: str = "1.0",
    valid_time: datetime | None = None,
    parameter: str = "precipitation",
    spatial_type: SpatialRepresentation = SpatialRepresentation.BASIN_AVERAGE,
    band_id: int | None = None,
    member_id: int | None = None,
    value: float = 5.0,
    rng: random.Random | None = None,
) -> RawHistoricalForcing:
    from sapphire_flow.types.historical_forcing import RawHistoricalForcing

    rng = rng or random.Random(_RNG_SEED)
    return RawHistoricalForcing(
        station_id=station_id or StationId(_uuid(rng)),
        source=source,
        version=version,
        valid_time=ensure_utc(valid_time or datetime(2026, 1, 15, 12, 0, tzinfo=UTC)),
        parameter=parameter,
        spatial_type=spatial_type,
        band_id=band_id,
        member_id=member_id,
        value=value,
    )
