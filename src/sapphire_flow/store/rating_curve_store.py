# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from sapphire_flow.db.metadata import rating_curves
from sapphire_flow.store._helpers import utc_from_row, utc_or_none
from sapphire_flow.types.enums import InterpolationMethod
from sapphire_flow.types.ids import RatingCurveId, StationId
from sapphire_flow.types.rating_curve import RatingCurve

if TYPE_CHECKING:
    from sapphire_flow.types.datetime import UtcDatetime


class PgRatingCurveStore:
    def __init__(self, conn: sa.Connection) -> None:
        self._conn = conn

    def store_rating_curve(self, curve: RatingCurve) -> RatingCurveId:
        self._conn.execute(
            sa.insert(rating_curves).values(
                id=curve.id,
                station_id=curve.station_id,
                version=curve.version,
                valid_from=curve.valid_from,
                valid_to=curve.valid_to,
                points=curve.points,
                interpolation=curve.interpolation.value,
                uploaded_by=curve.uploaded_by,
                created_at=curve.created_at,
            )
        )
        return curve.id

    def fetch_active_curve(self, station_id: StationId) -> RatingCurve | None:
        row = (
            self._conn.execute(
                sa.select(rating_curves).where(
                    sa.and_(
                        rating_curves.c.station_id == station_id,
                        rating_curves.c.valid_to.is_(None),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return _row_to_curve(row) if row is not None else None

    def fetch_curve_at(
        self, station_id: StationId, at: UtcDatetime
    ) -> RatingCurve | None:
        row = (
            self._conn.execute(
                sa.select(rating_curves).where(
                    sa.and_(
                        rating_curves.c.station_id == station_id,
                        rating_curves.c.valid_from <= at,
                        sa.or_(
                            rating_curves.c.valid_to.is_(None),
                            rating_curves.c.valid_to > at,
                        ),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return _row_to_curve(row) if row is not None else None

    def supersede_curve(self, curve_id: RatingCurveId, valid_to: UtcDatetime) -> None:
        self._conn.execute(
            sa.update(rating_curves)
            .where(rating_curves.c.id == curve_id)
            .values(valid_to=valid_to)
        )

    def fetch_curves_in_range(
        self, station_id: StationId, start: UtcDatetime, end: UtcDatetime
    ) -> list[RatingCurve]:
        rows = (
            self._conn.execute(
                sa.select(rating_curves)
                .where(
                    sa.and_(
                        rating_curves.c.station_id == station_id,
                        # Half-open overlap of curve [valid_from, valid_to) with
                        # query [start, end): valid_from < end AND valid_to > start
                        # (valid_to NULL = active/unbounded). Repo range convention
                        # is half-open (conventions.md) — an adjacent curve touching
                        # at a boundary does NOT overlap.
                        rating_curves.c.valid_from < end,
                        sa.or_(
                            rating_curves.c.valid_to.is_(None),
                            rating_curves.c.valid_to > start,
                        ),
                    )
                )
                .order_by(rating_curves.c.valid_from)
            )
            .mappings()
            .all()
        )
        return [_row_to_curve(row) for row in rows]

    def fetch_active_curves_batch(
        self, station_ids: list[StationId]
    ) -> dict[StationId, RatingCurve]:
        if not station_ids:
            return {}
        rows = (
            self._conn.execute(
                sa.select(rating_curves).where(
                    sa.and_(
                        rating_curves.c.station_id.in_(station_ids),
                        rating_curves.c.valid_to.is_(None),
                    )
                )
            )
            .mappings()
            .all()
        )
        curves = [_row_to_curve(row) for row in rows]
        return {c.station_id: c for c in curves}

    def fetch_active_curves_batch_at(
        self, station_ids: list[StationId], at: UtcDatetime
    ) -> dict[StationId, RatingCurve]:
        if not station_ids:
            return {}
        rows = (
            self._conn.execute(
                sa.select(rating_curves)
                .where(
                    sa.and_(
                        rating_curves.c.station_id.in_(station_ids),
                        # Curve active at `at`: valid_from <= at < valid_to
                        # (valid_to NULL = unbounded). Mirrors fetch_curve_at.
                        rating_curves.c.valid_from <= at,
                        sa.or_(
                            rating_curves.c.valid_to.is_(None),
                            rating_curves.c.valid_to > at,
                        ),
                    )
                )
                # Deterministic last-wins per station if curves ever overlap:
                # the latest valid_from is applied last.
                .order_by(rating_curves.c.valid_from)
            )
            .mappings()
            .all()
        )
        return {(c := _row_to_curve(row)).station_id: c for row in rows}


def _row_to_curve(row: sa.engine.row.RowMapping) -> RatingCurve:
    return RatingCurve(
        id=RatingCurveId(row["id"]),
        station_id=StationId(row["station_id"]),
        version=row["version"],
        valid_from=utc_from_row(row["valid_from"]),
        valid_to=utc_or_none(row["valid_to"]),
        points=list(row["points"]),
        interpolation=InterpolationMethod(row["interpolation"]),
        uploaded_by=row["uploaded_by"],
        created_at=utc_from_row(row["created_at"]),
    )
