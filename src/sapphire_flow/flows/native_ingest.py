from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from sapphire_flow.exceptions import ConfigurationError
from sapphire_flow.types.enums import ObservationSource
from sapphire_flow.types.historical_ingest import NativeCaptureScope
from sapphire_flow.types.native_capture import (
    CanonicalProviderIdentity,
    CaptureBatchContext,
    CapturedLevelReference,
    NativeFetchOutcome,
    accepted_capture_input_digest,
)
from sapphire_flow.types.rating_evidence import RatingEvidenceMode
from sapphire_flow.types.rating_reconciliation import RatingHoldReason

if TYPE_CHECKING:
    from uuid import UUID

    from sapphire_flow.config.dhm import DhmConfig
    from sapphire_flow.config.provisional_rating import (
        NativeRatingBinding,
        ProvisionalRatingConfig,
    )
    from sapphire_flow.protocols.native_capture import (
        NativeCaptureReader,
        NativeCaptureWriter,
    )
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import StationId
    from sapphire_flow.types.native_capture import CaptureRecoveryWindow
    from sapphire_flow.types.observation import HydroScraperBatchResult, RawObservation


def native_capture_bindings(config: object) -> tuple[NativeRatingBinding, ...]:
    configured = cast(
        "tuple[ProvisionalRatingConfig, ...]",
        getattr(config, "provisional_ratings", ()),
    )
    bindings = tuple(
        binding
        for item in configured
        if (binding := item.to_domain()).evidence_mode
        is RatingEvidenceMode.NATIVE_CAPTURE
    )
    if len({binding.station_id for binding in bindings}) != len(bindings):
        raise ConfigurationError("native capture requires a unique station binding")
    return bindings


@dataclass(frozen=True, kw_only=True, slots=True)
class NativeCaptureDispatch:
    station_ids: frozenset[StationId]
    observations_written: int
    canonical_observations: tuple[RawObservation, ...]
    failed_station_ids: frozenset[StationId]
    errors: tuple[str, ...]
    observations_unchanged: int = 0


def dispatch_native_captures(
    batch: HydroScraperBatchResult,
    bindings: tuple[NativeRatingBinding | NativeCaptureScope, ...],
    dhm: DhmConfig | None,
    writer: NativeCaptureWriter | None,
    *,
    flow_run_id: UUID | None,
) -> NativeCaptureDispatch:
    by_station = {binding.station_id: binding for binding in bindings}
    written = 0
    unchanged = 0
    accepted: list[RawObservation] = []
    failed: set[StationId] = set()
    errors: list[str] = []
    for outcome in batch.outcomes:
        binding = by_station.get(outcome.station_id)
        if binding is None or outcome.failure_cause is not None:
            continue
        rows = tuple(
            row
            for row in outcome.observations
            if row.parameter == "water_level"
            and row.source is ObservationSource.MEASURED
        )
        request = outcome.dhm_request_evidence
        if not rows and (
            request is None or request.outcome is not NativeFetchOutcome.COMPLETE
        ):
            continue
        reason = RatingHoldReason.MISSING_CAPTURE
        try:
            if request is None or writer is None or dhm is None:
                raise ValueError("native capture prerequisites unavailable")
            reason = RatingHoldReason.SOURCE_IDENTITY_MISMATCH
            matches = [
                item
                for item in dhm.bindings
                if item.station_code == binding.station_code
                and item.network
                == (
                    binding.network
                    if isinstance(binding, NativeCaptureScope)
                    else "dhm"
                )
            ]
            expected_provider = (
                binding.provider
                if isinstance(binding, NativeCaptureScope)
                else CanonicalProviderIdentity.parse(
                    binding.reference.endpoint, binding.reference.api_station_id
                )
            )
            if (
                len(matches) != 1
                or request.provider
                != CanonicalProviderIdentity.parse(
                    dhm.endpoint,
                    matches[0].api_station_id,
                )
                or request.provider != expected_provider
                or (
                    isinstance(binding, NativeCaptureScope)
                    and binding.level_reference
                    is not CapturedLevelReference(matches[0].level_reference)
                )
            ):
                raise ValueError("native capture provider scope disagreement")
            context = CaptureBatchContext(
                request=request,
                tenant_id=binding.tenant_id,
                flow_run_id=flow_run_id,
                accepted_input_sha256=accepted_capture_input_digest(rows),
                level_reference=CapturedLevelReference(matches[0].level_reference),
            )
            reason = RatingHoldReason.STORAGE_ERROR
            committed = writer.commit_batch(context, rows)
            written += len(committed.written_ids)
            unchanged += committed.unchanged_count
            accepted.extend(rows)
        except Exception:
            failed.add(outcome.station_id)
            errors.append(f"native_capture_held:{outcome.station_id}:{reason.value}")
    return NativeCaptureDispatch(
        station_ids=frozenset(by_station),
        observations_written=written,
        observations_unchanged=unchanged,
        canonical_observations=tuple(accepted),
        failed_station_ids=frozenset(failed),
        errors=tuple(errors),
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class NativeRecoveryDiscovery:
    windows: tuple[CaptureRecoveryWindow, ...]
    backlog: tuple[tuple[StationId, int | None], ...]
    failed_station_ids: frozenset[StationId]
    errors: tuple[str, ...]


def discover_native_recovery(
    reader: NativeCaptureReader | None,
    bindings: tuple[NativeRatingBinding | NativeCaptureScope, ...],
    *,
    now: UtcDatetime,
    start: UtcDatetime | None = None,
    end: UtcDatetime | None = None,
) -> NativeRecoveryDiscovery:
    from datetime import timedelta

    from sapphire_flow.types.datetime import ensure_utc
    from sapphire_flow.types.rating_reconciliation import RatingInputsUnavailable

    if (start is None) != (end is None):
        raise ConfigurationError("native recovery backfill requires both bounds")
    if (
        start is not None
        and end is not None
        and (not start < end <= now or end - start > timedelta(days=366))
    ):
        raise ConfigurationError("native recovery requires bounded past intervals")
    windows: list[CaptureRecoveryWindow] = []
    backlog: list[tuple[StationId, int | None]] = []
    failed: set[StationId] = set()
    errors: list[str] = []
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for binding in bindings:
        if start is None:
            if isinstance(binding, NativeCaptureScope):
                raise ConfigurationError(
                    "capture-only recovery requires explicit bounds"
                )
            lower = ensure_utc(midnight - timedelta(days=binding.reconciliation_days))
        else:
            lower = start
        stop = end or now
        older: int | None = None
        while lower < stop:
            chunk_start = ensure_utc(max(lower, stop - timedelta(hours=6)))
            try:
                if reader is None:
                    raise ValueError("native recovery reader unavailable")
                window = reader.pending_qc(
                    binding.station_id, start=chunk_start, end=stop, limit=1000
                )
                older = window.beyond_window_count
                if window.current_raw:
                    windows.append(window)
                if older == 0:
                    break
            except RatingInputsUnavailable as exc:
                failed.add(binding.station_id)
                errors.extend(
                    f"native_qc_recovery_held:{binding.station_id}:{held.reason.value}"
                    for held in exc.held
                )
                older = None
            except Exception:
                failed.add(binding.station_id)
                errors.append(
                    f"native_qc_recovery_held:{binding.station_id}:storage_error"
                )
                older = None
                break
            stop = chunk_start
        backlog.append((binding.station_id, older))
    return NativeRecoveryDiscovery(
        windows=tuple(windows),
        backlog=tuple(backlog),
        failed_station_ids=frozenset(failed),
        errors=tuple(errors),
    )
