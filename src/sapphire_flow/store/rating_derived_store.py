from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, replace
from typing import TYPE_CHECKING

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.pool import NullPool

from sapphire_flow.db.metadata import (
    native_measurement_captures,
    observations,
    rating_curves,
    rating_delivery_curves,
    rating_delivery_files,
    rating_derived_discharge,
)
from sapphire_flow.services.rating_derived import (
    derive_rating_sample,
    select_eligible_rating,
)
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.native_capture_store import resolve_native_captures
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.enums import AuditEventType, ObservationSource
from sapphire_flow.types.native_capture import (
    CanonicalProviderIdentity,
    CapturedLevelReference,
    CapturedSourceVersion,
    Sha256Digest,
)
from sapphire_flow.types.rating_derived import NativeCaptureFeed, NativeFeedCustody
from sapphire_flow.types.rating_evidence import RatingEvidenceMode
from sapphire_flow.types.rating_reconciliation import (
    AuditRecordState,
    RatingEvidenceError,
    RatingHeldSource,
    RatingHoldReason,
    RatingInputsUnavailable,
    RatingReconciliationOutcome,
    RatingWindowRead,
)
from sapphire_flow.types.rating_reference import (
    RatingReferenceProof,
    canonical_content,
    measurement_snapshot,
)

if TYPE_CHECKING:
    from sapphire_flow.config.dhm import DhmConfig
    from sapphire_flow.config.provisional_rating import NativeRatingBinding
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import ObservationId
    from sapphire_flow.types.observation import Observation
    from sapphire_flow.types.rating_curve import RatingCurve
    from sapphire_flow.types.rating_derived import RatingDerivedDischarge

log = structlog.get_logger(__name__)

TransactionFactory = Callable[[], AbstractContextManager[sa.Connection]]


def rating_transaction_factory(url: sa.URL) -> TransactionFactory:
    @contextmanager
    def transaction() -> Iterator[sa.Connection]:
        engine = sa.create_engine(
            url, poolclass=NullPool, isolation_level="READ COMMITTED"
        )
        try:
            with engine.begin() as conn:
                yield conn
        finally:
            engine.dispose()

    return transaction


def _select_approved_legacy(
    conn: sa.Connection,
    binding: NativeRatingBinding,
    observation: Observation,
    curve: RatingCurve,
    selected: sa.RowMapping,
    *,
    at: UtcDatetime,
) -> RatingDerivedDischarge | None:
    segments = [
        segment
        for segment in binding.legacy_segments
        if segment.feed.start <= observation.timestamp < segment.feed.end
    ]
    if not segments:
        return None
    approved = {
        fingerprint.value
        for segment in segments
        for fingerprint in segment.fingerprints
    }
    candidates = (
        conn.execute(
            sa.select(rating_derived_discharge)
            .where(
                rating_derived_discharge.c.tenant_id == binding.tenant_id,
                rating_derived_discharge.c.station_id == binding.station_id,
                rating_derived_discharge.c.observation_id == observation.id,
                rating_derived_discharge.c.evidence_version == 1,
                rating_derived_discharge.c.fingerprint.in_(approved),
            )
            .limit(1001)
        )
        .mappings()
        .all()
    )
    if len(candidates) > 1000:
        raise RatingInputsUnavailable(
            (
                RatingHeldSource(
                    observation_id=observation.id,
                    source_version_sha256=None,
                    reason=RatingHoldReason.CHUNK_LIMIT,
                ),
            )
        )
    by_fingerprint = {row["fingerprint"]: row for row in candidates}
    for segment in segments:
        candidate = derive_rating_sample(
            observation,
            curve,
            file_id=selected["id"],
            file_sha256=selected["sha256"],
            delivered_on=selected["delivered_on"].isoformat(),
            feed=segment.feed,
            reference=binding.reference,
            at=at,
        )
        persisted = by_fingerprint.get(candidate.fingerprint)
        if (
            Sha256Digest(value=candidate.fingerprint) in segment.fingerprints
            and persisted is not None
            and persisted["content"] == candidate.content
            and persisted["capture_id"] is None
        ):
            return candidate
    return None


