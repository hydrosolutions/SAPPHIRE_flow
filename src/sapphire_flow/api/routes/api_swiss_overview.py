# ruff: noqa: TC001, TC003 -- FastAPI resolves annotations at runtime.
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from sapphire_flow.api.deps import (
    get_daily_evaluation_reader,
    get_deployment_config,
    get_evaluation_clock,
    get_stores,
)
from sapphire_flow.api.publication_gate import PublicationGate, get_publication_gate
from sapphire_flow.api.security import Principal, require_reviewer
from sapphire_flow.api.swiss_overview_schemas import (
    BafuAvailable,
    BafuHeadline,
    BafuWithheld,
    CmalAvailable,
    CmalHeadline,
    CmalWithheld,
    HeadlineUnavailable,
    OverviewHour,
    OverviewInputFlag,
    OverviewObservation,
    OverviewSample,
    OverviewStation,
    SwissOverviewItem,
    SwissOverviewResponse,
)
from sapphire_flow.config.deployment import DeploymentConfig
from sapphire_flow.config.swiss_overview import load_swiss_roster
from sapphire_flow.exceptions import (
    DailyAuthorityIntegrityError,
    StoredForecastDecodeError,
)
from sapphire_flow.services.evaluation_forecasts import (
    DailyEvaluationReader,
    EvaluationStores,
)
from sapphire_flow.services.forecast_lab.bafu_archive import BafuArchiveRun
from sapphire_flow.services.forecast_read import ForecastReadUnavailableError
from sapphire_flow.services.swiss_overview import (
    CurrentHeadline,
    candidate_has_median,
    candidate_peak,
    read_bafu_headlines,
    read_current_headlines,
)
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.dhm_delivery import DELIVERY_ID
from sapphire_flow.types.enums import AccessTokenRole, ObservationSource, QcStatus
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.observation import Observation
from sapphire_flow.types.observation_read import ObservationWindowStart
from sapphire_flow.types.swiss_overview import HeadlineAbsence, MedianPeak

router = APIRouter(prefix="/api/v1", tags=["api-swiss-overview"])


def _sample(observation: Observation | None) -> OverviewSample | None:
    if observation is None or observation.value is None:
        return None
    return OverviewSample(observed_at=observation.timestamp, value=observation.value)


def _cmal(
    headline: CurrentHeadline, *, as_of: UtcDatetime, withheld: bool
) -> CmalHeadline:
    candidate = headline.candidate
    if candidate is None:
        return HeadlineUnavailable(
            reason=headline.absence or HeadlineAbsence.NO_CURRENT_CANDIDATE
        )
    row = candidate.record
    identity = CmalWithheld(
        output_id=str(row.id),
        output_source=candidate.reference.source.value,
        qc_status=row.qc_status.value,
        model_id=str(row.model_id),
        issued_at=row.issued_at,
    )
    if withheld:
        return identity
    if not candidate_has_median(candidate):
        return HeadlineUnavailable(reason=HeadlineAbsence.INVALID_SOURCE_DATA)
    peak = candidate_peak(candidate, as_of=as_of)
    if peak is None:
        return HeadlineUnavailable(reason=HeadlineAbsence.EXPIRED_HORIZON)
    units = row.ensemble.units if isinstance(row, OperationalForecast) else row.units
    if units not in {"m3/s", "m³/s"}:
        return HeadlineUnavailable(reason=HeadlineAbsence.INVALID_SOURCE_DATA)
    return CmalAvailable(
        output_id=identity.output_id,
        output_source=identity.output_source,
        qc_status=identity.qc_status,
        model_id=identity.model_id,
        issued_at=row.issued_at,
        horizon_start=peak.horizon_start,
        horizon_end=peak.horizon_end,
        peak_value=peak.value,
        peak_valid_time=peak.valid_time,
        input_quality=row.input_quality.value
        if isinstance(row, OperationalForecast) and row.input_quality
        else None,
        input_quality_flags=[
            OverviewInputFlag(
                category=f.category.value, level=f.level.value, detail=f.detail
            )
            for f in row.input_quality_flags
        ]
        if isinstance(row, OperationalForecast)
        else [],
    )


