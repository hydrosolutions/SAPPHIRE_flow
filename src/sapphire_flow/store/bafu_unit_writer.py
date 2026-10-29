from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import sqlalchemy as sa
import structlog
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.pool import NullPool

from sapphire_flow.db.metadata import bafu_unit_batch_admissions as admissions
from sapphire_flow.db.metadata import bafu_unit_batch_receipts as receipts
from sapphire_flow.db.metadata import bafu_unit_event_reconciliation as safe_events
from sapphire_flow.db.metadata import bafu_unit_events as events
from sapphire_flow.db.metadata import bafu_unit_read_holds as holds
from sapphire_flow.db.metadata import bafu_unit_receipt_reconciliation as safe_receipts
from sapphire_flow.db.metadata import observations
from sapphire_flow.exceptions import DeliveryCollisionError
from sapphire_flow.store.bafu_batch_journal import (
    BafuBatchIntegrityError,
    PgBafuBatchJournal,
    lock_bafu_batch,
    validate_bafu_binding,
)
from sapphire_flow.store.bafu_unit_documents import (
    ReceiptDocument,
    ReceiptStateReference,
    capture_document,
    counts_document,
    document_digest,
    encode_document,
    observation_state,
    value_state,
)
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.types.bafu_capture import NormalizedDischargeCapture
from sapphire_flow.types.bafu_capture_batch import BafuAdmissionDisposition
from sapphire_flow.types.bafu_write_result import (
    BafuApplicability,
    BafuCapacityError,
    BafuCommitAcknowledgementError,
    BafuOutcomeCounts,
    BafuWriteResult,
    BafuWriteStatus,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.observation_units import DischargeSourceUnit

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from sapphire_flow.types.bafu_capture import BafuDischargeCapture
    from sapphire_flow.types.bafu_capture_batch import BafuStationCaptureBatch
    from sapphire_flow.types.bafu_write_result import BafuJournalQuota
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.observation import RawObservation


log = structlog.get_logger(__name__)


def _validate_capture(event: sa.RowMapping) -> None:
    if not event["capture_valid"]:
        raise BafuBatchIntegrityError("malformed stored evidence document")


def _db_state(conn: sa.Connection, state: dict[str, Any] | None) -> str | None:
    if state is None:
        return None
    return conn.execute(
        sa.select(
            sa.func.bafu_unit_db_state(
                sa.cast(sa.literal(encode_document(state)), JSONB)
            )
        )
    ).scalar_one()


def _row(
    conn: sa.Connection,
    batch: BafuStationCaptureBatch,
    timestamp: UtcDatetime,
    parameter: str = "discharge",
) -> sa.RowMapping | None:
    return (
        conn.execute(
            sa.select(observations)
            .where(
                observations.c.station_id == batch.station_id,
                observations.c.timestamp == timestamp,
                observations.c.parameter == parameter,
                observations.c.source == "measured",
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )


def _latest(
    conn: sa.Connection,
    batch: BafuStationCaptureBatch,
    timestamp: UtcDatetime | None,
) -> sa.RowMapping | None:
    return (
        conn.execute(
            sa.select(safe_events)
            .where(
                safe_events.c.station_id == batch.station_id,
                safe_events.c.tenant_id == batch.tenant_id,
                safe_events.c.measurement_time.is_(None)
                if timestamp is None
                else safe_events.c.measurement_time == timestamp,
            )
            .order_by(safe_events.c.event_id.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )


def _same_value_state(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> bool:
    if left is None or right is None:
        return left is right
    return document_digest(value_state(left)) == document_digest(value_state(right))


def _proven(event: sa.RowMapping | None, state: dict[str, Any] | None) -> bool:
    return (
        event is not None
        and state is not None
        and event["value_state_sha256"] == document_digest(value_state(state))
    )


def _assert_intended(row: sa.RowMapping | None, raw: RawObservation) -> None:
    if row is not None and row["delivery_id"] != raw.delivery_id:
        raise DeliveryCollisionError(raw.station_id)
    intended = {
        "station_id": raw.station_id,
        "timestamp": raw.timestamp,
        "parameter": raw.parameter,
        "source": raw.source.value,
        "value": raw.value,
        "rating_curve_id": raw.rating_curve_id,
        "rating_curve_correction_version": raw.rating_curve_correction_version,
        "delivery_id": raw.delivery_id,
    }
    if row is None or any(row[key] != value for key, value in intended.items()):
        raise BafuBatchIntegrityError("persisted row differs from intended observation")


def bafu_unit_transaction_factory(
    url: sa.URL,
) -> Callable[[], AbstractContextManager[sa.Connection]]:
    engine = sa.create_engine(
        url,
        poolclass=NullPool,
        isolation_level="READ COMMITTED",
        connect_args={"connect_timeout": 5},
    )
    return engine.begin


class PgBafuUnitWriter:
    """Atomic station writer on fresh READ COMMITTED transactions."""

    def __init__(
        self,
        transaction_factory: Callable[[], AbstractContextManager[sa.Connection]],
        *,
        quota: BafuJournalQuota,
        fault_point: Callable[[str], None] | None = None,
    ) -> None:
        self._transaction_factory = transaction_factory
        self._quota = quota
        self._fault_point = fault_point

    def write(self, batch: BafuStationCaptureBatch) -> BafuWriteResult:
        # START survives a subsequent station rollback. Do not merge admission
        # into the effects transaction: later START must fence unresolved work.
        try:
            PgBafuBatchJournal(self._transaction_factory).admit(batch)
            with self._transaction_factory() as conn:
                lock_bafu_batch(conn, batch)
                validate_bafu_binding(conn, batch)
                receipt, superseded = self._existing(conn, batch)
                if receipt is not None:
                    result = self._replay(conn, batch, receipt)
                elif superseded:
                    result = BafuWriteResult(
                        batch_id=batch.batch_id,
                        status=BafuWriteStatus.SUPERSEDED,
                        applicability=BafuApplicability.SUPERSEDED,
                    )
                else:
                    result = self._apply(conn, batch)
            return result
        except ValidationError as exc:
            raise BafuBatchIntegrityError("malformed stored evidence document") from exc
        except (SQLAlchemyError, BafuCommitAcknowledgementError) as exc:
            if (
                isinstance(exc, DBAPIError)
                and getattr(exc.orig, "sqlstate", None) == "PBU01"
            ):
                raise BafuBatchIntegrityError(
                    "BAFU database unit guard rejected batch"
                ) from exc
            log.warning(
                "bafu_unit.write_unacknowledged",
                batch_id=str(batch.batch_id),
                error_type=type(exc).__name__,
            )
            # Never retry writes here. An unreliable fresh read is UNKNOWN.
            return self.reconcile(batch)

    def reconcile(self, batch: BafuStationCaptureBatch) -> BafuWriteResult:
        try:
            with self._transaction_factory() as conn:
                lock_bafu_batch(conn, batch)
                validate_bafu_binding(conn, batch)
                receipt, superseded = self._existing(conn, batch)
                if receipt is not None:
                    return self._replay(conn, batch, receipt)
                return BafuWriteResult(
                    batch_id=batch.batch_id,
                    status=BafuWriteStatus.SUPERSEDED
                    if superseded
                    else BafuWriteStatus.NOT_COMMITTED,
                    applicability=BafuApplicability.SUPERSEDED
                    if superseded
                    else BafuApplicability.UNKNOWN,
                )
        except BafuBatchIntegrityError:
            raise
        except ValidationError as exc:
            raise BafuBatchIntegrityError("malformed stored evidence document") from exc
        except (SQLAlchemyError, BafuCommitAcknowledgementError):
            return BafuWriteResult(
                batch_id=batch.batch_id,
                status=BafuWriteStatus.UNKNOWN,
                applicability=BafuApplicability.UNKNOWN,
            )

    def _existing(
        self,
        conn: sa.Connection,
        batch: BafuStationCaptureBatch,
    ) -> tuple[sa.RowMapping | None, bool]:
        admitted = (
            conn.execute(
                sa.select(admissions).where(
                    admissions.c.batch_id == batch.batch_id,
                )
            )
            .mappings()
            .first()
        )
        if admitted is not None and (
            admitted["input_digest"] != batch.input_digest
            or admitted["station_id"] != batch.station_id
            or admitted["tenant_id"] != batch.tenant_id
        ):
            raise BafuBatchIntegrityError(
                "batch ID reused with different captured input"
            )
        latest = conn.execute(
            sa.select(admissions.c.sequence, admissions.c.received_at)
            .where(
                admissions.c.station_id == batch.station_id,
                admissions.c.tenant_id == batch.tenant_id,
                admissions.c.disposition == BafuAdmissionDisposition.ADMITTED.value,
            )
            .order_by(admissions.c.sequence.desc())
            .limit(1)
        ).first()
        receipt = (
            conn.execute(
                sa.select(safe_receipts).where(
                    safe_receipts.c.batch_id == batch.batch_id,
                )
            )
            .mappings()
            .first()
        )
        if admitted is None:
            return (
                receipt,
                latest is not None and latest.received_at >= batch.received_at,
            )
        if (
            BafuAdmissionDisposition(admitted["disposition"])
            is BafuAdmissionDisposition.REJECTED
        ):
            return receipt, True
        return receipt, latest is not None and latest.sequence > admitted["sequence"]

    def _replay(
        self,
        conn: sa.Connection,
        batch: BafuStationCaptureBatch,
        row: sa.RowMapping,
    ) -> BafuWriteResult:
        doc = ReceiptDocument.model_validate({**row["summary"], "observations": []})
        if (
            doc.input_digest != batch.input_digest
            or row["input_digest"] != batch.input_digest
            or not row["digest_valid"]
        ):
            raise BafuBatchIntegrityError("committed receipt digest mismatch")
        current = True
        canonical_times: set[UtcDatetime] = set()
        for event_id in doc.event_ids:
            event = (
                conn.execute(
                    sa.select(safe_events).where(
                        safe_events.c.event_id == event_id,
                        safe_events.c.station_id == batch.station_id,
                        safe_events.c.tenant_id == batch.tenant_id,
                    )
                )
                .mappings()
                .first()
            )
            if event is None:
                raise BafuBatchIntegrityError("receipt references missing event")
            _validate_capture(event)
            latest = _latest(conn, batch, event["measurement_time"])
            if latest is None or latest["event_id"] != event_id:
                current = False
            if event["observation_id"] is not None:
                canonical_times.add(event["measurement_time"])
                observation = _row(conn, batch, event["measurement_time"])
                state = (
                    observation_state(observation) if observation is not None else None
                )
                if not _proven(event, state):
                    current = False
            elif event["measurement_time"] is not None:
                observation = _row(conn, batch, event["measurement_time"])
                state = (
                    observation_state(observation) if observation is not None else None
                )
                if event["before_db_sha256"] != _db_state(conn, state):
                    current = False
        for previous in row["states"]:
            reference = ReceiptStateReference.model_validate(previous)
            observation = _row(
                conn, batch, ensure_utc(reference.timestamp), reference.parameter
            )
            if (
                observation is None
                or _db_state(conn, observation_state(observation))
                != reference.db_sha256
            ):
                current = False
        return BafuWriteResult(
            batch_id=batch.batch_id,
            status=BafuWriteStatus.REPLAYED,
            applicability=BafuApplicability.CURRENT
            if current
            else BafuApplicability.SUPERSEDED,
            counts=doc.domain_counts(),
            result_digest=row["result_digest"],
            event_ids=tuple(doc.event_ids),
            canonical_observations=tuple(
                obs
                for obs in batch.observations
                if obs.parameter != "discharge" or obs.timestamp in canonical_times
            )
            if current
            else (),
        )

    def _apply(
        self, conn: sa.Connection, batch: BafuStationCaptureBatch
    ) -> BafuWriteResult:
        input_bytes = sum(
            len(
                encode_document(
                    capture_document(
                        capture,
                        registry_version=batch.registry_version,
                        registry_sha256=batch.registry_sha256,
                    )
                ).encode()
            )
            for capture in batch.captures
        )
        if input_bytes > self._quota.max_batch_bytes:
            raise BafuCapacityError(
                "station capture exceeds journal quota; entire batch rolled back"
            )
        counts = dict.fromkeys(asdict(BafuOutcomeCounts()), 0)
        event_ids: list[int] = []
        canonical: list[RawObservation] = []
        new_events = 0
        for capture in batch.captures:
            outcome, event_id = self._capture(conn, batch, capture)
            counts[outcome] += 1
            event_ids.append(event_id)
            event = (
                conn.execute(
                    sa.select(safe_events).where(safe_events.c.event_id == event_id)
                )
                .mappings()
                .one()
            )
            if event["observation_id"] is not None:
                canonical.extend(
                    obs
                    for obs in batch.observations
                    if obs.parameter == "discharge"
                    and obs.timestamp == capture.result.measurement_time
                )
            new_events += outcome != "unchanged"
        other = [obs for obs in batch.observations if obs.parameter != "discharge"]
        changed = PgObservationStore(conn).store_raw_observations(other)
        canonical.extend(other)
        states: list[dict[str, Any]] = []
        for obs in other:
            row = _row(conn, batch, obs.timestamp, obs.parameter)
            _assert_intended(row, obs)
            assert row is not None
            states.append(observation_state(row))
        domain_counts = BafuOutcomeCounts(**counts)
        if not new_events and not changed:
            return BafuWriteResult(
                batch_id=batch.batch_id,
                status=BafuWriteStatus.CURRENT,
                applicability=BafuApplicability.CURRENT,
                counts=domain_counts,
                event_ids=tuple(event_ids),
                canonical_observations=tuple(canonical),
            )
        doc = ReceiptDocument(
            input_digest=batch.input_digest,
            expected_captures=len(batch.captures),
            counts=counts_document(domain_counts),
            event_ids=event_ids,
            observations=states,
        ).model_dump(mode="json")
        digest = document_digest(doc)
        conn.execute(
            sa.insert(receipts).values(
                batch_id=batch.batch_id,
                input_digest=batch.input_digest,
                result_digest=digest,
                result=doc,
            )
        )
        self._check_quota(conn, batch)
        if self._fault_point is not None:
            self._fault_point("before_commit")
        return BafuWriteResult(
            batch_id=batch.batch_id,
            status=BafuWriteStatus.COMMITTED,
            applicability=BafuApplicability.CURRENT,
            counts=domain_counts,
            result_digest=digest,
            event_ids=tuple(event_ids),
            observations_written=domain_counts.inserted
            + domain_counts.restated
            + len(changed),
            canonical_observations=tuple(canonical),
        )

    def _capture(
        self,
        conn: sa.Connection,
        batch: BafuStationCaptureBatch,
        capture: BafuDischargeCapture,
    ) -> tuple[str, int]:
        timestamp = capture.result.measurement_time
        old = _row(conn, batch, timestamp) if timestamp is not None else None
        if old is not None and old["delivery_id"] is not None:
            raise DeliveryCollisionError(batch.station_id)
        before = observation_state(old) if old is not None else None
        latest = _latest(conn, batch, timestamp)
        if latest is not None:
            _validate_capture(latest)
        document = capture_document(
            capture,
            registry_version=batch.registry_version,
            registry_sha256=batch.registry_sha256,
        )
        fingerprint = document_digest(document["scientific"])
        normalized = isinstance(capture.result, NormalizedDischargeCapture)
        reason = None
        if not normalized:
            reason = document["scientific"]["reason"]
        elif (
            isinstance(capture.result, NormalizedDischargeCapture)
            and before is not None
            and not _proven(latest, before)
            and (
                before["value"] != capture.result.canonical_value.value
                or before["rating_curve_id"] is not None
                or before["rating_curve_correction_version"] is not None
            )
        ):
            reason = "unproven_legacy_collision"
        if (
            latest is not None
            and latest["fingerprint"] == fingerprint
            and (
                (
                    reason is not None
                    and latest["reason"] == reason
                    and latest["before_db_sha256"] == _db_state(conn, before)
                )
                or (reason is None and _proven(latest, before))
            )
        ):
            return "unchanged", latest["event_id"]
        if reason is not None:
            outcome = "quarantined"
            after = None
        else:
            result = capture.result
            assert isinstance(result, NormalizedDischargeCapture)
            raw = next(
                obs
                for obs in batch.observations
                if obs.parameter == "discharge" and obs.timestamp == timestamp
            )
            if old is None:
                # A competing unguarded writer must not turn an absent-row
                # insertion into an unaudited historical overwrite.
                conn.execute(
                    sa.insert(observations).values(
                        id=uuid4(),
                        station_id=raw.station_id,
                        timestamp=raw.timestamp,
                        parameter=raw.parameter,
                        source=raw.source.value,
                        value=raw.value,
                        qc_status="raw",
                    )
                )
            else:
                PgObservationStore(conn).store_raw_observations([raw])
            # First evidence of l/s zero changes unit/context semantics even
            # though IEEE numeric equality alone would preserve legacy QC.
            if before is not None and (
                (
                    not _proven(latest, before)
                    and result.resolution.rule.unit
                    is DischargeSourceUnit.LITRES_PER_SECOND
                )
                or (
                    latest is not None
                    and _proven(latest, before)
                    and latest["scientific_unit"] != result.resolution.rule.unit.value
                )
            ):
                conn.execute(
                    sa.update(observations)
                    .where(
                        observations.c.id == before["id"],
                    )
                    .values(qc_status="raw", qc_flags=None, qc_rule_version=None)
                )
            assert timestamp is not None
            row = _row(conn, batch, timestamp)
            _assert_intended(row, raw)
            assert row is not None
            after = observation_state(row)
            outcome = (
                "inserted"
                if before is None
                else (
                    "restated"
                    if not _same_value_state(before, after)
                    else "evidence_only"
                )
            )
        if self._fault_point is not None:
            self._fault_point("after_value")
        event_id = conn.execute(
            sa.insert(events)
            .values(
                batch_id=batch.batch_id,
                tenant_id=batch.tenant_id,
                station_id=batch.station_id,
                measurement_time=timestamp,
                outcome=outcome,
                fingerprint=fingerprint,
                capture=document,
                observation_id=after["id"] if after else None,
                canonical_value=after["value"] if after else None,
                value_state_sha256=document_digest(value_state(after))
                if after
                else None,
                before_state=before if before is not None else sa.null(),
                after_state=after if after is not None else sa.null(),
                reason=reason,
            )
            .returning(events.c.event_id)
        ).scalar_one()
        if reason is not None and timestamp is not None:
            conn.execute(
                sa.insert(holds).values(
                    id=uuid4(),
                    tenant_id=batch.tenant_id,
                    station_id=batch.station_id,
                    starts_at=timestamp,
                    ends_at=timestamp + timedelta(microseconds=1),
                    reason=reason,
                    origin="ingest",
                    event_id=event_id,
                    registry_sha256=batch.registry_sha256,
                )
            )
        return outcome, event_id

    def _check_quota(self, conn: sa.Connection, batch: BafuStationCaptureBatch) -> None:
        scope = (
            safe_events.c.tenant_id == batch.tenant_id,
            safe_events.c.station_id == batch.station_id,
        )
        count, size = conn.execute(
            sa.select(
                sa.func.count(),
                sa.func.coalesce(sa.func.sum(safe_events.c.evidence_bytes), 0),
            ).where(*scope)
        ).one()
        receipt_count, receipt_size = conn.execute(
            sa.select(
                sa.func.count(),
                sa.func.coalesce(sa.func.sum(safe_receipts.c.evidence_bytes), 0),
            )
            .select_from(
                safe_receipts.join(
                    admissions, safe_receipts.c.batch_id == admissions.c.batch_id
                )
            )
            .where(
                admissions.c.tenant_id == batch.tenant_id,
                admissions.c.station_id == batch.station_id,
            )
        ).one()
        batch_size = conn.execute(
            sa.select(
                sa.func.coalesce(sa.func.sum(safe_events.c.evidence_bytes), 0)
            ).where(safe_events.c.batch_id == batch.batch_id)
        ).scalar_one()
        batch_receipt_size = conn.execute(
            sa.select(
                sa.func.coalesce(sa.func.sum(safe_receipts.c.evidence_bytes), 0)
            ).where(safe_receipts.c.batch_id == batch.batch_id)
        ).scalar_one()
        quota = self._quota
        if (
            count > quota.max_station_events
            or receipt_count > quota.max_station_receipts
            or size + receipt_size > quota.max_station_evidence_bytes
            or batch_size + batch_receipt_size > quota.max_batch_bytes
        ):
            raise BafuCapacityError(
                "station capture exceeds journal quota; entire batch rolled back"
            )
