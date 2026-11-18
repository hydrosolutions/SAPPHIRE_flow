from __future__ import annotations

import ast
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from sapphire_flow.cli import create_station as cs
from sapphire_flow.config.deployment_identity import DeploymentIdentityConfig
from sapphire_flow.config.qc_rules import resolve_qc_rules
from sapphire_flow.services import synthetic_series as ss
from sapphire_flow.services.qc import Stage1QualityChecker, resolve_selection
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcRuleSet
from sapphire_flow.types.enums import ObservationSource, QcStatus, StationStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation
from sapphire_flow.types.tenant import DEFAULT_TENANT_CODE
from tests.fakes.fake_stores import (
    FakeAuditLogStore,
    FakeStationStore,
    FakeTenantStore,
)

FIXTURE = Path(__file__).parents[2] / "fixtures" / "synthetic_station"
STATION = StationId(uuid4())


@pytest.fixture
def swiss_rules(monkeypatch: pytest.MonkeyPatch) -> QcRuleSet:
    monkeypatch.delenv("SAPPHIRE_CONFIG", raising=False)
    rules, source = resolve_qc_rules()
    assert source == "builtin_default"
    return rules


def _values(rules: QcRuleSet, spec: ss.SeriesSpec) -> list[float]:
    return ss.synthetic_discharge(
        rng=random.Random(spec.seed),
        rule_set=rules,
        step=spec.step,
        n_points=spec.n_points,
        params=spec.params,
    )


def _observations(csv_name: str, spec: ss.SeriesSpec) -> list[Observation]:
    lines = (FIXTURE / csv_name).read_text().splitlines()[1:]
    stamps = ss.series_timestamps(
        last=spec.last, step=spec.step, n_points=spec.n_points
    )
    return [
        Observation(
            id=ObservationId(uuid4()),
            station_id=STATION,
            timestamp=t,
            parameter=ss.PARAMETER,
            value=float(line.split(",")[1]),
            source=ObservationSource.MEASURED,
            rating_curve_id=None,
            rating_curve_correction_version=None,
            qc_status=QcStatus.RAW,
            qc_flags=[],
            qc_rule_version=None,
            created_at=ensure_utc(datetime(2025, 1, 3, tzinfo=UTC)),
        )
        for t, line in zip(stamps, lines, strict=True)
    ]