class PgRatingDerivedStore:
    def __init__(self, transaction_factory: TransactionFactory) -> None:
        self._transaction = transaction_factory

    def reconcile_chunk(
        self,
        binding: NativeRatingBinding,
        dhm: DhmConfig,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
    ) -> tuple[RatingDerivedDischarge, ...]:
        records, outcome, error = self._reconcile_attempt(
            binding, dhm, start=start, end=end, at=at
        )
        if error is not None:
            raise error
        if outcome.held:
            raise RatingInputsUnavailable(outcome.held)
        return records

    def reconcile_chunk_outcome(
        self,
        binding: NativeRatingBinding,
        dhm: DhmConfig,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
    ) -> RatingReconciliationOutcome:
        return self._reconcile_attempt(binding, dhm, start=start, end=end, at=at)[1]

    def _reconcile_attempt(
        self,
        binding: NativeRatingBinding,
        dhm: DhmConfig,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
    ) -> tuple[
        tuple[RatingDerivedDischarge, ...],
        RatingReconciliationOutcome,
        Exception | None,
    ]:
        try:
            with self._transaction() as conn:
                held: list[RatingHeldSource] = []
                records = self._current(
                    conn, binding, dhm, start=start, end=end, at=at, held=held
                )
                inserted = 0
                for record in records:
                    values = asdict(record)
                    values.pop("warnings")
                    added = conn.execute(
                        insert(rating_derived_discharge)
                        .values(
                            **values,
                            fingerprint=record.fingerprint,
                            captured_at=at,
                        )
                        .on_conflict_do_nothing(index_elements=["fingerprint"])
                        .returning(rating_derived_discharge.c.fingerprint)
                    ).scalar_one_or_none()
                    inserted += added is not None
                outcome = RatingReconciliationOutcome(
                    start=start,
                    end=end,
                    accepted=sum(record.discharge is not None for record in records),
                    rejected=sum(record.rejection is not None for record in records),
                    inserted=inserted,
                    held=tuple(held),
                    qc_unassessed=sum(
                        record.discharge is not None
                        and "qc_unassessed" in record.warnings
                        for record in records
                    ),
                )
                policies = tuple(
                    sorted({json.loads(record.content)["policy"] for record in records})
                )
                self._append_outcome(conn, binding, outcome, policies=policies, at=at)
            return records, outcome, None
        except Exception as exc:
            if isinstance(exc, RatingInputsUnavailable):
                held_sources = exc.held
            else:
                reason = (
                    exc.reason
                    if isinstance(exc, RatingEvidenceError)
                    else RatingHoldReason.STORAGE_ERROR
                )
                held_sources = (
                    RatingHeldSource(
                        observation_id=None, source_version_sha256=None, reason=reason
                    ),
                )
            outcome = RatingReconciliationOutcome(
                start=start,
                end=end,
                accepted=0,
                rejected=0,
                inserted=0,
                held=held_sources,
                qc_unassessed=0,
            )
            try:
                with self._transaction() as conn:
                    self._append_outcome(conn, binding, outcome, policies=(), at=at)
            except Exception:
                outcome = replace(outcome, audit_record_state=AuditRecordState.FAILED)
                log.error(
                    "rating.reconciliation_audit_failed",
                    station_id=str(binding.station_id),
                    reason="storage_error",
                )
            return (), outcome, exc

    @staticmethod
    def _append_outcome(
        conn: sa.Connection,
        binding: NativeRatingBinding,
        outcome: RatingReconciliationOutcome,
        *,
        policies: tuple[str, ...],
        at: UtcDatetime,
    ) -> None:
        from sapphire_flow.types.datetime import ensure_utc

        counts = {
            reason.value: sum(source.reason is reason for source in outcome.held)
            for reason in RatingHoldReason
            if any(source.reason is reason for source in outcome.held)
        }
        recorded_at = ensure_utc(
            conn.execute(sa.select(sa.func.clock_timestamp())).scalar_one()
        )
        PgAuditLogStore(conn).append_entry(
            AuditEntry.system(  # pyright: ignore[reportUnknownMemberType]
                event_type=AuditEventType.DELIVERY_IMPORTED,
                target_type="station",
                target_id=str(binding.station_id),
                detail={
                    "command": "rating-derived-reconcile",
                    "schema_version": 2,
                    "window_start": outcome.start.isoformat(),
                    "window_end": outcome.end.isoformat(),
                    "accepted": outcome.accepted,
                    "rejected": outcome.rejected,
                    "inserted": outcome.inserted,
                    "held_counts": counts,
                    "qc_unassessed": outcome.qc_unassessed,
                    "policies": list(policies),
                    "policy": policies[0]
                    if len(policies) == 1
                    else "mixed"
                    if policies
                    else None,
                    "state": "held" if outcome.held else "complete",
                    "validation_at": at.isoformat(),
                },
                ip_address=None,
                created_at=recorded_at,
            )
        )

    def read_current(
        self,
        binding: NativeRatingBinding,
        dhm: DhmConfig,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
    ) -> tuple[RatingDerivedDischarge, ...]:
        window = self.read_current_window(binding, dhm, start=start, end=end, at=at)
        hard = tuple(
            source
            for source in window.held
            if source.reason is not RatingHoldReason.UNRECONCILED
        )
        if hard:
            raise RatingInputsUnavailable(hard)
        return window.records

    def read_current_window(
        self,
        binding: NativeRatingBinding,
        dhm: DhmConfig,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
    ) -> RatingWindowRead:
        import json

        held: list[RatingHeldSource] = []
        with self._transaction() as conn:
            current = self._current(
                conn, binding, dhm, start=start, end=end, at=at, held=held
            )
            source_count = len(current) + len(held)
            fingerprints = [record.fingerprint for record in current]
            stored = set(
                conn.execute(
                    sa.select(rating_derived_discharge.c.fingerprint).where(
                        rating_derived_discharge.c.fingerprint.in_(fingerprints),
                    )
                ).scalars()
            )
            for record in current:
                if record.fingerprint in stored:
                    continue
                data = json.loads(record.content)
                source_version = data["feed"].get("source_version_sha256")
                held.append(
                    RatingHeldSource(
                        observation_id=record.observation_id,
                        timestamp=record.timestamp,
                        source_version_sha256=Sha256Digest(value=source_version)
                        if source_version
                        else None,
                        source_snapshot_sha256=Sha256Digest.of(
                            canonical_content(data["source"])
                        ),
                        reason=RatingHoldReason.UNRECONCILED,
                    )
                )
            return RatingWindowRead(
                start=start,
                end=end,
                records=tuple(
                    record for record in current if record.fingerprint in stored
                ),
                held=tuple(held),
                source_count=source_count,
            )

    @staticmethod
    def _current(
        conn: sa.Connection,
        binding: NativeRatingBinding,
        dhm: DhmConfig,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
        held: list[RatingHeldSource] | None = None,
    ) -> tuple[RatingDerivedDischarge, ...]:
        if binding.evidence_mode is RatingEvidenceMode.LEGACY_CUSTODY and (
            binding.custody_start is None
            or binding.custody_end is None
            or not binding.custody_start <= start < end <= binding.custody_end
        ):
            raise RatingEvidenceError(
                RatingHoldReason.MISSING_CAPTURE,
                "requested interval lacks documented native feed custody",
            )
        PgTenantStore(conn).lock_tenant(binding.tenant_id)
        station = PgStationStore(conn).fetch_station(binding.station_id)
        if (
            station is None
            or station.tenant_id != binding.tenant_id
            or station.code != binding.station_code
            or station.network != "dhm"
        ):
            raise RatingEvidenceError(
                RatingHoldReason.SOURCE_IDENTITY_MISMATCH,
                "native rating station identity mismatch",
            )
        matches = [item for item in dhm.bindings if item.station_code == station.code]
        reference = binding.reference
        if len(matches) != 1 or (dhm.endpoint, matches[0].api_station_id) != (
            reference.endpoint,
            reference.api_station_id,
        ):
            raise RatingEvidenceError(
                RatingHoldReason.SOURCE_IDENTITY_MISMATCH,
                "native feed differs from exact reference binding",
            )
        level_reference = matches[0].level_reference
        if isinstance(reference, RatingReferenceProof) and (
            level_reference != reference.level_reference
        ):
            raise RatingEvidenceError(
                RatingHoldReason.SOURCE_IDENTITY_MISMATCH,
                "native feed datum differs from exact reference binding",
            )
        compatible_unit = (
            station.water_level_unit in ("m", "m a.s.l.")
            if level_reference == "masl"
            else station.water_level_unit == "m"
            and station.water_level_datum_masl is None
        )
        if not compatible_unit:
            raise RatingEvidenceError(
                RatingHoldReason.SOURCE_IDENTITY_MISMATCH,
                "native feed requires compatible metre unit and datum",
            )
        selected_candidates = (
            conn.execute(
                sa.select(rating_delivery_files)
                .where(
                    rating_delivery_files.c.station_id == binding.station_id,
                    rating_delivery_files.c.tenant_id == binding.tenant_id,
                )
                .order_by(rating_delivery_files.c.delivered_on.desc())
                .limit(2)
            )
            .mappings()
            .all()
        )
        if not selected_candidates:
            raise RatingEvidenceError(
                RatingHoldReason.REFERENCE_UNAVAILABLE, "no admitted rating delivery"
            )
        selected = selected_candidates[0]
        if len(selected_candidates) > 1 and (
            selected_candidates[1]["delivered_on"] == selected["delivered_on"]
        ):
            raise RatingEvidenceError(
                RatingHoldReason.REFERENCE_UNAVAILABLE,
                "ambiguous tied newest rating deliveries",
            )
        curve_ids = list(
            conn.execute(
                sa.select(rating_delivery_curves.c.rating_curve_id).where(
                    rating_delivery_curves.c.file_id == selected["id"],
                )
            ).scalars()
        )
        conn.execute(
            sa.select(rating_curves.c.id)
            .where(rating_curves.c.id.in_(curve_ids))
            .with_for_update(read=True)
        ).all()
        curves = [
            curve
            for curve in PgRatingCurveStore(conn).fetch_all_curves_for_station(
                binding.station_id
            )
            if curve.id in curve_ids
        ]
        locked = conn.execute(
            sa.select(observations.c.id)
            .where(
                observations.c.station_id == binding.station_id,
                observations.c.parameter == "water_level",
                observations.c.source == ObservationSource.MEASURED.value,
                observations.c.timestamp >= start,
                observations.c.timestamp < end,
            )
            .limit(1001)
            .with_for_update(read=True)
        ).all()
        if len(locked) > 1000:
            raise RatingEvidenceError(
                RatingHoldReason.CHUNK_LIMIT,
                "rating chunk exceeds 1000 source observations",
            )
        rows = PgObservationStore(conn).fetch_observations(
            binding.station_id,
            "water_level",
            start,
            end,
            source=ObservationSource.MEASURED,
        )
        from sapphire_flow.adapters.dhm_files import parse_rating_tables
        from sapphire_flow.adapters.nepal_local_day import nepal_day_start

        original = parse_rating_tables(bytes(selected["original"]).decode("utf-8-sig"))
        locked_ids = {row.id for row in locked}
        captured_history: set[ObservationId] = set()
        if binding.evidence_mode is RatingEvidenceMode.LEGACY_CUSTODY:
            captured_history = set(
                conn.execute(
                    sa.select(native_measurement_captures.c.observation_id)
                    .where(
                        native_measurement_captures.c.tenant_id == binding.tenant_id,
                        native_measurement_captures.c.station_id == binding.station_id,
                        native_measurement_captures.c.observation_id.in_(locked_ids),
                    )
                    .distinct()
                ).scalars()
            )
            if captured_history:
                log.warning(
                    "rating.native_capture_required",
                    station_id=str(binding.station_id),
                    count=len(captured_history),
                    reason="native_capture_required",
                )
        result: list[RatingDerivedDischarge] = []
        for observation in rows:
            if observation.id not in locked_ids:
                continue
            if observation.id in captured_history:
                blocked = RatingHeldSource(
                    observation_id=observation.id,
                    source_version_sha256=None,
                    reason=RatingHoldReason.SOURCE_IDENTITY_MISMATCH,
                )
                if held is None:
                    raise RatingInputsUnavailable((blocked,))
                held.append(blocked)
                continue
            starts = [
                nepal_day_start(block.from_date)
                for block in original.blocks
                if nepal_day_start(block.from_date) <= observation.timestamp
            ]
            if not starts:
                raise RatingEvidenceError(
                    RatingHoldReason.REFERENCE_UNAVAILABLE,
                    "selected delivery has no eligible historical block",
                )
            selected_start = max(starts)
            if starts.count(selected_start) != 1 or not any(
                c.valid_from == selected_start for c in curves
            ):
                raise ValueError(
                    "selected historical rating block is invalid; no fallback"
                )
            curve = select_eligible_rating(curves, observation.timestamp)
            feed: NativeFeedCustody | NativeCaptureFeed
            if binding.evidence_mode is RatingEvidenceMode.NATIVE_CAPTURE:
                legacy = _select_approved_legacy(
                    conn,
                    binding,
                    observation,
                    curve,
                    selected,
                    at=at,
                )
                if legacy is not None:
                    result.append(legacy)
                    continue
                try:
                    version = CapturedSourceVersion(
                        tenant_id=binding.tenant_id,
                        station_id=binding.station_id,
                        observation_id=observation.id,
                        measurement=measurement_snapshot(observation),
                        provider=CanonicalProviderIdentity.parse(
                            dhm.endpoint,
                            matches[0].api_station_id,
                        ),
                        level_reference=CapturedLevelReference(level_reference),
                    )
                except ValueError:
                    version = None
                captured = resolve_native_captures(conn, (version,)) if version else ()
                if not captured:
                    missing = RatingHeldSource(
                        observation_id=observation.id,
                        source_version_sha256=version.version_sha256
                        if version
                        else None,
                        reason=RatingHoldReason.MISSING_CAPTURE,
                    )
                    if held is None:
                        raise RatingInputsUnavailable((missing,))
                    held.append(missing)
                    continue
                feed = NativeCaptureFeed(capture=captured[0])
            else:
                if (
                    binding.custody_start is None
                    or binding.custody_end is None
                    or binding.custody_reference is None
                ):
                    raise RatingEvidenceError(
                        RatingHoldReason.MISSING_CAPTURE,
                        "legacy custody is unavailable",
                    )
                feed = NativeFeedCustody(
                    tenant_id=binding.tenant_id,
                    station_id=binding.station_id,
                    endpoint=dhm.endpoint,
                    api_station_id=matches[0].api_station_id,
                    evidence_reference=binding.custody_reference,
                    start=binding.custody_start,
                    end=binding.custody_end,
                )
            result.append(
                derive_rating_sample(
                    observation,
                    curve,
                    file_id=selected["id"],
                    file_sha256=selected["sha256"],
                    delivered_on=selected["delivered_on"].isoformat(),
                    feed=feed,
                    reference=reference,
                    at=at,
                )
            )
        return tuple(result)
