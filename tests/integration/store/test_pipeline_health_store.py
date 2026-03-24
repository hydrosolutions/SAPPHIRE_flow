from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sapphire_flow.store.pipeline_health_store import PgPipelineHealthStore
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import PipelineCheckType, PipelineHealthStatus
from sapphire_flow.types.pipeline import PipelineHealthRecord

if TYPE_CHECKING:
    import sqlalchemy as sa


def _make_record(
    check_type: PipelineCheckType = PipelineCheckType.NWP_DELIVERY,
    status: PipelineHealthStatus = PipelineHealthStatus.OK,
    subject: str = "nwp_icon",
    checked_at_hour: int = 0,
    detail: dict | None = None,  # type: ignore[type-arg]
    cycle_time_hour: int | None = None,
) -> PipelineHealthRecord:
    checked_at = ensure_utc(datetime(2026, 1, 1, checked_at_hour, tzinfo=UTC))
    cycle_time = (
        ensure_utc(datetime(2026, 1, 1, cycle_time_hour, tzinfo=UTC))
        if cycle_time_hour is not None
        else None
    )
    return PipelineHealthRecord(
        check_type=check_type,
        checked_at=checked_at,
        status=status,
        subject=subject,
        detail=detail or {},
        cycle_time=cycle_time,
        created_at=checked_at,
    )


class TestPgPipelineHealthStore:
    def test_append_and_fetch(self, db_connection: sa.Connection) -> None:
        store = PgPipelineHealthStore(db_connection)
        record = _make_record(detail={"lag_minutes": 15})
        store.append_health_record(record)

        results = store.fetch_recent()

        assert len(results) == 1
        r = results[0]
        assert r.check_type == PipelineCheckType.NWP_DELIVERY
        assert r.status == PipelineHealthStatus.OK
        assert r.subject == "nwp_icon"
        assert r.detail == {"lag_minutes": 15}
        assert r.cycle_time is None

    def test_fetch_recent_ordering(self, db_connection: sa.Connection) -> None:
        store = PgPipelineHealthStore(db_connection)
        store.append_health_record(_make_record(checked_at_hour=1))
        store.append_health_record(_make_record(checked_at_hour=3))
        store.append_health_record(_make_record(checked_at_hour=2))

        results = store.fetch_recent()

        assert len(results) == 3
        hours = [r.checked_at.hour for r in results]
        assert hours == [3, 2, 1]

    def test_fetch_recent_filter_by_type(self, db_connection: sa.Connection) -> None:
        store = PgPipelineHealthStore(db_connection)
        store.append_health_record(
            _make_record(check_type=PipelineCheckType.NWP_DELIVERY, checked_at_hour=1)
        )
        store.append_health_record(
            _make_record(
                check_type=PipelineCheckType.OBSERVATION_FRESHNESS, checked_at_hour=2
            )
        )
        store.append_health_record(
            _make_record(check_type=PipelineCheckType.NWP_DELIVERY, checked_at_hour=3)
        )

        results = store.fetch_recent(check_type=PipelineCheckType.NWP_DELIVERY)

        assert len(results) == 2
        assert all(r.check_type == PipelineCheckType.NWP_DELIVERY for r in results)

    def test_fetch_recent_limit(self, db_connection: sa.Connection) -> None:
        store = PgPipelineHealthStore(db_connection)
        for hour in range(5):
            store.append_health_record(_make_record(checked_at_hour=hour))

        results = store.fetch_recent(limit=2)

        assert len(results) == 2
        assert results[0].checked_at.hour == 4
        assert results[1].checked_at.hour == 3