class TestGenerator:
    def test_same_seed_gives_same_series(self, swiss_rules: QcRuleSet) -> None:
        assert _values(swiss_rules, ss.DAILY_SPEC) == _values(
            swiss_rules, ss.DAILY_SPEC
        )

    def test_different_seed_gives_different_series(
        self, swiss_rules: QcRuleSet
    ) -> None:
        other = random.Random(ss.DAILY_SPEC.seed + 1)
        values = ss.synthetic_discharge(
            rng=other,
            rule_set=swiss_rules,
            step=ss.DAILY_SPEC.step,
            n_points=90,
            params=ss.DAILY_SPEC.params,
        )
        assert values != _values(swiss_rules, ss.DAILY_SPEC)

    def test_module_never_reads_the_clock(self) -> None:
        tree = ast.parse(Path(ss.__file__).read_text())
        called = {
            n.func.attr
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert not called & {"now", "utcnow", "today"}

    def test_rule_set_without_matching_cadence_is_refused(
        self, swiss_rules: QcRuleSet
    ) -> None:
        with pytest.raises(ValueError, match="no discharge rules"):
            ss.synthetic_discharge(
                rng=random.Random(1),
                rule_set=swiss_rules,
                step=timedelta(minutes=7),
                n_points=10,
                params=ss.DAILY_SPEC.params,
            )

    def test_limits_come_from_the_rule_set(self, swiss_rules: QcRuleSet) -> None:
        tight = QcRuleSet(
            version="t",
            rules=tuple(
                r
                if r.rule_id != "range_check"
                else type(r)(
                    rule_id=r.rule_id,
                    rule_version=r.rule_version,
                    parameter=r.parameter,
                    time_step=r.time_step,
                    thresholds={"value_min": 0.0, "value_max": 25.0},
                )
                for r in swiss_rules.rules
            ),
        )
        values = ss.synthetic_discharge(
            rng=random.Random(5),
            rule_set=tight,
            step=timedelta(days=1),
            n_points=90,
            params=ss.DAILY_SPEC.params,
        )
        assert max(values) <= 25.0

    def test_csv_has_full_precision_and_labels(self) -> None:
        t = ensure_utc(datetime(2025, 1, 2, 21, tzinfo=UTC))
        assert ss.render_csv([t], [1.23456789], ss.TimeLabel.UTC_INSTANT) == (
            "time,value\n2025-01-02T21:00:00Z,1.234568\n"
        )
        assert ss.render_csv([t], [2.5], ss.TimeLabel.LOCAL_DAY) == (
            "time,value\n2025-01-02,2.500000\n"
        )


@pytest.mark.parametrize(
    ("name", "spec", "expected_rules"),
    [
        (
            "discharge_hourly.csv",
            ss.HOURLY_SPEC,
            {"range_check", "rate_of_change", "spike", "gross_outlier"},
        ),
        (
            "discharge_daily.csv",
            ss.DAILY_SPEC,
            {
                "range_check",
                "rate_of_change",
                "frozen_sensor",
                "spike",
                "gross_outlier",
            },
        ),
    ],
)
class TestCommittedFixtures:
    def test_file_equals_generator_output_for_recorded_seed(
        self,
        swiss_rules: QcRuleSet,
        name: str,
        spec: ss.SeriesSpec,
        expected_rules: set[str],
    ) -> None:
        assert (FIXTURE / name).read_text() == ss.render_scenario(spec, swiss_rules)

    def test_shape_and_precision(
        self, name: str, spec: ss.SeriesSpec, expected_rules: set[str]
    ) -> None:
        rows = (FIXTURE / name).read_text().splitlines()
        assert rows[0] == "time,value"
        assert len(rows) - 1 == spec.n_points
        assert all(len(r.split(",")[1].split(".")[1]) >= 4 for r in rows[1:])

    def test_real_stage1_qc_gives_no_flags_and_judges_every_reading(
        self,
        swiss_rules: QcRuleSet,
        name: str,
        spec: ss.SeriesSpec,
        expected_rules: set[str],
    ) -> None:
        observations = _observations(name, spec)
        networks = {STATION: "synthetic"}
        selection = resolve_selection(
            observations, swiss_rules, station_networks=networks
        )
        step, n_rules = selection[(STATION, ss.PARAMETER)]
        assert step == spec.step
        assert n_rules == len(expected_rules)
        selected = {
            r.rule_id
            for r in swiss_rules.rules_for(ss.PARAMETER, spec.step, network=None)
        }
        assert selected == expected_rules

        result = Stage1QualityChecker().check_with_coverage(
            observations=observations,
            rule_set=swiss_rules,
            overrides=[],
            baselines=[],
            station_networks=networks,
        )
        assert [f for flags in result.flags.values() for f in flags] == []
        assert result.judged == {o.id for o in observations}

    def test_history_outlier_rule_cannot_judge_without_a_baseline(
        self,
        swiss_rules: QcRuleSet,
        name: str,
        spec: ss.SeriesSpec,
        expected_rules: set[str],
    ) -> None:
        observations = _observations(name, spec)
        only_outlier = Stage1QualityChecker().check_with_coverage(
            observations=observations,
            rule_set=swiss_rules,
            overrides=[],
            baselines=[],
            station_networks={STATION: "synthetic"},
            skipped_rule_ids=frozenset(expected_rules - {"gross_outlier"}),
        )
        assert only_outlier.judged == frozenset()


class TestScenarioJson:
    def _argv(self) -> list[str]:
        scenario = json.loads((FIXTURE / "scenario.json").read_text())
        flags = [
            part
            for key, value in scenario.items()
            for part in (f"--{key.replace('_', '-')}", str(value))
        ]
        return ["--tenant", DEFAULT_TENANT_CODE, *flags]

    def _run(self, extra: list[str]) -> tuple[int, FakeStationStore]:
        stations = FakeStationStore()
        rc = cs.run_create(
            cs._parse_args(self._argv() + extra),  # pyright: ignore[reportPrivateUsage]
            identity=DeploymentIdentityConfig(
                writable_tenants=frozenset({DEFAULT_TENANT_CODE}), global_admin=False
            ),
            tenants=FakeTenantStore(),
            stations=stations,
            audit=FakeAuditLogStore(),
            ask=None,
            new_id=lambda: UUID(int=42),
            clock=lambda: ensure_utc(datetime(2026, 11, 18, tzinfo=UTC)),
        )
        return rc, stations

    def test_dry_run_is_accepted_and_writes_nothing(self) -> None:
        rc, stations = self._run([])
        assert rc == 0
        assert stations.fetch_station_by_code("SYN001", "synthetic") is None

    def test_apply_stores_the_scenario_station(self) -> None:
        rc, stations = self._run(["--apply"])
        station = stations.fetch_station_by_code("SYN001", "synthetic")
        assert rc == 0
        assert station is not None
        assert station.timezone == "UTC"
        assert station.measured_parameters == frozenset({"discharge"})
        assert station.station_status is StationStatus.ONBOARDING
