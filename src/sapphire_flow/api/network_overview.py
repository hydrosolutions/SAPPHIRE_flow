from __future__ import annotations

from collections import Counter
from contextlib import nullcontext
from dataclasses import asdict
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

import sqlalchemy as sa
from fastapi import HTTPException

from sapphire_flow.api.network_overview_schemas import (
    DisclosedWaterLevelDatumResponse,
    ForecastGapResponse,
    ForecastPointResponse,
    NetworkOverviewResponse,
    ObservationSampleResponse,
    ObservationSlotResponse,
    OverviewForecastResponse,
    OverviewObservationResponse,
    OverviewStationResponse,
    UndisclosedWaterLevelDatumResponse,
)
from sapphire_flow.api.provisional_forecasts import ProvisionalHeader, rating_metadata
from sapphire_flow.api.schemas import ThresholdResponse
from sapphire_flow.db.metadata import (
    forecast_publication_selections,
    forecast_values,
    forecasts,
    group_model_assignments,
    model_assignments,
    station_group_members,
    station_groups,
    stations,
)
from sapphire_flow.exceptions import DailyAuthorityIntegrityError
from sapphire_flow.services.evaluation_forecasts import (
    daily_candidate_from_record,
    daily_day_available,
)
from sapphire_flow.services.network_overview import (
    compact_forecast_series,
    forecast_freshness,
)
from sapphire_flow.store.daily_forecast_store import PgDailyForecastStore
from sapphire_flow.store.forecast_publication_store import PgForecastPublicationStore
from sapphire_flow.store.forecast_store import PgForecastStore
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.types.daily_forecast import ScheduledDailyForecast
from sapphire_flow.types.daily_forecast_claim import DailyClaimKey
from sapphire_flow.types.daily_forecast_outcome import DailyOutputSource
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ForecastDataUse,
    ForecastStatus,
    ObservationSource,
    QcStatus,
)
from sapphire_flow.types.ids import ForecastId, ModelId, StationId, TenantId
from sapphire_flow.types.network_overview import (
    OverviewAvailability,
    OverviewSeries,
    ProtectedObservationVisibility,
)

if TYPE_CHECKING:
    from sapphire_flow.api.publication_gate import PublicationGate
    from sapphire_flow.api.security import Principal
    from sapphire_flow.config.deployment import DeploymentConfig
    from sapphire_flow.types.daily_forecast_outcome import DailyOutputReference
    from sapphire_flow.types.daily_forecast_snapshot import DailyAttemptSnapshot
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.forecast import OperationalForecast
    from sapphire_flow.types.network_overview import (
        OverviewObservations,
        OverviewObservationSlot,
    )

_HEADER_NAMES = (
    "id",
    "station_id",
    "model_id",
    "issued_at",
    "parameter",
    "representation",
    "status",
    "qc_status",
    "nwp_cycle_source",
    "created_at",
    "input_quality",
    "data_use",
    "model_artifact_id",
    "rating_curve_id",
    "version",
    "time_step_seconds",
)
_OutputKey = tuple[StationId, ModelId, str, ForecastDataUse]


def _slot(value: OverviewObservationSlot) -> ObservationSlotResponse:
    sample = value.sample
    return ObservationSlotResponse(
        end=value.end,
        availability=value.availability,
        sample=ObservationSampleResponse(
            timestamp=sample.timestamp,
            value=sample.value,
            source="measured"
            if sample.source is ObservationSource.MEASURED
            else "supplied",
            qc_status=sample.qc_status,
        )
        if sample is not None
        else None,
    )


def _observations(value: OverviewObservations) -> OverviewObservationResponse:
    sample = value.latest.sample
    return OverviewObservationResponse(
        parameter=value.parameter,
        unit="m" if value.parameter == "water_level" else "m³/s",
        latest=_slot(value.latest),
        latest_failed_at=value.latest_failed_at,
        newer_failed=(
            value.latest_failed_at > sample.timestamp
            if sample is not None and value.latest_failed_at is not None
            else False
            if sample is not None
            else None
        ),
        hourly=tuple(_slot(slot) for slot in value.hourly),
    )


