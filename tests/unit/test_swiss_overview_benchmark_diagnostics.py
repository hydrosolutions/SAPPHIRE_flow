import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.swiss_overview_benchmark_diagnostics import record_then_assert_budgets

EXPECTED_KEYS = {
    "case",
    "profiled",
    "stations",
    "horizon_steps",
    "forecast_value_rows",
    "observation_rows",
    "bafu_files",
    "archive_names",
    "archive_enumerations",
    "raw_evidence_files",
    "raw_bytes",
    "gzip_bytes",
    "members",
    "quantiles",
    "time_step_seconds",
    "p95_seconds",
    "route_seconds",
    "serialization_seconds",
    "samples_seconds",
    "queries",
    "latency_target_met",
    "raw_payload_target_met",
}


def _production_record() -> dict[str, object]:
    samples = [0.5] * 10
    return {
        "case": "production_quantiles",
        "profiled": False,
        "stations": 134,
        "horizon_steps": 10,
        "forecast_value_rows": 9380,
        "observation_rows": 670,
        "bafu_files": 54,
        "archive_names": 36944,
        "archive_enumerations": 10,
        "raw_evidence_files": 54,
        "raw_bytes": 300000,
        "gzip_bytes": 10000,
        "members": None,
        "quantiles": [0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95],
        "time_step_seconds": 86400.0,
        "p95_seconds": 0.5,
        "route_seconds": samples,
        "serialization_seconds": [0.01] * 10,
        "samples_seconds": samples,
        "queries": [12] * 10,
        "latency_target_met": True,
        "raw_payload_target_met": True,
    }


def _stress_record() -> dict[str, object]:
    record = _production_record()
    record.update(
        {
            "case": "stress_members",
            "profiled": True,
            "horizon_steps": 72,
            "forecast_value_rows": 192960,
            "members": 20,
            "quantiles": None,
            "time_step_seconds": 3600.0,
            "p95_seconds": 1.5,
            "route_seconds": [1.5] * 3,
            "serialization_seconds": [0.1] * 3,
            "samples_seconds": [1.5] * 3,
            "queries": [12] * 3,
            "latency_target_met": None,
        }
    )
    return record


class TestRecordThenAssertBudgets:
    @pytest.mark.parametrize("record_factory", [_production_record, _stress_record])
    def test_accepts_both_complete_case_records(
        self, tmp_path: Path, record_factory: Callable[[], dict[str, object]]
    ) -> None:
        target = tmp_path / "benchmark.json"
        record_then_assert_budgets(record_factory(), target)
        assert set(json.loads(target.read_text())) == EXPECTED_KEYS

    @pytest.mark.parametrize(
        ("mutation", "message", "measured"),
        [
            ({"queries": [13] * 10}, "query budget", "queries=13 limit=12"),
            (
                {"raw_bytes": 393217, "raw_payload_target_met": False},
                "payload budget",
                "raw_bytes=393217 limit=393216",
            ),
            (
                {"p95_seconds": 1.25, "latency_target_met": False},
                "latency budget",
                "p95_seconds=1.25 limit=1.25",
            ),
        ],
    )
    def test_writes_full_allowlist_before_each_budget_failure(
        self, tmp_path: Path, mutation: dict[str, object], message: str, measured: str
    ) -> None:
        record = _production_record()
        record.update(mutation)
        target = tmp_path / "benchmark.json"

        with pytest.raises(AssertionError, match=message) as caught:
            record_then_assert_budgets(record, target)
        assert measured in str(caught.value)

        persisted = json.loads(target.read_text())
        assert set(persisted) == EXPECTED_KEYS
        assert persisted == record

    @pytest.mark.parametrize("seconds", [1.0, 1.09578, 1.249999])
    def test_production_latency_below_new_limit_passes(
        self, tmp_path: Path, seconds: float
    ) -> None:
        record = _production_record()
        record["p95_seconds"] = seconds
        record_then_assert_budgets(record, tmp_path / "benchmark.json")

    def test_unstable_query_counts_write_before_failure(self, tmp_path: Path) -> None:
        record = _production_record()
        record["queries"] = [11, *([12] * 9)]
        target = tmp_path / "benchmark.json"
        with pytest.raises(AssertionError, match="stable") as caught:
            record_then_assert_budgets(record, target)
        assert repr(record["queries"]) in str(caught.value)
        assert set(json.loads(target.read_text())) == EXPECTED_KEYS

    @pytest.mark.parametrize(
        ("key", "value", "message"),
        [
            ("route_seconds", [float("nan")] * 10, "route_seconds"),
            ("route_seconds", [float("inf")] * 10, "route_seconds"),
            ("route_seconds", [-1.0] * 10, "route_seconds"),
            ("stations", True, "stations"),
            ("queries", [True] * 10, "queries"),
            ("route_seconds", [True] * 10, "route_seconds"),
            ("queries", [12] * 9, "queries"),
            ("samples_seconds", [0.5] * 9, "samples_seconds"),
            ("profiled", True, "unprofiled"),
            ("latency_target_met", False, "latency_target_met"),
            ("raw_payload_target_met", False, "raw_payload_target_met"),
            ("quantiles", [0.05], "members and quantiles"),
        ],
    )
    def test_validation_errors_write_nothing(
        self, tmp_path: Path, key: str, value: object, message: str
    ) -> None:
        record = _production_record()
        record[key] = value
        target = tmp_path / "benchmark.json"

        with pytest.raises(ValueError, match=message):
            record_then_assert_budgets(record, target)

        assert not target.exists()

    def test_missing_and_extra_keys_write_nothing(self, tmp_path: Path) -> None:
        missing = _production_record()
        missing.pop("gzip_bytes")
        extra = _production_record()
        extra["scope"] = "must not be persisted"
        for index, record in enumerate((missing, extra)):
            target = tmp_path / f"benchmark-{index}.json"
            with pytest.raises(ValueError, match="diagnostic keys"):
                record_then_assert_budgets(record, target)
            assert not target.exists()

    @pytest.mark.parametrize(
        ("key", "value", "message"),
        [
            ("members", None, "members and quantiles"),
            ("profiled", "yes", "profiled"),
            ("latency_target_met", True, "latency_target_met"),
            ("route_seconds", [1.5] * 10, "exactly 3"),
        ],
    )
    def test_stress_case_rejects_bad_types_counts_and_flags(
        self, tmp_path: Path, key: str, value: object, message: str
    ) -> None:
        record = _stress_record()
        record[key] = value
        target = tmp_path / "stress.json"
        with pytest.raises(ValueError, match=message):
            record_then_assert_budgets(record, target)
        assert not target.exists()

    def test_none_target_validates_and_does_not_write(self, tmp_path: Path) -> None:
        record_then_assert_budgets(_production_record(), None)
        assert list(tmp_path.iterdir()) == []
