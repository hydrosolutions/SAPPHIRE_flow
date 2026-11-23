from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

from sapphire_flow.services.fine_tuning_evidence import RecordingReanalysisSource
from tests.conftest import make_raw_historical_forcing


class ReturnedRows:
    def __init__(self, rows: list) -> None:
        self.rows = rows

    def fetch_reanalysis(self, *args: object) -> list:
        return self.rows


def test_preliminary_dates_are_explicit_with_gaps_and_repeated_reads() -> None:
    first = make_raw_historical_forcing(source="meteoswiss-rprelimd", version="v1")
    third = replace(first, valid_time=first.valid_time + timedelta(days=2))
    source = RecordingReanalysisSource(ReturnedRows([first, third]))
    for _ in range(2):
        assert (
            len(
                source.fetch_reanalysis(
                    [], first.valid_time, third.valid_time, ["precipitation"]
                )
            )
            == 2
        )
    report = json.loads(source.snapshot())
    assert report["returned_rows"] == 4
    assert report["products"][0]["utc_dates_with_rows"] == [
        first.valid_time.date().isoformat(),
        third.valid_time.date().isoformat(),
    ]
    assert "repeated" in report["count_semantics"]
    source.reset()
    assert json.loads(source.snapshot())["products"] == []
