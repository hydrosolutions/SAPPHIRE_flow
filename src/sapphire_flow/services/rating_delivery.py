from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import sqlalchemy as sa

from sapphire_flow.cli.import_dhm_delivery import build_delivery_curves
from sapphire_flow.db.metadata import (
    rating_delivery_curves,
    rating_delivery_files,
    stations,
)
from sapphire_flow.services.rating_conversion import convert_provisional_level
from sapphire_flow.store.audit_log_store import PgAuditLogStore
from sapphire_flow.store.rating_curve_store import PgRatingCurveStore
from sapphire_flow.store.tenant_store import PgTenantStore
from sapphire_flow.types.auth import AuditEntry
from sapphire_flow.types.enums import AuditEventType
from sapphire_flow.types.ids import StationId
from sapphire_flow.types.rating_delivery import select_rating_deliveries

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime
    from sapphire_flow.types.ids import TenantId
    from sapphire_flow.types.rating_delivery import RatingDeliveryFile


def admit_rating_deliveries(
    conn: sa.Connection,
    tenant_id: TenantId,
    files: tuple[RatingDeliveryFile, ...],
    *,
    now: UtcDatetime,
) -> int:
    """Caller owns a real transaction, including the audit. Never creates stations."""
    if (
        not conn.in_transaction()
        or conn.get_execution_options().get("isolation_level") == "AUTOCOMMIT"
    ):
        raise ValueError("rating admission requires a real transaction")
    select_rating_deliveries(files)
    PgTenantStore(conn).lock_tenant(tenant_id)
    curve_store = PgRatingCurveStore(conn)
    admitted = 0
    affected: set[StationId] = set()
    for item in files:
        matches = (
            conn.execute(
                sa.select(stations.c.id, stations.c.tenant_id).where(
                    stations.c.code == item.station_code,
                    stations.c.network == item.network,
                )
            )
            .mappings()
            .all()
        )
        if len(matches) != 1 or matches[0]["tenant_id"] != tenant_id:
            raise ValueError(
                f"missing, ambiguous or foreign station {item.station_code}"
            )
        station_id = StationId(matches[0]["id"])
        affected.add(station_id)
        prior = (
            conn.execute(
                sa.select(rating_delivery_files).where(
                    rating_delivery_files.c.station_id == station_id,
                    rating_delivery_files.c.delivery_id == item.delivery_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if prior is not None:
            if (
                prior["sha256"] != item.sha256
                or prior["delivered_on"] != item.delivered_on
            ):
                raise ValueError("conflicting rating delivery retry")
            continue
        conflict = conn.execute(
            sa.select(rating_delivery_files.c.id).where(
                rating_delivery_files.c.station_id == station_id,
                rating_delivery_files.c.delivered_on == item.delivered_on,
            )
        ).first()
        if conflict is not None:
            raise ValueError("ambiguous rating deliveries on same date")
        conn.execute(
            sa.insert(rating_delivery_files).values(
                id=uuid4(),
                tenant_id=tenant_id,
                station_id=station_id,
                delivery_id=item.delivery_id,
                delivered_on=item.delivered_on,
                sha256=item.sha256,
                original=item.original,
                admitted_at=now,
            )
        )
        admitted += 1
    # Store every original first. Import order never chooses conversion candidates.
    from sapphire_flow.adapters.dhm_files import parse_rating_tables

    for station_id in sorted(affected, key=str):
        winner = (
            conn.execute(
                sa.select(rating_delivery_files)
                .where(
                    rating_delivery_files.c.station_id == station_id,
                )
                .order_by(rating_delivery_files.c.delivered_on.desc())
                .limit(1)
            )
            .mappings()
            .one()
        )
        if (
            conn.execute(
                sa.select(rating_delivery_curves.c.file_id).where(
                    rating_delivery_curves.c.file_id == winner["id"],
                )
            ).first()
            is not None
        ):
            continue
        table = parse_rating_tables(bytes(winner["original"]).decode("utf-8-sig"))
        existing = curve_store.fetch_all_curves_for_station(station_id)
        highest = max((c.version for c in existing), default=0)
        blocks = sorted(table.blocks, key=lambda block: block.from_date)
        starts = [block.from_date for block in blocks]
        invalid_blocks: list[dict[str, str]] = []
        for ordinal, block in enumerate(blocks, start=highest):
            try:
                if starts.count(block.from_date) != 1:
                    raise ValueError("ambiguous tied rating blocks")
                curve = build_delivery_curves(
                    station_id,
                    replace(table, blocks=(block,)),
                    highest_surviving_version=ordinal,
                    now=now,
                )[0]
                convert_provisional_level(
                    cast("list[dict[str, float]]", curve.points)[0]["water_level"],  # pyright: ignore[reportUnknownMemberType]
                    curve,
                )
            except ValueError as exc:
                invalid_blocks.append(
                    {"valid_from": block.from_date.isoformat(), "reason": str(exc)}
                )
                continue
            curve = replace(curve, delivery_id=winner["delivery_id"])
            curve_store.store_rating_curve(curve)
            conn.execute(
                sa.insert(rating_delivery_curves).values(
                    file_id=winner["id"],
                    station_id=station_id,
                    rating_curve_id=curve.id,
                )
            )
        PgAuditLogStore(conn).append_entry(
            AuditEntry.system(  # pyright: ignore[reportUnknownMemberType]
                event_type=AuditEventType.DELIVERY_IMPORTED,
                target_type="station",
                target_id=str(station_id),
                detail={
                    "command": "rating-block-validation",
                    "file_sha256": winner["sha256"],
                    "invalid_blocks": invalid_blocks,
                    "blocks": len(blocks),
                },
                ip_address=None,
                created_at=now,
            )
        )

    PgAuditLogStore(conn).append_entry(
        AuditEntry.system(  # pyright: ignore[reportUnknownMemberType]
            event_type=AuditEventType.DELIVERY_IMPORTED,
            target_type="tenant",
            target_id=str(tenant_id),
            detail={"command": "ratings-only", "files_admitted": admitted},
            ip_address=None,
            created_at=now,
        )
    )
    return admitted
