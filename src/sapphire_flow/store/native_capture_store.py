from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, NonNegativeInt, StrictInt
from sqlalchemy.dialects.postgresql import insert

from sapphire_flow.db.metadata import (
    native_capture_batches,
    native_capture_sightings,
    native_measurement_captures,
)
from sapphire_flow.db.metadata import (
    observations as observations_table,
)
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.native_capture_predicates import (
    current_capture_matches_observation,
)
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.enums import AuditEventType, ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId, TenantId
from sapphire_flow.types.native_capture import (
    CanonicalProviderIdentity,
    CaptureBatchContext,
    CaptureBatchId,
    CaptureCommit,
    CapturedLevelReference,
    CapturedSourceVersion,
    CaptureId,
    CaptureRecoveryWindow,
    CurrentCapturedObservation,
    NativeCapture,
    NativeFetchOutcome,
    Sha256Digest,
    accepted_capture_input_digest,
    canonical_capture_rows,
)
from sapphire_flow.types.rating_reference import (
    MeasurementSnapshot,
    canonical_content,
    measurement_snapshot,
)

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.observation import RawObservation

TransactionFactory = Callable[[], AbstractContextManager[sa.Connection]]


class _ProviderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["bipad"]
    endpoint: str
    api_station_id: StrictInt


class _SourcePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    tenant_id: UUID
    station_id: UUID
    measurement: dict[str, Any]
    provider: _ProviderPayload
    level_unit: Literal["m"]
    level_reference: CapturedLevelReference


class _CommitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: UUID
    written_ids: tuple[UUID, ...]
    observed_capture_ids: tuple[UUID, ...]
    inserted_count: NonNegativeInt
    updated_count: NonNegativeInt
    unchanged_count: NonNegativeInt


def _capture_from_row(row: sa.RowMapping) -> NativeCapture:
    try:
        payload = _SourcePayload.model_validate_json(row["source_content"])
        source = CapturedSourceVersion(
            tenant_id=TenantId(payload.tenant_id),
            station_id=StationId(payload.station_id),
            observation_id=ObservationId(UUID(payload.measurement["id"])),
            measurement=MeasurementSnapshot(
                content=canonical_content(payload.measurement)
            ),
            provider=CanonicalProviderIdentity(
                endpoint=payload.provider.endpoint,
                api_station_id=payload.provider.api_station_id,
            ),
            level_reference=payload.level_reference,
        )
        capture = NativeCapture(id=CaptureId(row["id"]), source_version=source)
    except (ValueError, TypeError, KeyError):
        raise ValueError("stored native capture has invalid shape") from None
    if (
        source.content != row["source_content"]
        or source.version_sha256.value != row["source_version_sha256"]
        or capture.content != row["evidence_content"]
        or capture.evidence_sha256.value != row["evidence_sha256"]
        or source.tenant_id != row["tenant_id"]
        or source.station_id != row["station_id"]
        or source.observation_id != row["observation_id"]
    ):
        raise ValueError("stored native capture identity or digest disagreement")
    return capture


def _load_captures(
    conn: sa.Connection, identities: tuple[UUID, ...]
) -> tuple[NativeCapture, ...]:
    if not identities:
        return ()
    rows = (
        conn.execute(
            sa.select(native_measurement_captures).where(
                native_measurement_captures.c.id.in_(identities)
            )
        )
        .mappings()
        .all()
    )
    by_id = {row["id"]: _capture_from_row(row) for row in rows}
    if set(identities) != set(by_id):
        raise ValueError("stored native capture is unavailable")
    return tuple(by_id[identity] for identity in identities)


def _commit_content(result: CaptureCommit) -> str:
    return canonical_content(
        {
            "batch_id": result.batch_id,
            "written_ids": result.written_ids,
            "observed_capture_ids": tuple(c.id for c in result.observed_versions),
            "inserted_count": result.inserted_count,
            "updated_count": result.updated_count,
            "unchanged_count": result.unchanged_count,
        }
    )


