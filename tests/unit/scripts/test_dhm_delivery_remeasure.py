from pathlib import Path

from scripts.dhm_delivery.remeasure import check_aggregates, measure_station


def test_synthetic_aggregate_and_check_are_value_free(tmp_path: Path) -> None:
    fixtures = Path(__file__).resolve().parents[2] / "fixtures/dhm"
    daily = (fixtures / "synthetic_daily_flow.txt").read_text()
    rating = (fixtures / "synthetic_rating_tables.txt").read_text()
    (tmp_path / "DFL_447.txt").write_text(daily.replace("Station: 999", "Station: 447"))
    (tmp_path / "RT_447.txt").write_text(
        rating.replace("Station No: 999", "Station No: 447")
    )
    aggregate = measure_station(tmp_path, "447")
    assert aggregate.observations == 3
    assert aggregate.curves == 2
    assert aggregate.curve_less_days == 0
    assert aggregate.gap_runs == ((aggregate.first_day.replace(day=3), 728),)
    assert aggregate.curve_windows[0][2] == 2
    assert aggregate.one_decimal_percent > 0
    failures = check_aggregates([aggregate])
    assert "daily observation count" in failures
    assert "rating curve count" in failures
    assert all("1.5" not in failure for failure in failures)


def test_precision_ignores_accepted_trailing_spaces(tmp_path: Path) -> None:
    fixtures = Path(__file__).resolve().parents[2] / "fixtures/dhm"
    daily = (fixtures / "synthetic_daily_flow.txt").read_text()
    rating = (fixtures / "synthetic_rating_tables.txt").read_text()
    (tmp_path / "DFL_447.txt").write_text(
        daily.replace("Station: 999", "Station: 447").replace("1.5\n", "1.5  \n")
    )
    (tmp_path / "RT_447.txt").write_text(
        rating.replace("Station No: 999", "Station No: 447")
    )

    aggregate = measure_station(tmp_path, "447")

    assert aggregate.one_decimal_percent == 100 / 3


def test_equal_start_curve_uses_later_block(tmp_path: Path) -> None:
    fixtures = Path(__file__).resolve().parents[2] / "fixtures/dhm"
    daily = (fixtures / "synthetic_daily_flow.txt").read_text()
    rating = (fixtures / "synthetic_rating_tables.txt").read_text()
    (tmp_path / "DFL_447.txt").write_text(daily.replace("Station: 999", "Station: 447"))
    (tmp_path / "RT_447.txt").write_text(
        rating.replace("Station No: 999", "Station No: 447").replace(
            "From Date = 01-Jan-1987", "From Date = 01-Jan-1985"
        )
    )

    aggregate = measure_station(tmp_path, "447")

    assert aggregate.outside_curve_range_days == 2
