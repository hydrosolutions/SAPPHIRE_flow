from __future__ import annotations

import os
import pathlib
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import polars as pl
import pytest
import structlog

# Plan 147 Slice C: the API fails closed at startup without a readable
# access_token_pepper (R1). Tests never talk to Docker secrets, so provide
# the env-var fallback process-wide for any TestClient(app) instantiation.
os.environ.setdefault("ACCESS_TOKEN_PEPPER", "test-only-pepper-do-not-use-in-prod")

# Per-checkout Prefect home. Unset, every worktree and every parallel session
# shares ~/.prefect and its single SQLite database — measured at 17 GB with a
# 147 MB write-ahead log on 2026-09-02, with 23 live Prefect processes. Any
# unscoped `pytest` touching a Prefect flow then queues on that lock, which
# presents as a hang rather than a failure: two `/implement` runs died as
# "agent stalled, no progress for 180000ms" after ~4h each, and a 64-hour
# station-onboarding flow on the mac-mini was "cancelled by the runtime
# environment".
#
# `setdefault`, so an explicit PREFECT_HOME still wins. The containers already
# scope this (`PREFECT_HOME: /tmp/prefect`, docker-compose.yml); CI is safe
# because its runners start clean. Local development was the remaining gap.
os.environ.setdefault(
    "PREFECT_HOME", str(pathlib.Path(__file__).resolve().parent.parent / ".prefect")
)

from sapphire_flow.logging import configure_test_logging
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import (
    GeoCoord,
)
from sapphire_flow.types.enums import (
    AlertSource,
    AlertStatus,
    EnsembleRepresentation,
    ForeignForecastStatus,
    GaugingStatus,
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
    StationGroupId,
    StationId,
    TenantId,
)
from sapphire_flow.types.tenant import DEFAULT_TENANT_ID

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.types.alert import Alert
    from sapphire_flow.types.ensemble import ForecastEnsemble
    from sapphire_flow.types.forecast import ForeignForecast
    from sapphire_flow.types.historical_forcing import (
        HistoricalForcingRecord,
        RawHistoricalForcing,
    )
    from sapphire_flow.types.model import ModelArtifactRecord
    from sapphire_flow.types.model_onboarding import (
        CompatibilityReport,
        OnboardingUnitResult,
        SkillGateResult,
    )
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.station import StationConfig
    from sapphire_flow.types.training import TrainingUnit
    from sapphire_flow.types.weather import PointForecast
    from tests.fakes.fake_clock import FakeClock

_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_RNG_SEED = 42


def _utc(year: int = 2025, month: int = 1, day: int = 1, hour: int = 0) -> UtcDatetime:
    return ensure_utc(datetime(year, month, day, hour, tzinfo=UTC))


def _uuid(rng: random.Random) -> UUID:
    return UUID(int=rng.getrandbits(128), version=4)


@pytest.fixture(autouse=True)
def _reset_structlog_config_before_each_test() -> Iterator[None]:
    """Plan 201: force known structlog global config before AND during every test.

    Any test that exercises a real entry point (``main()`` in a CLI module,
    a flow, ...) calls one of ``configure_cli_logging`` /
    ``configure_api_logging`` / ``configure_prefect_logging``, which mutates
    structlog's PROCESS-GLOBAL config via ``structlog.configure(...,
    cache_logger_on_first_use=True)``. That flag is not just a config value:
    per ``structlog._config.BoundLoggerLazyProxy.bind``, the FIRST time a
    module-level ``log = structlog.get_logger(__name__)`` proxy is bound
    while the flag reads True, the proxy permanently monkeypatches its own
    ``.bind`` to a closure over the processors active at that moment. No
    later ``structlog.configure()`` call — including the one
    ``structlog.testing.capture_logs()`` performs internally — can undo
    that: it is per-proxy state, not global state, so re-configuring
    afterwards has no effect on a logger that already cached itself.

    Concretely (Plan 201 T1's minimal reproducer): a CLI test calls
    ``configure_cli_logging()`` (cache=True); a later, unrelated test is the
    first to log through ``sapphire_flow.services.skill.combined_skill``'s
    module logger while that flag is still True, permanently caching it;
    a still-later test's ``capture_logs()`` then silently observes `[]`
    because the cached logger never routes through the capturing
    processors. Resetting to the test-safe config (cache=False) before
    EVERY test closes the *inter-test* window: no logger can ever be
    first-bound while a flag leaked from a PRIOR test's body is live.

    That reset-at-setup alone leaves an *intra-test* window open: nothing
    stops a test's own body from calling a production configurator
    (cache=True) and then first-binding some logger before that same test
    (or the one right after) reads it back via ``capture_logs()`` — the
    cache takes effect on the very next bind, not at the next fixture
    setup. Closing that requires forcing the flag for the whole test, not
    just at its start: every ``structlog.configure()`` call made anywhere
    during the test — including ones production code issues via
    ``configure_cli_logging()`` and friends — has
    ``cache_logger_on_first_use`` forced to False here, restored
    automatically by ``monkeypatch`` at teardown. Both call sites
    (``src/sapphire_flow/logging.py``) do ``import structlog`` then call
    ``structlog.configure(...)``, an attribute lookup at call time, so
    patching the ``structlog.configure`` name catches them regardless of
    which module made the call. ``structlog.testing.capture_logs()`` is
    unaffected: it holds its own ``from structlog import configure``
    binding captured at ``structlog.testing`` import time, a different
    name pointing at the same underlying function — and it never passes
    ``cache_logger_on_first_use`` anyway, only ``processors``.
    """
    configure_test_logging()

    real_configure = structlog.configure

    def _test_safe_configure(*args: object, **kwargs: object) -> None:
        kwargs["cache_logger_on_first_use"] = False
        real_configure(*args, **kwargs)

    # A DEDICATED MonkeyPatch context, not the shared ``monkeypatch`` fixture.
    # Plan 201 review (major): the shared instance is undone wholesale by any
    # test that calls ``monkeypatch.undo()`` — and one does, at
    # ``tests/unit/ops/test_watchdog.py:5031``. That would silently restore the
    # real ``structlog.configure`` mid-test and re-open the very window this
    # guard exists to close, with no failure to show for it. An owned context
    # is invisible to that call and is still unwound here at teardown.
    with pytest.MonkeyPatch.context() as guard:
        guard.setattr(structlog, "configure", _test_safe_configure)
        yield


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
    forecast_targets: frozenset[str] | None = frozenset({"discharge"}),
    measured_parameters: frozenset[str] | None = None,
    station_status: StationStatus = StationStatus.OPERATIONAL,
    network: str = "bafu",
    ownership: StationOwnership = StationOwnership.OWN,
    wigos_id: str | None = None,
    gauging_status: GaugingStatus = GaugingStatus.GAUGED,
    water_level_datum_masl: float | None = None,
    water_level_unit: str | None = None,
    tenant_id: TenantId = DEFAULT_TENANT_ID,
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
        forecast_targets=forecast_targets,
        measured_parameters=measured_parameters or frozenset({"discharge"}),
        station_status=station_status,
        created_at=now,
        updated_at=now,
        network=network,
        ownership=ownership,
        wigos_id=wigos_id,
        gauging_status=gauging_status,
        water_level_datum_masl=water_level_datum_masl,
        water_level_unit=water_level_unit,
        tenant_id=tenant_id,
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
    units: str = "m³/s",
    rng: random.Random | None = None,
    model_id: ModelId | None = None,
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
            units=units,
            time_step=time_step,
            values=df,
            model_id=model_id,
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
            units=units,
            time_step=time_step,
            values=df,
            model_id=model_id,
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
        model_ids=(),
        alert_model_strategy=None,
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
        sha256_hash="",
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


