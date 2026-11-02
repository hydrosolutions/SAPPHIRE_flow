from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.pool import NullPool

from sapphire_flow.db.metadata import (
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
from sapphire_flow.store.observation_store import PgObservationStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.station_store import PgStationStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.enums import AuditEventType, ObservationSource
from sapphire_flow.types.rating_derived import NativeFeedCustody
from sapphire_flow.types.rating_reference import RatingReferenceProof

if TYPE_CHECKING:
    from sapphire_flow.config.dhm import DhmConfig
    from sapphire_flow.config.provisional_rating import NativeRatingBinding
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.rating_derived import RatingDerivedDischarge

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
        with self._transaction() as conn:
            records = self._current(conn, binding, dhm, start=start, end=end, at=at)
            inserted = rejected_inserted = 0
            for record in records:
                values = asdict(record)
                values.pop("warnings")
                result = conn.execute(
                    insert(rating_derived_discharge)
                    .values(
                        **values,
                        fingerprint=record.fingerprint,
                        captured_at=at,
                    )
                    .on_conflict_do_nothing(index_elements=["fingerprint"])
                    .returning(rating_derived_discharge.c.fingerprint)
                )
                added = result.scalar_one_or_none() is not None
                inserted += added
                rejected_inserted += added and record.rejection is not None
            if inserted:
                PgAuditLogStore(conn).append_entry(
                    AuditEntry.system(  # pyright: ignore[reportUnknownMemberType]
                        event_type=AuditEventType.DELIVERY_IMPORTED,
                        target_type="station",
                        target_id=str(binding.station_id),
                        detail={
                            "command": "rating-derived-reconcile",
                            "inserted": inserted,
                            "rejected": rejected_inserted,
                            "policy": "native-rating-linear-endpoints-v1",
                        },
                        ip_address=None,
                        created_at=at,
                    )
                )
            return records

    def read_current(
        self,
        binding: NativeRatingBinding,
        dhm: DhmConfig,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
    ) -> tuple[RatingDerivedDischarge, ...]:
        with self._transaction() as conn:
            current = self._current(conn, binding, dhm, start=start, end=end, at=at)
            fingerprints = [record.fingerprint for record in current]
            stored = set(
                conn.execute(
                    sa.select(rating_derived_discharge.c.fingerprint).where(
                        rating_derived_discharge.c.fingerprint.in_(fingerprints),
                    )
                ).scalars()
            )
            return tuple(record for record in current if record.fingerprint in stored)

    @staticmethod
    def _current(
        conn: sa.Connection,
        binding: NativeRatingBinding,
        dhm: DhmConfig,
        *,
        start: UtcDatetime,
        end: UtcDatetime,
        at: UtcDatetime,
    ) -> tuple[RatingDerivedDischarge, ...]:
        if not binding.custody_start <= start < end <= binding.custody_end:
            raise ValueError("requested interval lacks documented native feed custody")
        PgTenantStore(conn).lock_tenant(binding.tenant_id)
        station = PgStationStore(conn).fetch_station(binding.station_id)
        if (
            station is None
            or station.tenant_id != binding.tenant_id
            or station.code != binding.station_code
            or station.network != "dhm"
        ):
            raise ValueError("native rating station identity mismatch")
        matches = [item for item in dhm.bindings if item.station_code == station.code]
        reference = binding.reference
        if len(matches) != 1 or (dhm.endpoint, matches[0].api_station_id) != (
            reference.endpoint,
            reference.api_station_id,
        ):
            raise ValueError("native feed differs from exact reference binding")
        level_reference = matches[0].level_reference
        if isinstance(reference, RatingReferenceProof) and (
            level_reference != reference.level_reference
        ):
            raise ValueError("native feed datum differs from exact reference binding")
        compatible_unit = (
            station.water_level_unit in ("m", "m a.s.l.")
            if level_reference == "masl"
            else station.water_level_unit == "m"
            and station.water_level_datum_masl is None
        )
        if not compatible_unit:
            raise ValueError("native feed requires compatible metre unit and datum")
        selected = (
            conn.execute(
                sa.select(rating_delivery_files)
                .where(
                    rating_delivery_files.c.station_id == binding.station_id,
                    rating_delivery_files.c.tenant_id == binding.tenant_id,
                )
                .order_by(rating_delivery_files.c.delivered_on.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if selected is None:
            raise ValueError("no admitted rating delivery")
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
            raise ValueError("rating chunk exceeds 1000 source observations")
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
        result: list[RatingDerivedDischarge] = []
        for observation in rows:
            if observation.id not in locked_ids:
                continue
            starts = [
                nepal_day_start(block.from_date)
                for block in original.blocks
                if nepal_day_start(block.from_date) <= observation.timestamp
            ]
            if not starts:
                raise ValueError("selected delivery has no eligible historical block")
            selected_start = max(starts)
            if starts.count(selected_start) != 1 or not any(
                c.valid_from == selected_start for c in curves
            ):
                raise ValueError(
                    "selected historical rating block is invalid; no fallback"
                )
            curve = select_eligible_rating(curves, observation.timestamp)
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
