from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from sapphire_flow.adapters.bafu_thresholds import (
    fetch_threshold_page,
    parse_threshold_page,
)
from sapphire_flow.types.datetime import ensure_utc

NOW = ensure_utc(datetime(2026, 11, 24, tzinfo=UTC))
HTML = Path("tests/fixtures/bafu_thresholds/2150.html").read_text()


class TestParseThresholdPage:
    def test_reads_labelled_bounds(self) -> None:
        result = parse_threshold_page(HTML, station_code="2150", retrieved_at=NOW)
        assert result.bounds == (170, 280, 350, 440)
        assert result.retrieved_at == NOW

    def test_litres_are_normalized_to_cubic_metres(self) -> None:
        html = HTML.replace("Abfluss m³/s", "Abfluss l/s")
        result = parse_threshold_page(html, station_code="2150", retrieved_at=NOW)
        assert result.bounds == (0.17, 0.28, 0.35, 0.44)
        assert result.html == html

    @pytest.mark.parametrize(
        ("old", "new", "error"),
        [
            ("2150", "2305", "identity"),
            ("170 - 280", "170 - 281", "contiguous"),
            ("Abfluss m³/s", "Wasserstand m ü.M.", "unsupported"),
            ("350 - 440", "350 - NaN", "malformed"),
            (
                "<h4>Gefahrenstufen</h4>",
                "<h4>Gefahrenstufen</h4><h4>Gefahrenstufen</h4>",
                "ambiguous",
            ),
        ],
    )
    def test_rejects_bad_source(self, old: str, new: str, error: str) -> None:
        with pytest.raises(ValueError, match=error):
            parse_threshold_page(
                HTML.replace(old, new), station_code="2150", retrieved_at=NOW
            )

    def test_missing_is_distinct_from_invalid(self) -> None:
        result = parse_threshold_page(
            "<h1>Landquart 2150</h1>", station_code="2150", retrieved_at=NOW
        )
        assert result.bounds is None


class TestFetchThresholdPage:
    def test_rejects_redirect(self) -> None:
        with (
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(
                        302, headers={"Location": "https://example.org"}
                    )
                )
            ) as client,
            pytest.raises(httpx.HTTPStatusError, match="302"),
        ):
            fetch_threshold_page(client, station_code="2150", retrieved_at=NOW)

    def test_rejects_non_station_code_before_request(self) -> None:
        with (
            httpx.Client() as client,
            pytest.raises(ValueError, match="four digits"),
        ):
            fetch_threshold_page(client, station_code="../2150", retrieved_at=NOW)