def make_training_unit(
    *,
    model_id: ModelId | None = None,
    station_id: StationId | None = None,
    group_id: StationGroupId | None = None,
    rng: random.Random | None = None,
) -> TrainingUnit:
    from sapphire_flow.types.training import TrainingUnit

    rng = rng or random.Random(_RNG_SEED)
    mid = model_id or ModelId("test_model")
    if group_id is not None:
        sid = None
        station_ids: frozenset[StationId] = frozenset({StationId(_uuid(rng))})
    else:
        sid = station_id or StationId(_uuid(rng))
        station_ids = frozenset({sid})
    return TrainingUnit(
        model_id=mid,
        station_id=sid,
        group_id=group_id,
        station_ids=station_ids,
        training_period_start=_EPOCH,
        training_period_end=_EPOCH,
        time_step=timedelta(days=1),
    )


def make_compatibility_report(
    *,
    model_id: ModelId | None = None,
    station_id: StationId | None = None,
    rng: random.Random | None = None,
    **overrides: object,
) -> CompatibilityReport:
    from sapphire_flow.types.model_onboarding import CompatibilityReport

    rng = rng or random.Random(_RNG_SEED)
    defaults: dict[str, object] = {
        "model_id": model_id or ModelId("test_model"),
        "station_id": station_id or StationId(_uuid(rng)),
        "group_id": None,
        "protocol_conforms": True,
        "missing_target_parameters": frozenset(),
        "missing_past_dynamic": frozenset(),
        "missing_future_dynamic": frozenset(),
        "missing_static_features": frozenset(),
        "time_step_compatible": True,
    }
    defaults.update(overrides)
    return CompatibilityReport(**defaults)  # type: ignore[arg-type]


def make_skill_gate_result(
    *,
    rng: random.Random | None = None,
    **overrides: object,
) -> SkillGateResult:
    from sapphire_flow.types.model_onboarding import SkillGateResult

    rng = rng or random.Random(_RNG_SEED)
    defaults: dict[str, object] = {
        "model_artifact_id": ArtifactId(_uuid(rng)),
        "metric_scores": (("nse", 0.85), ("kge", 0.80)),
        "thresholds": (("nse", 0.5, True), ("kge", 0.5, True)),
        "failing_metrics": frozenset(),
    }
    defaults.update(overrides)
    return SkillGateResult(**defaults)  # type: ignore[arg-type]


def make_onboarding_unit_result(
    *,
    model_id: ModelId | None = None,
    station_id: StationId | None = None,
    rng: random.Random | None = None,
    **overrides: object,
) -> OnboardingUnitResult:
    from sapphire_flow.types.enums import OnboardingOutcome
    from sapphire_flow.types.model_onboarding import OnboardingUnitResult

    rng = rng or random.Random(_RNG_SEED)
    unit = make_training_unit(model_id=model_id, station_id=station_id, rng=rng)
    compat = make_compatibility_report(
        model_id=unit.model_id, station_id=unit.station_id, rng=rng
    )
    gate = make_skill_gate_result(rng=rng)
    artifact_id = ArtifactId(_uuid(rng))
    defaults: dict[str, object] = {
        "unit": unit,
        "outcome": OnboardingOutcome.PROMOTED,
        "compatibility": compat,
        "artifact_id": artifact_id,
        "hindcast_steps": (),
        "skill_gate": gate,
        "error": None,
    }
    defaults.update(overrides)
    return OnboardingUnitResult(**defaults)  # type: ignore[arg-type]


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
