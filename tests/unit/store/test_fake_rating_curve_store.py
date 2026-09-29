from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from sapphire_flow.adapters.dhm_files import (
    DhmFileFormatError,
    RatingPoint,
    parse_rating_tables,
)
from sapphire_flow.cli.import_dhm_delivery import DELIVERY_ID, build_delivery_curves
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ids import RatingCurveId, StationId
from tests.fakes.fake_stores import FakeRatingCurveStore

_RATING_FIXTURE = (
    Path(__file__).resolve().parents[2] / "fixtures/dhm/synthetic_rating_tables.txt"
)


def test_delivery_curves_are_bounded_and_numbered_after_survivors() -> None:
    source = parse_rating_tables(_RATING_FIXTURE.read_text())
    station_id = StationId(uuid4())
    now = ensure_utc(datetime(2026, 9, 28, tzinfo=UTC))
    curves = build_delivery_curves(
        station_id, source, highest_surviving_version=4, now=now
    )
    assert [curve.version for curve in curves] == [5, 6]
    assert curves[0].valid_from == datetime(1984, 12, 31, 18, 30, tzinfo=UTC)
    assert curves[0].valid_to == datetime(1985, 12, 31, 18, 30, tzinfo=UTC)
    assert all(curve.valid_to is not None for curve in curves)
    assert all(curve.delivery_id == DELIVERY_ID for curve in curves)
    assert [curve.rating_type_label for curve in curves] == ["3", "3"]


def test_overlap_uses_latest_valid_from_and_delivery_delete_is_scoped() -> None:
    source = parse_rating_tables(_RATING_FIXTURE.read_text())
    station_id = StationId(uuid4())
    now = ensure_utc(datetime(2026, 9, 28, tzinfo=UTC))
    older = build_delivery_curves(
        station_id, source, highest_surviving_version=0, now=now
    )[0]
    newer = replace(
        older,
        id=RatingCurveId(uuid4()),
        version=2,
        valid_from=ensure_utc(datetime(1985, 6, 1, tzinfo=UTC)),
    )
    unrelated = replace(
        older,
        id=RatingCurveId(uuid4()),
        version=3,
        delivery_id=None,
        valid_from=ensure_utc(datetime(1990, 1, 1, tzinfo=UTC)),
        valid_to=ensure_utc(datetime(1990, 12, 31, tzinfo=UTC)),
    )
    store = FakeRatingCurveStore()
    for curve in (older, newer, unrelated):
        store.store_rating_curve(curve)
    assert (
        store.fetch_curve_at(station_id, ensure_utc(datetime(1985, 6, 2, tzinfo=UTC)))
        == newer
    )
    assert store.delete_delivery_curves(DELIVERY_ID, [station_id]) == 2
    assert store.fetch_delivery_curves(DELIVERY_ID, [station_id]) == []
    assert (
        store.fetch_curve_at(station_id, ensure_utc(datetime(1990, 1, 2, tzinfo=UTC)))
        == unrelated
    )


def test_invalid_curve_error_does_not_disclose_delivered_values() -> None:
    source = parse_rating_tables(_RATING_FIXTURE.read_text())
    invalid = replace(
        source.blocks[0],
        points=(
            RatingPoint(stage_m=0.0, discharge_m3s=9999.0),
            RatingPoint(stage_m=1.0, discharge_m3s=1111.0),
        ),
    )
    with pytest.raises(DhmFileFormatError, match="invalid for linear") as exc:
        build_delivery_curves(
            StationId(uuid4()),
            replace(source, blocks=(invalid,)),
            highest_surviving_version=0,
            now=ensure_utc(datetime(2026, 9, 28, tzinfo=UTC)),
        )
    assert "9999" not in str(exc.value)
    assert "1111" not in str(exc.value)