def resolve_native_captures(
    conn: sa.Connection, versions: tuple[CapturedSourceVersion, ...]
) -> tuple[NativeCapture, ...]:
    if not versions:
        return ()
    predicates = [
        sa.and_(
            native_measurement_captures.c.tenant_id == version.tenant_id,
            native_measurement_captures.c.station_id == version.station_id,
            native_measurement_captures.c.observation_id == version.observation_id,
            native_measurement_captures.c.source_version_sha256
            == version.version_sha256.value,
        )
        for version in versions
    ]
    rows = (
        conn.execute(sa.select(native_measurement_captures).where(sa.or_(*predicates)))
        .mappings()
        .all()
    )
    return tuple(_capture_from_row(row) for row in rows)


class PgNativeCaptureStore:
    def __init__(
        self,
        transaction_factory: TransactionFactory,
        *,
        clock: Callable[[], UtcDatetime],
        id_gen: Callable[[], UUID],
    ) -> None:
        self._transaction = transaction_factory
        self._clock = clock
        self._id_gen = id_gen

    def commit_batch(
        self, batch: CaptureBatchContext, observations: tuple[RawObservation, ...]
    ) -> CaptureCommit:
        rows = canonical_capture_rows(observations)
        if accepted_capture_input_digest(rows) != batch.accepted_input_sha256:
            raise ValueError("capture accepted input digest disagreement")
        if rows and batch.request.outcome is not NativeFetchOutcome.COMPLETE:
            raise ValueError("failed fetch cannot establish source captures")
        if any(
            row.station_id != batch.request.station_id
            or row.parameter != "water_level"
            or row.source is not ObservationSource.MEASURED
            or row.delivery_id is not None
            for row in rows
        ):
            raise ValueError(
                "capture batch requires one native measured water-level station"
            )
        if any(
            not any(
                p.lower_exclusive < row.timestamp < p.upper_exclusive
                for p in batch.request.requested_intervals
            )
            for row in rows
        ):
            raise ValueError(
                "capture observations fall outside actual requested intervals"
            )
        with self._transaction() as conn:
            if (
                not conn.in_transaction()
                or conn.get_execution_options().get("isolation_level") == "AUTOCOMMIT"
            ):
                raise ValueError("native capture requires a real transaction")
            PgTenantStore(conn).lock_tenant(batch.tenant_id)
            key = int.from_bytes(
                hashlib.sha256(
                    ("native-capture-batch:" + str(batch.request.batch_id)).encode()
                ).digest()[:8],
                "big",
                signed=True,
            )
            conn.execute(sa.select(sa.func.pg_advisory_xact_lock(key)))
            prior = (
                conn.execute(
                    sa.select(native_capture_batches).where(
                        native_capture_batches.c.id == batch.request.batch_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if prior is not None:
                return self._replay(conn, batch, prior)
            station = PgStationStore(conn).fetch_station(batch.request.station_id)
            if (
                station is None
                or station.tenant_id != batch.tenant_id
                or station.network != "dhm"
            ):
                raise ValueError("native capture station scope mismatch")
            compatible = (
                station.water_level_unit in ("m", "m a.s.l.")
                if batch.level_reference is CapturedLevelReference.MASL
                else station.water_level_unit == "m"
                and station.water_level_datum_masl is None
            )
            if not compatible:
                raise ValueError("native capture requires compatible metre metadata")
            obs_store = PgObservationStore(conn)
            written = obs_store.store_raw_observations_detailed(list(rows))
            captured_at = self._clock()
            captures = tuple(
                self._capture_row(conn, obs_store, batch, raw, captured_at)
                for raw in rows
            )
            result = CaptureCommit(
                batch_id=batch.request.batch_id,
                written_ids=written.written_ids,
                observed_versions=captures,
                inserted_count=len(written.inserted_ids),
                updated_count=len(written.updated_ids),
                unchanged_count=len(rows) - len(written.written_ids),
            )
            content = _commit_content(result)
            conn.execute(
                sa.insert(native_capture_batches).values(
                    id=result.batch_id,
                    tenant_id=batch.tenant_id,
                    station_id=batch.request.station_id,
                    flow_run_id=batch.flow_run_id,
                    context_content=batch.content,
                    context_sha256=Sha256Digest.of(batch.content).value,
                    accepted_input_sha256=batch.accepted_input_sha256.value,
                    commit_content=content,
                    commit_sha256=Sha256Digest.of(content).value,
                    recorded_at=captured_at,
                )
            )
            if captures:
                conn.execute(
                    sa.insert(native_capture_sightings),
                    [
                        {
                            "batch_id": result.batch_id,
                            "capture_id": capture.id,
                            "tenant_id": batch.tenant_id,
                            "station_id": batch.request.station_id,
                        }
                        for capture in captures
                    ],
                )
            PgAuditLogStore(conn).append_entry(
                AuditEntry.system(  # pyright: ignore[reportUnknownMemberType]
                    event_type=AuditEventType.DELIVERY_IMPORTED,
                    target_type="station",
                    target_id=str(batch.request.station_id),
                    detail={
                        "command": "native-capture-commit",
                        "batch_id": str(result.batch_id),
                        "inserted": result.inserted_count,
                        "updated": result.updated_count,
                        "unchanged": result.unchanged_count,
                    },
                    ip_address=None,
                    created_at=captured_at,
                )
            )
            return result

    def _capture_row(
        self,
        conn: sa.Connection,
        obs_store: PgObservationStore,
        batch: CaptureBatchContext,
        raw: RawObservation,
        at: UtcDatetime,
    ) -> NativeCapture:
        identity = conn.execute(
            sa.select(observations_table.c.id)
            .where(
                observations_table.c.station_id == raw.station_id,
                observations_table.c.timestamp == raw.timestamp,
                observations_table.c.parameter == raw.parameter,
                observations_table.c.source == raw.source.value,
            )
            .with_for_update()
        ).scalar_one()
        observation = obs_store.fetch_observation_by_id(ObservationId(identity))
        if (
            observation is None
            or observation.value != raw.value
            or observation.delivery_id != raw.delivery_id
            or observation.rating_curve_id != raw.rating_curve_id
            or observation.rating_curve_correction_version
            != raw.rating_curve_correction_version
        ):
            raise ValueError("native capture raw postcondition failed")
        source = CapturedSourceVersion(
            tenant_id=batch.tenant_id,
            station_id=raw.station_id,
            observation_id=observation.id,
            measurement=measurement_snapshot(observation),
            provider=batch.request.provider,
            level_reference=batch.level_reference,
        )
        predicate = sa.and_(
            native_measurement_captures.c.tenant_id == source.tenant_id,
            native_measurement_captures.c.station_id == source.station_id,
            native_measurement_captures.c.observation_id == source.observation_id,
            native_measurement_captures.c.source_version_sha256
            == source.version_sha256.value,
        )
        prior = (
            conn.execute(sa.select(native_measurement_captures).where(predicate))
            .mappings()
            .one_or_none()
        )
        if prior is not None:
            return _capture_from_row(prior)
        capture = NativeCapture(id=CaptureId(self._id_gen()), source_version=source)
        conn.execute(
            insert(native_measurement_captures)
            .values(
                id=capture.id,
                tenant_id=source.tenant_id,
                station_id=source.station_id,
                observation_id=source.observation_id,
                source_version_sha256=source.version_sha256.value,
                source_content=source.content,
                evidence_content=capture.content,
                evidence_sha256=capture.evidence_sha256.value,
                registered_at=at,
            )
            .on_conflict_do_nothing(constraint="uq_native_capture_version")
        )
        row = (
            conn.execute(sa.select(native_measurement_captures).where(predicate))
            .mappings()
            .one()
        )
        return _capture_from_row(row)

    @staticmethod
    def _replay(
        conn: sa.Connection, batch: CaptureBatchContext, prior: sa.RowMapping
    ) -> CaptureCommit:
        if (
            prior["tenant_id"] != batch.tenant_id
            or prior["station_id"] != batch.request.station_id
            or prior["context_content"] != batch.content
            or prior["context_sha256"] != Sha256Digest.of(batch.content).value
            or prior["accepted_input_sha256"] != batch.accepted_input_sha256.value
        ):
            raise ValueError("capture batch identity was reused with different input")
        if Sha256Digest.of(prior["commit_content"]).value != prior["commit_sha256"]:
            raise ValueError("capture batch result digest disagreement")
        try:
            payload = _CommitPayload.model_validate_json(prior["commit_content"])
        except ValueError:
            raise ValueError("stored capture batch result is invalid") from None
        result = CaptureCommit(
            batch_id=CaptureBatchId(payload.batch_id),
            written_ids=tuple(ObservationId(i) for i in payload.written_ids),
            observed_versions=_load_captures(conn, payload.observed_capture_ids),
            inserted_count=payload.inserted_count,
            updated_count=payload.updated_count,
            unchanged_count=payload.unchanged_count,
        )
        if (
            result.batch_id != batch.request.batch_id
            or _commit_content(result) != prior["commit_content"]
        ):
            raise ValueError("stored capture batch result identity disagreement")
        return result

    def resolve(
        self, versions: tuple[CapturedSourceVersion, ...]
    ) -> tuple[NativeCapture, ...]:
        with self._transaction() as conn:
            return resolve_native_captures(conn, versions)

    def pending_qc(
        self,
        station_id: StationId,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        limit: int,
    ) -> CaptureRecoveryWindow:
        from datetime import timedelta

        if not start < end <= start + timedelta(hours=6) or not 1 <= limit <= 1000:
            raise ValueError("capture recovery requires six-hour/1000-row bounds")
        matching = observations_table.join(
            native_measurement_captures, current_capture_matches_observation()
        )
        scope = sa.and_(
            observations_table.c.station_id == station_id,
            observations_table.c.parameter == "water_level",
            observations_table.c.source == ObservationSource.MEASURED.value,
            observations_table.c.qc_status == QcStatus.RAW.value,
        )
        bounded = (
            sa.select(
                observations_table.c.id.label("observation_id"),
                observations_table.c.timestamp,
                native_measurement_captures.c.id.label("capture_id"),
                native_measurement_captures.c.source_version_sha256,
            )
            .select_from(matching)
            .where(
                scope,
                observations_table.c.timestamp >= start,
                observations_table.c.timestamp < end,
            )
            .distinct(observations_table.c.id)
            .order_by(
                observations_table.c.id,
                native_measurement_captures.c.id,
            )
            .limit(limit + 1)
            .subquery()
        )
        backlog = (
            sa.select(sa.func.count(sa.distinct(observations_table.c.id)))
            .select_from(matching)
            .where(scope, observations_table.c.timestamp < start)
            .scalar_subquery()
        )
        singleton = sa.select(sa.literal(1).label("one")).subquery()
        query = sa.select(bounded, backlog.label("backlog")).select_from(
            singleton.outerjoin(bounded, sa.true())
        )
        with self._transaction() as conn:
            records = conn.execute(query).mappings().all()
        current = tuple(
            CurrentCapturedObservation(
                observation_id=ObservationId(row["observation_id"]),
                timestamp=row["timestamp"],
                source_version_sha256=Sha256Digest(value=row["source_version_sha256"]),
                capture_id=CaptureId(row["capture_id"]),
            )
            for row in records
            if row["observation_id"] is not None
        )
        if len(current) > limit:
            from sapphire_flow.types.rating_reconciliation import (
                RatingHeldSource,
                RatingHoldReason,
                RatingInputsUnavailable,
            )

            raise RatingInputsUnavailable(
                (
                    RatingHeldSource(
                        observation_id=None,
                        source_version_sha256=None,
                        reason=RatingHoldReason.CHUNK_LIMIT,
                    ),
                )
            )
        return CaptureRecoveryWindow(
            station_id=station_id,
            start=start,
            end=end,
            current_raw=tuple(
                sorted(current, key=lambda row: (row.timestamp, row.observation_id))
            ),
            beyond_window_count=records[0]["backlog"],
        )
