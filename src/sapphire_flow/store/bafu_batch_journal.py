from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import psycopg
import sqlalchemy as sa

from sapphire_flow.db.metadata import bafu_unit_batch_admissions as admissions
from sapphire_flow.db.metadata import stations
from sapphire_flow.types.bafu_capture_batch import (
    BafuAdmissionDecision,
    BafuAdmissionDisposition,
    BafuBatchAdmission,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from sapphire_flow.types.bafu_capture_batch import BafuStationCaptureBatch


class BafuBatchIntegrityError(ValueError):
    pass


class BafuBatchBindingError(ValueError):
    pass


def advisory_key(identity: str) -> int:
    return int.from_bytes(
        hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True
    )


def lock_bafu_batch(conn: sa.Connection, batch: BafuStationCaptureBatch) -> None:
    driver = conn.connection.driver_connection
    if not isinstance(driver, psycopg.Connection) or driver.autocommit:
        raise ValueError("BAFU journal requires a non-AUTOCOMMIT psycopg connection")
    if conn.get_isolation_level() != "READ COMMITTED":
        raise ValueError("BAFU journal requires READ COMMITTED isolation")
    # Psycopg row decoding must retain native float8 values for document proofs.
    conn.execute(sa.text("SET LOCAL extra_float_digits = 3"))
    conn.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    conn.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
    conn.execute(
        sa.text("SELECT set_config('sapphire.bafu_unit_batch', :batch, true)"),
        {"batch": str(batch.batch_id)},
    )
    batch_lock = advisory_key(f"bafu-unit-batch-v1:{batch.batch_id}")
    conn.execute(
        sa.text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": batch_lock},
    )
    keys = {advisory_key(f"bafu-unit-station-v1:{batch.tenant_id}:{batch.station_id}")}
    keys.update(
        advisory_key(
            f"bafu-unit-observation-v1:{obs.station_id}:{obs.timestamp.isoformat()}:{obs.parameter}:{obs.source.value}"
        )
        for obs in batch.observations
    )
    keys.update(
        advisory_key(
            f"bafu-unit-observation-v1:{batch.station_id}:{capture.result.measurement_time.isoformat()}:discharge:measured"
        )
        for capture in batch.captures
        if capture.result.measurement_time is not None
    )
    keys.discard(batch_lock)
    for key in sorted(keys):
        conn.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    contexts = {(batch.station_id, "discharge")}
    contexts.update((obs.station_id, obs.parameter) for obs in batch.observations)
    for station_id, parameter in sorted(contexts):
        conn.execute(
            sa.text("SELECT public.observation_qc_lock(:station, :parameter)"),
            {"station": station_id, "parameter": parameter},
        )


def validate_bafu_binding(conn: sa.Connection, batch: BafuStationCaptureBatch) -> None:
    row = conn.execute(
        sa.select(
            stations.c.tenant_id,
            stations.c.network,
            stations.c.code,
            stations.c.station_kind,
        )
        .where(stations.c.id == batch.station_id)
        .with_for_update(read=True)
    ).first()
    if row is None or tuple(row) != (
        batch.tenant_id,
        "bafu",
        batch.key.gauge.value,
        batch.key.kind,
    ):
        raise BafuBatchBindingError(
            "persisted BAFU station binding differs from capture"
        )


class PgBafuBatchJournal:
    def __init__(
        self,
        transaction_factory: Callable[[], AbstractContextManager[sa.Connection]],
    ) -> None:
        self._transaction_factory = transaction_factory

    def admit(self, batch: BafuStationCaptureBatch) -> BafuBatchAdmission:
        with self._transaction_factory() as conn:
            lock_bafu_batch(conn, batch)
            validate_bafu_binding(conn, batch)
            existing = (
                conn.execute(
                    sa.select(admissions).where(admissions.c.batch_id == batch.batch_id)
                )
                .mappings()
                .first()
            )
            latest = (
                conn.execute(
                    sa.select(admissions)
                    .where(
                        admissions.c.tenant_id == batch.tenant_id,
                        admissions.c.station_id == batch.station_id,
                        admissions.c.disposition
                        == BafuAdmissionDisposition.ADMITTED.value,
                    )
                    .order_by(admissions.c.sequence.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if existing is not None:
                if (
                    existing["input_digest"] != batch.input_digest
                    or existing["tenant_id"] != batch.tenant_id
                    or existing["station_id"] != batch.station_id
                ):
                    raise BafuBatchIntegrityError(
                        "batch ID reused with different captured input"
                    )
                if (
                    BafuAdmissionDisposition(existing["disposition"])
                    is BafuAdmissionDisposition.REJECTED
                ):
                    return BafuBatchAdmission(
                        batch_id=batch.batch_id,
                        decision=BafuAdmissionDecision.SUPERSEDED,
                        sequence=None,
                    )
                decision = (
                    BafuAdmissionDecision.SUPERSEDED
                    if latest is not None and latest["sequence"] > existing["sequence"]
                    else BafuAdmissionDecision.RETRY
                )
                return BafuBatchAdmission(
                    batch_id=batch.batch_id,
                    decision=decision,
                    sequence=existing["sequence"],
                )
            disposition = (
                BafuAdmissionDisposition.REJECTED
                if latest is not None and latest["received_at"] >= batch.received_at
                else BafuAdmissionDisposition.ADMITTED
            )
            sequence = conn.execute(
                sa.insert(admissions)
                .values(
                    batch_id=batch.batch_id,
                    disposition=disposition.value,
                    tenant_id=batch.tenant_id,
                    station_id=batch.station_id,
                    gauge_code=batch.key.gauge.value,
                    lindas_kind=batch.key.kind,
                    received_at=batch.received_at,
                    registry_version=batch.registry_version,
                    registry_sha256=batch.registry_sha256,
                    input_digest=batch.input_digest,
                )
                .returning(admissions.c.sequence)
            ).scalar_one()
            return BafuBatchAdmission(
                batch_id=batch.batch_id,
                decision=BafuAdmissionDecision.ADMITTED
                if disposition is BafuAdmissionDisposition.ADMITTED
                else BafuAdmissionDecision.SUPERSEDED,
                sequence=sequence
                if disposition is BafuAdmissionDisposition.ADMITTED
                else None,
            )