class NetworkOverviewReader:
    def __init__(
        self,
        conn: sa.Connection,
        principal: Principal,
        config: DeploymentConfig | None,
        gate: PublicationGate,
    ) -> None:
        self.conn = conn
        self.principal = principal
        self.config = config
        self.gate = gate

    def _scope(self) -> sa.ColumnElement[bool]:
        if self.principal.is_admin:
            return sa.true()
        return sa.and_(
            stations.c.tenant_id == self.principal.tenant_id,
            stations.c.id.in_(self.principal.station_ids),
        )

    def read(
        self,
        network: str,
        purposes: tuple[ForecastDataUse, ...],
        as_of: UtcDatetime,
    ) -> NetworkOverviewResponse:
        self.conn.execute(sa.text("SET LOCAL statement_timeout = '2s'"))
        roster = (
            self.conn.execute(
                sa.select(
                    stations.c.id,
                    stations.c.tenant_id,
                    stations.c.code,
                    stations.c.name,
                    stations.c.network,
                    stations.c.measured_parameters,
                    (
                        stations.c.water_level_datum_masl
                        if self.principal.can_review
                        else sa.null()
                    ).label("water_level_datum_masl"),
                    sa.func.ST_X(stations.c.location).label("longitude"),
                    sa.func.ST_Y(stations.c.location).label("latitude"),
                )
                .where(self._scope(), stations.c.network == network)
                .order_by(stations.c.id)
                .limit(201)
            )
            .mappings()
            .all()
        )
        if not roster:
            raise HTTPException(404, "Network not found")
        if len(roster) > 200:
            raise ValueError("overview station admission exceeded")
        ids = [StationId(row["id"]) for row in roster]
        tenants = {StationId(row["id"]): TenantId(row["tenant_id"]) for row in roster}
        thresholds = PgStationStore(self.conn).fetch_thresholds_for(ids)
        assignments = self._assignments(ids)
        gated = [sid for sid in ids if self.gate.active(tenants[sid])]
        if gated and ForecastDataUse.STANDARD in purposes:
            if self.config is None:
                raise ValueError("publication config unavailable")
            PgForecastPublicationStore(
                self.conn,
                backup_max_age=timedelta(
                    hours=self.config.protected_backup_max_age_hours
                ),
                proof_window=timedelta(
                    hours=self.config.publication_proof_window_hours
                ),
            ).lock_read_snapshot()
        daily_refs, daily_keys = self._daily_references(
            ids, tenants, assignments, purposes, as_of
        )
        headers = self._headers(
            ids, assignments, gated, daily_refs, daily_keys, purposes, as_of
        )
        admitted_keys = set(headers) | set(daily_refs)
        missing = self._missing_outputs(assignments, purposes, admitted_keys)
        projected_counts = Counter(key[0] for key in admitted_keys)
        projected_counts.update(key[0] for key in missing)
        if any(count > 4 for count in projected_counts.values()):
            raise ValueError("overview output admission exceeded")
        records = self._records(headers)
        output: dict[StationId, list[OverviewForecastResponse]] = {
            sid: [] for sid in ids
        }
        for key in daily_refs:
            if key not in headers:
                output[key[0]].append(self._absence(key, "withheld_or_ineligible"))
        for key, header in headers.items():
            record = records[ForecastId(header["id"])]
            if key in daily_refs:
                summary, ref = daily_refs[key]
                assert (
                    self.config is not None
                    and self.config.evaluation_forecasts is not None
                )
                candidate = daily_candidate_from_record(
                    summary=summary,
                    ref=ref,
                    record=record,
                    now=as_of,
                    settings=self.config.evaluation_forecasts.to_domain(),
                )
                if candidate is None:
                    output[key[0]].append(self._absence(key, "withheld_or_ineligible"))
                    continue
            output[key[0]].append(
                self._forecast(
                    record, network, tenants[key[0]], as_of, header["time_step_seconds"]
                )
            )
        for sid, model, parameter, purpose in missing:
            output[sid].append(
                OverviewForecastResponse(
                    model_id=model,
                    parameter=parameter,
                    data_use=purpose,
                    availability=(
                        "withheld_or_ineligible"
                        if sid in gated and purpose is ForecastDataUse.STANDARD
                        else "no_forecast"
                    ),
                    provisional="provisional"
                    if purpose is ForecastDataUse.PROVISIONAL_RATING
                    else "standard",
                )
            )
        if any(len(items) > 4 for items in output.values()):
            raise ValueError("overview output admission exceeded")
        observations = PgObservationStore(self.conn).fetch_overview_observations(
            ids,
            as_of=as_of,
            protected_visibility=(
                ProtectedObservationVisibility.DETAIL_AUTHORIZED
                if self.principal.can_review
                else ProtectedObservationVisibility.EXCLUDE
            ),
        )
        # Recheck selected identity/currentness after all reads. Publication selection
        # is protected by the same request's share lock until serialization completes.
        self._revalidate(headers)
        items = tuple(
            OverviewStationResponse(
                station_id=str(row["id"]),
                code=row["code"],
                name=row["name"],
                network=network,
                latitude=row["latitude"],
                longitude=row["longitude"],
                measured_parameters=tuple(row["measured_parameters"]),
                water_level_datum=(
                    DisclosedWaterLevelDatumResponse(
                        value_masl=row["water_level_datum_masl"]
                    )
                    if self.principal.can_review
                    else UndisclosedWaterLevelDatumResponse()
                ),
                thresholds=tuple(
                    ThresholdResponse(
                        danger_level=threshold.danger_level,
                        parameter=threshold.parameter,
                        value=threshold.value,
                        source=threshold.source.value,
                    )
                    for threshold in thresholds[StationId(row["id"])]
                ),
                water_level=_observations(observations[row["id"], "water_level"]),
                discharge=_observations(observations[row["id"], "discharge"]),
                forecasts=tuple(
                    sorted(
                        output[row["id"]],
                        key=lambda item: (
                            item.model_id,
                            item.parameter or "",
                            item.data_use.value,
                        ),
                    )
                ),
                forecast_availability="outputs"
                if any(item.forecast_id is not None for item in output[row["id"]])
                else "no_forecast",
            )
            for row in roster
        )
        return NetworkOverviewResponse(
            generated_at=as_of,
            hourly_end=as_of.replace(minute=0, second=0, microsecond=0),
            network=network,
            station_count=len(items),
            items=items,
        )

    def _missing_outputs(
        self,
        assignments: set[tuple[StationId, ModelId]],
        purposes: tuple[ForecastDataUse, ...],
        present: set[_OutputKey],
    ) -> list[tuple[StationId, ModelId, str | None, ForecastDataUse]]:
        missing: list[tuple[StationId, ModelId, str | None, ForecastDataUse]] = []
        settings = self.config.evaluation_forecasts if self.config is not None else None
        for sid, model in assignments:
            for purpose in purposes:
                declared: tuple[str, ...] = ()
                if settings is not None and purpose is ForecastDataUse.STANDARD:
                    declared = (
                        settings.parameters
                        if model == settings.daily_model_id
                        else tuple(
                            contract.parameter
                            for contract in settings.fallback_contracts.get(model, ())
                        )
                    )
                if declared:
                    missing.extend(
                        (sid, model, parameter, purpose)
                        for parameter in declared
                        if (sid, model, parameter, purpose) not in present
                    )
                elif not any(
                    key[0] == sid and key[1] == model and key[3] is purpose
                    for key in present
                ):
                    missing.append((sid, model, None, purpose))
        return missing

    def _assignments(self, ids: list[StationId]) -> set[tuple[StationId, ModelId]]:
        direct = sa.select(
            model_assignments.c.station_id, model_assignments.c.model_id
        ).where(
            model_assignments.c.station_id.in_(ids),
            model_assignments.c.status == "active",
        )
        grouped = (
            sa.select(
                station_group_members.c.station_id, group_model_assignments.c.model_id
            )
            .join(
                group_model_assignments,
                group_model_assignments.c.group_id == station_group_members.c.group_id,
            )
            .where(
                station_group_members.c.station_id.in_(ids),
                group_model_assignments.c.status == "active",
            )
        )
        rows = self.conn.execute(direct.union(grouped).limit(801)).all()
        result = {(StationId(row[0]), ModelId(row[1])) for row in rows}
        if len(result) > 800 or any(
            count > 4 for count in Counter(sid for sid, _ in result).values()
        ):
            raise ValueError("overview assignment admission exceeded")
        return result

    def _daily_references(
        self,
        ids: list[StationId],
        tenants: dict[StationId, TenantId],
        assignments: set[tuple[StationId, ModelId]],
        purposes: tuple[ForecastDataUse, ...],
        as_of: UtcDatetime,
    ) -> tuple[
        dict[_OutputKey, tuple[DailyAttemptSnapshot, DailyOutputReference]],
        set[tuple[StationId, ModelId]],
    ]:
        config = self.config.evaluation_forecasts if self.config is not None else None
        if config is None or ForecastDataUse.STANDARD not in purposes:
            return {}, set()
        settings = config.to_domain()
        daily_keys = {
            (sid, model)
            for sid, model in assignments
            if model == settings.daily_model_id and tenants[sid] == settings.tenant_id
        }
        if not daily_keys:
            return {}, set()
        if len(settings.group_ids) > 16:
            raise ValueError("overview daily group admission exceeded")
        groups = self.conn.execute(
            sa.select(station_groups.c.id, station_groups.c.tenant_id).where(
                station_groups.c.id.in_(settings.group_ids)
            )
        ).all()
        if {row[0] for row in groups} != set(settings.group_ids) or any(
            row[1] != settings.tenant_id for row in groups
        ):
            raise DailyAuthorityIntegrityError("daily group scope disagreement")
        keys = tuple(
            DailyClaimKey(
                tenant_id=settings.tenant_id,
                deployment=settings.deployment,
                group_id=group,
                model_id=settings.daily_model_id,
                context=ScheduledDailyForecast(
                    scheduled_tick=ensure_utc(as_of - timedelta(days=day))
                ),
            )
            for day in range(7)
            for group in sorted(settings.group_ids, key=str)
        )
        days = PgDailyForecastStore(lambda: nullcontext(self.conn)).read_days_bounded(
            keys
        )
        result: dict[_OutputKey, tuple[DailyAttemptSnapshot, DailyOutputReference]] = {}
        seen_members: set[StationId] = set()
        for key in keys:
            day = days[key]
            if not daily_day_available(day, key, as_of) or day.authoritative is None:
                continue
            summary = day.authoritative
            for member in summary.outcomes or ():
                sid = member.station_id
                if (
                    sid not in ids
                    or (sid, settings.daily_model_id) not in daily_keys
                    or sid in seen_members
                ):
                    continue
                seen_members.add(sid)
                for ref in member.references:
                    output_key = (
                        sid,
                        settings.daily_model_id,
                        ref.key.parameter,
                        ForecastDataUse.STANDARD,
                    )
                    result[output_key] = (summary, ref)
        return result, daily_keys

    def _headers(
        self,
        ids: list[StationId],
        assignments: set[tuple[StationId, ModelId]],
        gated: list[StationId],
        daily_refs: dict[_OutputKey, tuple[DailyAttemptSnapshot, DailyOutputReference]],
        daily_keys: set[tuple[StationId, ModelId]],
        purposes: tuple[ForecastDataUse, ...],
        as_of: UtcDatetime,
    ) -> dict[_OutputKey, sa.RowMapping]:
        selection = forecast_publication_selections
        published = sa.exists(
            sa.select(selection.c.selected_forecast_id).where(
                selection.c.selected_forecast_id == forecasts.c.id,
                selection.c.station_id == forecasts.c.station_id,
                selection.c.parameter == forecasts.c.parameter,
                selection.c.issued_at == forecasts.c.issued_at,
                selection.c.tenant_id == stations.c.tenant_id,
            )
        )
        daily_ids = [
            ref.output_id
            for _, ref in daily_refs.values()
            if ref.source is DailyOutputSource.FORECAST
        ]
        if daily_ids:
            daily_headers = (
                self.conn.execute(
                    sa.select(*(forecasts.c[name] for name in _HEADER_NAMES))
                    .join(stations, stations.c.id == forecasts.c.station_id)
                    .where(
                        self._scope(),
                        stations.c.id.in_(ids),
                        forecasts.c.id.in_(daily_ids),
                        forecasts.c.data_use == ForecastDataUse.STANDARD.value,
                    )
                )
                .mappings()
                .all()
            )
            actual = {row["id"]: row for row in daily_headers}
            if set(actual) != set(daily_ids):
                raise DailyAuthorityIntegrityError("daily reference unavailable")
            for key, (_, ref) in daily_refs.items():
                if ref.source is DailyOutputSource.REJECTED:
                    continue
                row = actual[ref.output_id]
                if (
                    row["station_id"],
                    row["model_id"],
                    row["parameter"],
                    row["issued_at"],
                ) != (key[0], key[1], key[2], ref.key.issued_at):
                    raise DailyAuthorityIntegrityError(
                        "daily reference identity disagreement"
                    )
        query = (
            sa.select(*(forecasts.c[name] for name in _HEADER_NAMES))
            .join(stations, stations.c.id == forecasts.c.station_id)
            .where(
                self._scope(),
                stations.c.id.in_(ids),
                forecasts.c.data_use.in_([purpose.value for purpose in purposes]),
                forecasts.c.status != ForecastStatus.SUPERSEDED.value,
                forecasts.c.issued_at <= as_of,
                forecasts.c.issued_at >= as_of - timedelta(hours=168),
                forecasts.c.parameter.in_(("water_level", "discharge")),
                sa.tuple_(forecasts.c.station_id, forecasts.c.model_id).in_(
                    assignments
                ),
                sa.or_(
                    forecasts.c.data_use != ForecastDataUse.STANDARD.value,
                    ~forecasts.c.station_id.in_(gated),
                    published,
                ),
                sa.or_(
                    forecasts.c.data_use != ForecastDataUse.STANDARD.value,
                    ~sa.tuple_(forecasts.c.station_id, forecasts.c.model_id).in_(
                        daily_keys
                    ),
                    sa.and_(
                        forecasts.c.id.in_(daily_ids),
                        forecasts.c.qc_status.in_(
                            (QcStatus.QC_PASSED.value, QcStatus.QC_SUSPECT.value)
                        ),
                    ),
                ),
            )
            .distinct(
                forecasts.c.station_id,
                forecasts.c.model_id,
                forecasts.c.parameter,
                forecasts.c.data_use,
            )
            .order_by(
                forecasts.c.station_id,
                forecasts.c.model_id,
                forecasts.c.parameter,
                forecasts.c.data_use,
                forecasts.c.issued_at.desc(),
                forecasts.c.version.desc(),
                forecasts.c.id.desc(),
            )
            .limit(801)
        )
        rows = self.conn.execute(query).mappings().all()
        if len(rows) > 800 or any(
            count > 4 for count in Counter(row["station_id"] for row in rows).values()
        ):
            raise ValueError("overview output admission exceeded")
        return {
            (
                StationId(row["station_id"]),
                ModelId(row["model_id"]),
                row["parameter"],
                ForecastDataUse(row["data_use"]),
            ): row
            for row in rows
        }

    def _records(
        self, headers: dict[_OutputKey, sa.RowMapping]
    ) -> dict[ForecastId, OperationalForecast]:
        if not headers:
            return {}
        ids = [row["id"] for row in headers.values()]
        counts = (
            self.conn.execute(
                sa.select(
                    forecast_values.c.forecast_id,
                    sa.func.count().label("rows"),
                    sa.func.count(sa.distinct(forecast_values.c.valid_time)).label(
                        "times"
                    ),
                )
                .where(forecast_values.c.forecast_id.in_(ids))
                .group_by(forecast_values.c.forecast_id)
            )
            .mappings()
            .all()
        )
        if (
            len(counts) != len(ids)
            or sum(row["rows"] for row in counts) > 250000
            or any(row["times"] > 512 for row in counts)
        ):
            raise ValueError("overview value admission exceeded")
        result: dict[ForecastId, OperationalForecast] = {}
        for purpose in (ForecastDataUse.STANDARD, ForecastDataUse.PROVISIONAL_RATING):
            selected = [
                ForecastId(row["id"])
                for key, row in headers.items()
                if key[3] is purpose
            ]
            result.update(
                PgForecastStore(
                    self.conn, data_use=purpose
                ).fetch_bounded_forecasts_by_ids(selected)
            )
        if set(result) != set(ids):
            raise ValueError("selected forecast disappeared")
        for key, header in headers.items():
            record = result[ForecastId(header["id"])]
            if (
                (
                    record.station_id,
                    record.model_id,
                    record.ensemble.parameter,
                    record.data_use,
                )
                != key
                or record.issued_at != header["issued_at"]
                or record.version != header["version"]
                or record.status is ForecastStatus.SUPERSEDED
            ):
                raise ValueError("forecast identity changed")
            if key[3] is ForecastDataUse.PROVISIONAL_RATING:
                if record.input_lineage is None:
                    raise ValueError("missing provisional lineage")
                rating_metadata(
                    ProvisionalHeader.from_row(header), record.input_lineage
                )
        return result

    def _revalidate(self, headers: dict[_OutputKey, sa.RowMapping]) -> None:
        if not headers:
            return
        rows = (
            self.conn.execute(
                sa.select(*(forecasts.c[name] for name in _HEADER_NAMES)).where(
                    forecasts.c.id.in_([row["id"] for row in headers.values()])
                )
            )
            .mappings()
            .all()
        )
        expected = {row["id"]: dict(row) for row in headers.values()}
        if {row["id"]: dict(row) for row in rows} != expected:
            raise ValueError("forecast changed during projection")

    def _forecast(
        self,
        record: OperationalForecast,
        network: str,
        tenant: TenantId,
        as_of: UtcDatetime,
        time_step_seconds: float | None,
    ) -> OverviewForecastResponse:
        policy = None
        if self.config is not None:
            policy = next(
                (
                    item.to_domain()
                    for item in self.config.network_overview.freshness_policies
                    if (
                        item.tenant_id,
                        item.network,
                        item.model_id,
                        item.parameter,
                        item.data_use,
                    )
                    == (
                        tenant,
                        network,
                        record.model_id,
                        record.ensemble.parameter,
                        record.data_use.value,
                    )
                ),
                None,
            )
        freshness = forecast_freshness(
            issued_at=record.issued_at, as_of=as_of, policy=policy
        )
        series = (
            compact_forecast_series(record.ensemble, as_of=as_of)
            if time_step_seconds is not None
            else OverviewSeries(availability=OverviewAvailability.INVALID_SERIES)
        )
        return OverviewForecastResponse(
            model_id=record.model_id,
            parameter=record.ensemble.parameter,
            data_use=record.data_use,
            availability=series.availability,
            freshness=freshness.freshness,
            forecast_id=str(record.id),
            unit=record.ensemble.units,
            issued_at=record.issued_at,
            original_horizon_steps=record.ensemble.forecast_horizon_steps,
            time_step_seconds=time_step_seconds,
            expected_issue=freshness.expected_issue,
            delivery_deadline=freshness.deadline,
            qc_status=record.qc_status,
            input_quality=record.input_quality.value if record.input_quality else None,
            input_quality_codes=tuple(
                sorted(
                    {
                        flag.category.value + ":" + flag.level.value
                        for flag in record.input_quality_flags
                    }
                )
            ),
            provisional="provisional"
            if record.data_use is ForecastDataUse.PROVISIONAL_RATING
            else "standard",
            peak=ForecastPointResponse(**asdict(series.peak))
            if series.peak is not None
            else None,
            support_start=series.support_start,
            support_end=series.support_end,
            series=tuple(
                ForecastPointResponse(**asdict(point)) for point in series.points
            ),
            gaps=tuple(ForecastGapResponse(**asdict(gap)) for gap in series.gaps),
            original_point_count=series.original_point_count,
            returned_point_count=len(series.points),
            quartile_method=series.quartile_method,
            band_availability=(
                "available"
                if series.peak.p25 is not None and series.peak.p75 is not None
                else "unavailable"
            )
            if series.peak is not None
            else None,
            detail_url=f"/api/v1/forecasts/{record.id}"
            + (
                "?data_use=provisional_rating"
                if record.data_use is ForecastDataUse.PROVISIONAL_RATING
                else ""
            ),
        )

    @staticmethod
    def _absence(
        key: _OutputKey, state: Literal["withheld_or_ineligible"]
    ) -> OverviewForecastResponse:
        return OverviewForecastResponse(
            model_id=key[1],
            parameter=key[2],
            data_use=key[3],
            availability=state,
            provisional="provisional"
            if key[3] is ForecastDataUse.PROVISIONAL_RATING
            else "standard",
        )
