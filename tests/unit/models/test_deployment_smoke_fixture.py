from __future__ import annotations

import csv
import io
import json
import math
import tomllib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from sapphire_flow.config.qc_rules import parse_qc_rules
from sapphire_flow.services.qc import Stage1QualityChecker, resolve_selection
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation

if TYPE_CHECKING:
    from sapphire_flow.types.domain import QcRuleSet

_RESOURCES = files("sapphire_flow").joinpath("data/deployment_smoke")
_STATION = StationId(UUID(int=1))


def _observations() -> list[Observation]:
    rows = csv.DictReader(
        io.StringIO(_RESOURCES.joinpath("discharge_hourly.csv").read_text())
    )
    assert rows.fieldnames == ["time", "value"]
    parsed_rows = [
        (datetime.fromisoformat(row["time"]), float(row["value"])) for row in rows
    ]
    assert all(stamp.utcoffset() == timedelta(0) for stamp, _ in parsed_rows)
    return [
        Observation(
            id=ObservationId(UUID(int=index)),
            station_id=_STATION,
            timestamp=ensure_utc(stamp),
            parameter="discharge",
            value=value,
            source=ObservationSource.MEASURED,
            rating_curve_id=None,
            rating_curve_correction_version=None,
            qc_status=QcStatus.RAW,
            qc_flags=[],
            qc_rule_version=None,
            created_at=ensure_utc(datetime(2025, 1, 3, tzinfo=UTC)),
        )
        for index, (stamp, value) in enumerate(parsed_rows, 1)
    ]


def _rules() -> QcRuleSet:
    rules, source = parse_qc_rules(
        tomllib.loads(_RESOURCES.joinpath("qc-rules.toml").read_text())
    )
    assert source == "config"
    return rules


class TestDeploymentSmokeFixture:
    def test_expected_values_match_independent_decimal_arithmetic(self) -> None:
        discharge = list(
            csv.DictReader(
                io.StringIO(_RESOURCES.joinpath("discharge_hourly.csv").read_text())
            )
        )
        weather = list(
            csv.DictReader(io.StringIO(_RESOURCES.joinpath("weather.csv").read_text()))
        )
        expected = json.loads(
            _RESOURCES.joinpath("expected-result.json").read_text(),
            parse_float=Decimal,
        )
        last_discharge = Decimal(discharge[-1]["value"])
        assert expected["member_ids"] == list(range(1, 21))
        assert len(expected["forecast"]) == len(weather) == 3
        for step, rain in zip(expected["forecast"], weather, strict=True):
            assert step["valid_time"] == rain["valid_time"]
            assert step["values"] == [
                last_discharge
                + Decimal("0.1") * Decimal(rain["value"])
                + Decimal("0.01") * member
                for member in range(20)
            ]

    def test_pinned_hourly_rules_retain_exact_source_thresholds(self) -> None:
        rules = _rules()
        assert rules.version == "deployment-smoke-hourly-1"
        assert {rule.rule_id: dict(rule.thresholds) for rule in rules.rules} == {
            "range_check": {"value_min": 0.0, "value_max": 100000.0},
            "rate_of_change": {"max_rate": 50.0},
            "spike": {"tolerance": 0.75},
            "gross_outlier": {"k_sigma": 5.0},
        }
        assert all(rule.rule_version == "1.0.0" for rule in rules.rules)

    def test_source_files_are_copied_without_changes(self) -> None:
        source = Path(__file__).parents[2] / "fixtures" / "synthetic_station"
        for name in ("discharge_hourly.csv", "scenario.json"):
            assert (
                _RESOURCES.joinpath(name).read_bytes() == (source / name).read_bytes()
            )
        scenario = json.loads(_RESOURCES.joinpath("scenario.json").read_text())
        assert (scenario["code"], scenario["network"], scenario["timezone"]) == (
            "SYN001",
            "synthetic",
            "UTC",
        )
        assert scenario["parameters"] == "discharge"
        assert "tenant" not in scenario

    def test_actual_csv_timestamps_cover_full_hourly_support_and_model_lookback(
        self,
    ) -> None:
        observations = _observations()
        stamps = [observation.timestamp for observation in observations]
        assert len(stamps) == len(set(stamps)) == 72
        assert stamps[0] == datetime(2024, 12, 31, tzinfo=UTC)
        assert stamps[-1] == datetime(2025, 1, 2, 23, tzinfo=UTC)
        assert all(
            right - left == timedelta(hours=1)
            for left, right in zip(stamps, stamps[1:], strict=False)
        )
        assert [o.value for o in observations[-3:]] == [20.214988, 19.970200, 19.737592]
        assert all(
            o.value is not None and math.isfinite(o.value) and o.value >= 0
            for o in observations
        )

    def test_real_qc_judges_all_readings_without_flags_but_does_not_load_them(
        self,
    ) -> None:
        observations, rules = _observations(), _rules()
        assert resolve_selection(
            observations, rules, station_networks={_STATION: "synthetic"}
        ) == {(_STATION, "discharge"): (timedelta(hours=1), 4)}
        result = Stage1QualityChecker().check_with_coverage(
            observations,
            rules,
            overrides=[],
            baselines=[],
            station_networks={_STATION: "synthetic"},
        )
        assert result.judged == {o.id for o in observations}
        assert not any(result.flags.values())
        assert "frozen_sensor" not in {rule.rule_id for rule in rules.rules}

    @pytest.mark.parametrize(
        ("rule_id", "judged_count"),
        [
            ("range_check", 72),
            ("rate_of_change", 71),
            ("spike", 70),
            ("gross_outlier", 0),
        ],
    )
    def test_per_rule_coverage_is_explicit(
        self, rule_id: str, judged_count: int
    ) -> None:
        rules = _rules()
        result = Stage1QualityChecker().check_with_coverage(
            _observations(),
            rules,
            overrides=[],
            baselines=[],
            station_networks={_STATION: "synthetic"},
            skipped_rule_ids=frozenset(
                rule.rule_id for rule in rules.rules if rule.rule_id != rule_id
            ),
        )
        assert len(result.judged) == judged_count
        assert not any(result.flags.values())
        manifest = json.loads(_RESOURCES.joinpath("manifest.json").read_text())
        assert (
            manifest["qc_qualification"]["rule_judged_counts"][rule_id] == judged_count
        )
