from datetime import UTC, date, datetime

from sapphire_flow.adapters.nepal_local_day import nepal_day_start, nepal_local_date
from sapphire_flow.types.datetime import ensure_utc


def test_historical_kathmandu_day_boundaries() -> None:
    assert nepal_day_start(date(1970, 1, 1)) == datetime(
        1969, 12, 31, 18, 30, tzinfo=UTC
    )
    assert nepal_day_start(date(1986, 1, 1)) == datetime(
        1985, 12, 31, 18, 30, tzinfo=UTC
    )
    assert nepal_day_start(date(1986, 1, 2)) == datetime(1986, 1, 1, 18, 15, tzinfo=UTC)
    assert nepal_day_start(date(2000, 1, 1)) == datetime(
        1999, 12, 31, 18, 15, tzinfo=UTC
    )


def test_local_date_returns_day_across_the_1986_jump() -> None:
    first = nepal_day_start(date(1986, 1, 1))
    second = nepal_day_start(date(1986, 1, 2))
    assert nepal_local_date(first) == date(1986, 1, 1)
    assert nepal_local_date(second) == date(1986, 1, 2)
    assert second - first != datetime(1986, 1, 2, tzinfo=UTC) - datetime(
        1986, 1, 1, tzinfo=UTC
    )
    assert nepal_local_date(
        ensure_utc(datetime(1985, 12, 31, 18, 45, tzinfo=UTC))
    ) == date(1986, 1, 1)