@router.get("/swiss/overview", response_model=SwissOverviewResponse)
def get_swiss_overview(
    principal: Principal = Depends(require_reviewer),
    stores: dict[str, Any] = Depends(get_stores),
    config: DeploymentConfig | None = Depends(get_deployment_config),
    daily: DailyEvaluationReader = Depends(get_daily_evaluation_reader),
    now: datetime = Depends(get_evaluation_clock),
    gate: PublicationGate = Depends(get_publication_gate),
) -> SwissOverviewResponse:
    as_of = ensure_utc(now)
    if (
        config is None
        or config.swiss_overview is None
        or config.evaluation_forecasts is None
    ):
        raise HTTPException(503, "Swiss overview is unavailable")
    settings = config.evaluation_forecasts.to_domain()
    try:
        roster = load_swiss_roster(config.swiss_overview)
        stations = {s.id: s for s in stores["station_store"].fetch_all_stations()}
        if any(
            (station := stations.get(entry.station_id)) is None
            or station.tenant_id != settings.tenant_id
            or station.network != entry.network
            or station.code != entry.code
            for entry in roster
        ):
            raise ValueError("Swiss roster metadata mismatch")
    except (OSError, ValueError):
        raise HTTPException(503, "Swiss overview is unavailable") from None
    scoped = tuple(
        entry
        for entry in roster
        if principal.is_admin
        or (
            principal.tenant_id == settings.tenant_id
            and entry.station_id in principal.station_ids
        )
    )
    ids = [entry.station_id for entry in scoped]
    obs = stores["obs_store"]
    latest = obs.fetch_latest_observations(
        ids,
        "discharge",
        ensure_utc(as_of - timedelta(hours=168)),
        as_of,
        source=ObservationSource.MEASURED,
        excluded_delivery_id=DELIVERY_ID,
        qc_status=QcStatus.QC_PASSED,
    )
    hour = as_of.replace(minute=0, second=0, microsecond=0)
    ends = [ensure_utc(hour - timedelta(hours=i)) for i in (2, 1, 0)]
    hourly = [
        obs.fetch_latest_observations(
            ids,
            "discharge",
            ensure_utc(end - timedelta(hours=1)),
            end,
            source=ObservationSource.MEASURED,
            excluded_delivery_id=DELIVERY_ID,
            qc_status=QcStatus.QC_PASSED,
            window_start_bound=ObservationWindowStart.EXCLUSIVE,
        )
        for end in ends
    ]
    try:
        current = read_current_headlines(
            station_ids=ids,
            settings=settings,
            stores=EvaluationStores(
                daily=daily,
                forecasts=stores["forecast_store"],
                rejected=stores["rejected_forecast_store"],
                groups=stores["group_store"],
            ),
            as_of=as_of,
        )
    except (DailyAuthorityIntegrityError, StoredForecastDecodeError):
        current = dict.fromkeys(
            ids,
            CurrentHeadline(
                candidate=None, absence=HeadlineAbsence.AUTHORITY_INTEGRITY_ERROR
            ),
        )
    except ForecastReadUnavailableError:
        raise HTTPException(503, "Swiss overview is unavailable") from None
    gated = gate.active(settings.tenant_id)
    bafu: Mapping[str, tuple[BafuArchiveRun, MedianPeak] | HeadlineAbsence] = (
        {}
        if gated
        else read_bafu_headlines(
            config.bafu_forecast_archive_path, [s.code for s in scoped], as_of=as_of
        )
    )
    items: list[SwissOverviewItem] = []
    for station in scoped:
        comparison: BafuHeadline
        if gated:
            comparison = BafuWithheld()
        elif isinstance(run := bafu[station.code], HeadlineAbsence):
            comparison = HeadlineUnavailable(reason=run)
        else:
            archive, peak = run
            comparison = BafuAvailable(
                issued_at=archive.issued_at,
                horizon_start=peak.horizon_start,
                horizon_end=peak.horizon_end,
                peak_value=peak.value,
                peak_valid_time=peak.valid_time,
            )
        sample = _sample(latest[station.station_id])
        items.append(
            SwissOverviewItem(
                station=OverviewStation(
                    id=str(station.station_id),
                    network=station.network,
                    code=station.code,
                ),
                observation=OverviewObservation(
                    availability="available" if sample else "unavailable",
                    reason=None if sample else "no_eligible_observation",
                    latest=sample,
                    hourly=[
                        OverviewHour(
                            hour_end=end, sample=_sample(values[station.station_id])
                        )
                        for end, values in zip(ends, hourly, strict=True)
                    ],
                ),
                forecast=_cmal(
                    current[station.station_id],
                    as_of=as_of,
                    withheld=gated and principal.role is AccessTokenRole.REVIEWER,
                ),
                bafu_comparison=comparison,
            )
        )
    return SwissOverviewResponse(
        generated_at=as_of, station_count=len(items), items=items
    )
