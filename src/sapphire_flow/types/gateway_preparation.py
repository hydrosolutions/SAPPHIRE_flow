"""Immutable identities for approved-handover Gateway preparation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path  # noqa: TC003 -- state boundary serialization
from typing import Literal

from sapphire_flow.basin_operator.inventory import StationInput  # noqa: TC001
from sapphire_flow.types.weather import GatewayHruName


def parse_gateway_hru_name(value: str) -> GatewayHruName:
    if re.fullmatch(r"[a-z_][a-z0-9_]*", value) is None:
        raise ValueError("new Gateway HRU must be a lowercase identifier")
    return GatewayHruName(value)


class RegistrationStatus(StrEnum):
    NOT_SENT = "not_sent"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    CONFIRMED = "confirmed"
    NOT_CREATED = "not_created"


@dataclass(frozen=True, kw_only=True, slots=True)
class ApprovedFeature:
    network: str
    station: StationInput
    polygon_name: str
    gauge_id: str
    proposed_hru: str


@dataclass(frozen=True, kw_only=True, slots=True)
class ApprovedHandover:
    directory: Path
    package_id: str
    source_fingerprint: str
    derived_fingerprint: str
    checksums: dict[str, str]
    features: tuple[ApprovedFeature, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class RegistrationAttempt:
    attempt_id: str
    recorded_at: str
    requested_hru: str
    upload_sha256: str
    status: RegistrationStatus
    actor: str = ""
    evidence_reference: str = ""
    message: str = ""
    detail: str = ""
    warnings: str = ""
    failure_reason: str = ""

    def __post_init__(self) -> None:
        if not self.attempt_id or not self.recorded_at:
            raise ValueError("attempt identity and timestamp required")
        if self.status in (
            RegistrationStatus.CONFIRMED,
            RegistrationStatus.NOT_CREATED,
        ) and (not self.actor.strip() or not self.evidence_reference.strip()):
            raise ValueError("attributed reconciliation evidence required")


class DataStream(StrEnum):
    ERA5_HISTORY = "era5_history"
    IFS_FORECAST = "ifs_forecast"
    SNOW_HISTORY = "snow_history"
    SNOW_FORECAST = "snow_forecast"


@dataclass(frozen=True, kw_only=True, slots=True)
class HistoryScope:
    start: datetime
    end: datetime
    purpose: str

    def __post_init__(self) -> None:
        if any(
            t.tzinfo is None or t.utcoffset() != timedelta(0)
            for t in (self.start, self.end)
        ):
            raise ValueError("history scope must be UTC")
        if self.start >= self.end or not self.purpose.strip():
            raise ValueError("explicit nonempty history interval and purpose required")

    @property
    def job_years(self) -> tuple[int, int]:
        return self.start.year, (self.end - timedelta(microseconds=1)).year


@dataclass(frozen=True, kw_only=True, slots=True)
class RequestedVariable:
    canonical: str
    stream: DataStream
    parameter: str
    source_unit: str


class SourceRunSemantics(StrEnum):
    UNRESOLVED = "unresolved"
    EXACT = "exact"
    NULL = "null"
    ABSENT = "absent"
    UTC_INSTANT = "utc_instant"
    UTC_DATE_MIDNIGHT = "utc_date_midnight"


@dataclass(frozen=True, kw_only=True, slots=True)
class SourceRunContract:
    semantics: SourceRunSemantics = SourceRunSemantics.UNRESOLVED
    value: str | None = None
    timestamp: datetime | None = None

    def __post_init__(self) -> None:
        if self.semantics == SourceRunSemantics.EXACT:
            if not isinstance(self.value, str) or not self.value.strip():
                raise ValueError("exact source_run requires a nonempty expected value")
        elif self.value is not None:
            raise ValueError("only exact source_run semantics accept a value")
        if self.semantics == SourceRunSemantics.UTC_INSTANT:
            if self.timestamp is None or self.timestamp.utcoffset() is None:
                raise ValueError("fixed run identity requires an aware datetime")
            object.__setattr__(self, "timestamp", self.timestamp.astimezone(UTC))
        elif self.timestamp is not None:
            raise ValueError("only fixed run identities accept a timestamp")


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastScope:
    cycles: tuple[datetime, ...]
    first_lead: timedelta
    end_lead: timedelta
    members: tuple[int, ...]
    level_type: Literal["sfc", "pl", "ml"]
    level: int | None
    control_grid: Literal["0p1"]
    product_grid_reference: str
    source_runs: tuple[tuple[str, SourceRunContract], ...] = ()

    def __post_init__(self) -> None:
        if not self.cycles or len(set(self.cycles)) != len(self.cycles):
            raise ValueError("distinct explicit forecast cycles required")
        if any(
            t.tzinfo is None
            or t.utcoffset() != timedelta(0)
            or t.hour not in (0, 6, 12, 18)
            or t.minute
            or t.second
            or t.microsecond
            for t in self.cycles
        ):
            raise ValueError("forecast cycles must be UTC synoptic hours")
        if self.first_lead < timedelta(0) or self.first_lead >= self.end_lead:
            raise ValueError("nonempty forecast lead interval required")
        runs = dict(self.source_runs)
        if len(runs) != len(self.source_runs) or not set(runs) <= {
            c.isoformat() for c in self.cycles
        }:
            raise ValueError("run contracts must identify distinct requested cycles")
        for cycle in self.cycles:
            run = runs.get(cycle.isoformat())
            if run is None:
                continue
            if run.semantics not in (
                SourceRunSemantics.EXACT,
                SourceRunSemantics.UTC_INSTANT,
                SourceRunSemantics.UNRESOLVED,
            ):
                raise ValueError("forecast runs require fixed identities")
            if (
                run.semantics == SourceRunSemantics.UTC_INSTANT
                and run.timestamp != cycle
            ):
                raise ValueError("forecast run timestamp must match its cycle")
        if self.members != (0,) or type(self.members[0]) is not int:
            raise ValueError(
                "Gateway preparation currently supports control member 0 only"
            )


class DateEndConvention(StrEnum):
    INCLUSIVE = "inclusive"
    EXCLUSIVE = "exclusive"


@dataclass(frozen=True, kw_only=True, slots=True)
class SubscriptionParameter:
    canonical: str
    parameter_name: str
    forecast_type: Literal["fc"] | None = None

    def __post_init__(self) -> None:
        if self.forecast_type not in (None, "fc"):
            raise ValueError("only control forecast subscriptions are supported")
        if not self.canonical or not self.parameter_name:
            raise ValueError(
                "exact canonical and subscription parameter names required"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastLeadBand:
    start: timedelta
    end: timedelta
    cadence: timedelta

    def __post_init__(self) -> None:
        if (
            self.start < timedelta(0)
            or self.end <= self.start
            or self.cadence <= timedelta(0)
        ):
            raise ValueError(
                "lead band requires nonnegative start and positive interval/cadence"
            )
        if (self.end - self.start) % self.cadence:
            raise ValueError("lead band boundaries must align with cadence")


@dataclass(frozen=True, kw_only=True, slots=True)
class ProductContract:
    stream: DataStream
    subscription_source: str
    cadence: timedelta
    provenance: str
    reference: str
    date_end: DateEndConvention
    publication_lag: timedelta
    snow_history_procedure: str = ""
    subscription_parameters: tuple[SubscriptionParameter, ...] = ()
    historical_source_run: SourceRunContract = field(default_factory=SourceRunContract)
    forecast_lead_bands: tuple[ForecastLeadBand, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.subscription_source.strip()
            or not self.provenance.strip()
            or not self.reference.strip()
        ):
            raise ValueError(
                "explicit product semantics and evidence reference required"
            )
        if self.cadence <= timedelta(0) or self.publication_lag < timedelta(0):
            raise ValueError("valid source cadence and publication lag required")
        if self.forecast_lead_bands:
            if self.stream not in (DataStream.IFS_FORECAST, DataStream.SNOW_FORECAST):
                raise ValueError("lead bands apply only to forecast streams")
            if self.forecast_lead_bands[0].start != timedelta(0) or any(
                left.end != right.start
                for left, right in zip(
                    self.forecast_lead_bands[:-1],
                    self.forecast_lead_bands[1:],
                    strict=True,
                )
            ):
                raise ValueError("lead bands must be ordered and contiguous from zero")

    def forecast_leads(self, start: timedelta, end: timedelta) -> tuple[timedelta, ...]:
        if start < timedelta(0) or start >= end:
            raise ValueError("nonempty forecast interval required")
        bands = self.forecast_lead_bands or (
            ForecastLeadBand(start=timedelta(0), end=end, cadence=self.cadence),
        )
        if end > bands[-1].end:
            raise ValueError("forecast interval exceeds declared lead support")
        for boundary in (start, end):
            if not any(
                b.start <= boundary <= b.end
                and (boundary - b.start) % b.cadence == timedelta(0)
                for b in bands
            ):
                raise ValueError("forecast interval boundary is not declared support")
        return tuple(
            lead
            for band in bands
            for n in range(int((band.end - band.start) / band.cadence))
            if start <= (lead := band.start + n * band.cadence) < end
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class RequirementSnapshot:
    target_parameters: tuple[str, ...]
    past_dynamic_features: tuple[str, ...]
    future_dynamic_features: tuple[str, ...]
    static_features: tuple[str, ...]
    supported_time_steps: tuple[timedelta, ...]
    lookback_steps: int
    forecast_horizon_steps: int
    spatial_input_type: str
    ensemble_mode: str
    required_past_targets: tuple[str, ...] | None
    declared_lookbacks: tuple[tuple[str, int], ...]
    declared_aggregations: tuple[tuple[str, str], ...]
    declared_horizon_semantics: str | None
    declared_min_future_steps: int | None


@dataclass(frozen=True, kw_only=True, slots=True)
class DataProposal:
    model_id: str
    requirements: RequirementSnapshot | None
    time_step: timedelta
    history: HistoryScope | None
    variables: tuple[RequestedVariable, ...]
    non_gateway_prerequisites: tuple[str, ...]
    holds: tuple[str, ...]
    configuration_hash: str | None = None
    forecast: ForecastScope | None = None
    contracts: tuple[ProductContract, ...] = ()


class StreamSetting(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    UNSPECIFIED = "unspecified"


@dataclass(frozen=True, kw_only=True, slots=True)
class Subscription:
    source: str
    hru_code: str
    parameter_name: str
    enabled: StreamSetting
    forecast_enabled: StreamSetting = StreamSetting.UNSPECIFIED
    reanalysis_enabled: StreamSetting = StreamSetting.UNSPECIFIED

    def __post_init__(self) -> None:
        for value in (self.source, self.hru_code, self.parameter_name):
            if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_:-]*", value) is None:
                raise ValueError("subscription identity must be a safe identifier")
        if self.enabled == StreamSetting.UNSPECIFIED:
            raise ValueError("subscription enabled state must be explicit")


@dataclass(frozen=True, kw_only=True, slots=True)
class SubscriptionChange:
    before: Subscription | None
    after: Subscription


@dataclass(frozen=True, kw_only=True, slots=True)
class SubscriptionPlan:
    proposal_revision: str
    changes: tuple[SubscriptionChange, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class SubscriptionAction:
    change: SubscriptionChange
    recorded_at: str
    outcome: Literal["intent", "verified", "uncertain"]


@dataclass(frozen=True, kw_only=True, slots=True)
class ChunkEvidence:
    scope_revision: str
    station_code: str
    stream: DataStream
    variable: str
    start: str
    end: str
    member: int | None
    cycle: str | None
    fetched_at: str
    expected_count: int
    usable_count: int
    missing_intervals: tuple[tuple[str, str], ...]
    holds: tuple[str, ...]
    response_sha256: str
    validated_source: str | None
    validated_source_run: SourceRunContract | None
    validated_run_identities: tuple[str, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class PreparationState:
    handover: ApprovedHandover
    attempts: tuple[RegistrationAttempt, ...] = ()
    schema_version: int = 2
    proposals: tuple[DataProposal, ...] = ()
    confirmed_proposal: str | None = None
    gateway_review_reference: str = ""
    subscription_plans: tuple[SubscriptionPlan, ...] = ()
    confirmed_subscription_plan: str | None = None
    subscription_actions: tuple[SubscriptionAction, ...] = ()
    coverage: tuple[ChunkEvidence, ...] = ()
    history_jobs: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise ValueError("unsupported preparation state version")
