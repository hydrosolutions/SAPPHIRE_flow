from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from sapphire_flow.config.qc_rules import _default_swiss_qc_rules, load_qc_rules
from sapphire_flow.services.qc import Stage1QualityChecker, resolve_selection
from sapphire_flow.services.qc_datum import obs_skipped_rules
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import ClimBaseline
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sapphire_flow.types.domain import QcRuleParams

_REPO_ROOT = Path(__file__).resolve().parents[3]

_MINIMAL_TOML = """\
weather_hot_days = 180
forecast_hot_days = 548
max_retention_days = 3650

[qc_rules]
version = "2.0.0"

[[qc_rules.rules]]
rule_id = "range_check"
rule_version = "1.0.0"
parameter = "discharge"
time_step_seconds = 600
thresholds = { value_min = 0.0, value_max = 9999.0 }

[[qc_rules.rules]]
rule_id = "gross_outlier"
rule_version = "1.0.0"
parameter = "water_level"
time_step_seconds = 600
thresholds = { k_sigma = 3.0 }
"""

_NO_QC_TOML = """\
weather_hot_days = 180
forecast_hot_days = 548
max_retention_days = 3650
"""


class TestLoadFromToml:
    def test_load_from_toml(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_MINIMAL_TOML)

        result = load_qc_rules(config_file)

        assert result.version == "2.0.0"
        assert len(result.rules) == 2

        discharge_rule = result.rules[0]
        assert discharge_rule.rule_id == "range_check"
        assert discharge_rule.parameter == "discharge"
        assert discharge_rule.time_step == timedelta(seconds=600)
        assert discharge_rule.thresholds == {"value_min": 0.0, "value_max": 9999.0}

        wl_rule = result.rules[1]
        assert wl_rule.rule_id == "gross_outlier"
        assert wl_rule.parameter == "water_level"
        assert wl_rule.thresholds == {"k_sigma": 3.0}

    def test_network_round_trips_from_toml(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[qc_rules]\nversion = "2.0.0"\n\n'
            '[[qc_rules.rules]]\nrule_id = "range_check"\n'
            'rule_version = "2.0.0"\nparameter = "discharge"\n'
            'time_step_seconds = 86400\nnetwork = "dhm"\n'
            "thresholds = { value_min = 0.0, value_max = 100.0 }\n"
        )

        result = load_qc_rules(config_file)

        assert result.rules[0].network == "dhm"

    @pytest.mark.parametrize(
        "field_value, error",
        [
            ("network = 12", "network must be a string or null"),
            ('netwrok = "dhm"', "Unknown QC rule fields"),
        ],
    )
    def test_invalid_network_or_unknown_rule_field_is_rejected(
        self, tmp_path: Path, field_value: str, error: str
    ) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[qc_rules]\nversion = "2.0.0"\n\n'
            '[[qc_rules.rules]]\nrule_id = "range_check"\n'
            'rule_version = "2.0.0"\nparameter = "discharge"\n'
            "time_step_seconds = 86400\n"
            f"{field_value}\n"
            "thresholds = { value_min = 0.0, value_max = 100.0 }\n"
        )

        with pytest.raises(ValueError, match=error):
            load_qc_rules(config_file)


class TestDefaultRules:
    def test_default_rules_has_discharge_10min(self) -> None:
        rules = _default_swiss_qc_rules()
        discharge_10min = rules.rules_for("discharge", timedelta(seconds=600))
        assert len(discharge_10min) > 0

    def test_default_rules_has_discharge_daily(self) -> None:
        rules = _default_swiss_qc_rules()
        discharge_daily = rules.rules_for("discharge", timedelta(seconds=86400))
        assert len(discharge_daily) > 0

    def test_default_rules_has_water_level(self) -> None:
        rules = _default_swiss_qc_rules()
        wl_rules = rules.rules_for("water_level", timedelta(seconds=600))
        assert len(wl_rules) > 0

    def test_default_rules_has_water_temperature(self) -> None:
        rules = _default_swiss_qc_rules()
        wt_rules = rules.rules_for("water_temperature", timedelta(seconds=600))
        assert len(wt_rules) > 0

    def test_default_rules_has_precipitation(self) -> None:
        rules = _default_swiss_qc_rules()
        precip_rules = rules.rules_for("precipitation", timedelta(seconds=86400))
        assert len(precip_rules) > 0

    def test_default_rules_has_temperature(self) -> None:
        rules = _default_swiss_qc_rules()
        temp_rules = rules.rules_for("temperature", timedelta(seconds=86400))
        assert len(temp_rules) > 0

    def test_default_version(self) -> None:
        rules = _default_swiss_qc_rules()
        assert rules.version == "1.0.0"

    def test_all_default_rules_are_generic(self) -> None:
        assert all(rule.network is None for rule in _default_swiss_qc_rules().rules)

    def test_water_level_daily_rules_exist(self) -> None:
        rules = _default_swiss_qc_rules()
        wl_daily = rules.rules_for("water_level", timedelta(seconds=86400))
        assert len(wl_daily) == 5
        rule_ids = {r.rule_id for r in wl_daily}
        assert rule_ids == {
            "range_check",
            "rate_of_change",
            "frozen_sensor",
            "spike",
            "gross_outlier",
        }


class TestProductionConfigRules:
    @pytest.mark.parametrize(
        "relative_path",
        ("config.toml", "docs/spec/config-reference.toml"),
    )
    def test_water_level_spike_rules_use_max_delta(self, relative_path: str) -> None:
        rules = load_qc_rules(_REPO_ROOT / relative_path)
        water_level_spikes = [
            rule
            for rule in rules.rules
            if rule.parameter == "water_level" and rule.rule_id == "spike"
        ]

        assert {rule.time_step for rule in water_level_spikes} == {
            timedelta(seconds=600),
            timedelta(seconds=3600),
            timedelta(seconds=86400),
        }
        assert {
            rule.time_step: rule.thresholds["max_delta"] for rule in water_level_spikes
        } == {
            timedelta(seconds=600): 1.0,
            timedelta(seconds=3600): 1.0,
            timedelta(seconds=86400): 5.0,
        }
        assert all("tolerance" not in rule.thresholds for rule in water_level_spikes)

    def test_checked_in_qc_configs_agree_on_network_rules_and_version(self) -> None:
        config = load_qc_rules(_REPO_ROOT / "config.toml")
        reference = load_qc_rules(_REPO_ROOT / "docs/spec/config-reference.toml")

        assert config.version == reference.version
        config_network_rules = {
            (
                rule.rule_id,
                rule.rule_version,
                rule.parameter,
                rule.time_step,
                rule.network,
                frozenset(rule.thresholds.items()),
            )
            for rule in config.rules
            if rule.network is not None
        }
        reference_network_rules = {
            (
                rule.rule_id,
                rule.rule_version,
                rule.parameter,
                rule.time_step,
                rule.network,
                frozenset(rule.thresholds.items()),
            )
            for rule in reference.rules
            if rule.network is not None
        }
        assert config_network_rules == reference_network_rules

    def test_loaded_water_level_spike_rule_dispatches_on_max_delta(self) -> None:
        station_id = StationId(uuid4())
        start = ensure_utc(datetime(2026, 4, 8, 14, 0, tzinfo=UTC))
        observations = [
            Observation(
                id=ObservationId(uuid4()),
                station_id=station_id,
                timestamp=ensure_utc(start + timedelta(minutes=10 * i)),
                parameter="water_level",
                value=value,
                source=ObservationSource.MEASURED,
                rating_curve_id=None,
                rating_curve_correction_version=None,
                qc_status=QcStatus.RAW,
                qc_flags=[],
                qc_rule_version=None,
                created_at=start,
            )
            for i, value in enumerate((15.0, 16.2, 15.0))
        ]

        flags = Stage1QualityChecker().check(
            observations,
            load_qc_rules(_REPO_ROOT / "config.toml"),
            overrides=[],
            baselines=[],
            station_networks={station_id: "bafu"},
        )

        assert any(flag.rule_id == "spike" for flag in flags[observations[1].id])


class TestRulesForFilter:
    def test_rules_for_returns_correct_subset(self) -> None:
        rules = _default_swiss_qc_rules()
        result = rules.rules_for("discharge", timedelta(seconds=600))

        assert all(r.parameter == "discharge" for r in result)
        assert all(r.time_step == timedelta(seconds=600) for r in result)

    def test_rules_for_excludes_daily_discharge(self) -> None:
        rules = _default_swiss_qc_rules()
        result = rules.rules_for("discharge", timedelta(seconds=600))
        daily = rules.rules_for("discharge", timedelta(seconds=86400))

        assert set(r.rule_id for r in result) & set(r.rule_id for r in daily)
        # 10-min and daily should be distinct objects
        assert not any(r in daily for r in result)

    def test_rules_for_empty_for_unknown_parameter(self) -> None:
        rules = _default_swiss_qc_rules()
        result = rules.rules_for("unknown_param", timedelta(seconds=600))
        assert result == ()


class TestMissingQcSectionReturnsDefault:
    def test_missing_qc_section_returns_default(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_NO_QC_TOML)

        result = load_qc_rules(config_file)
        default = _default_swiss_qc_rules()

        assert result.version == default.version
        assert result.rules == default.rules

    def test_no_path_no_env_raises(self) -> None:
        import os

        env_backup = os.environ.pop("SAPPHIRE_CONFIG", None)
        try:
            with pytest.raises(ValueError, match="SAPPHIRE_CONFIG"):
                load_qc_rules()
        finally:
            if env_backup is not None:
                os.environ["SAPPHIRE_CONFIG"] = env_backup


class TestOverlaySupport:
    def test_overlay_setting_qc_version_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base = tmp_path / "config.toml"
        base.write_text(_MINIMAL_TOML)
        overlay = tmp_path / "overlay.toml"
        overlay.write_text('[qc_rules]\nversion = "3.5.0"\n')
        monkeypatch.setenv("SAPPHIRE_CONFIG_OVERLAY", str(overlay))

        with pytest.raises(
            ValueError, match="QC rules must be changed in the base config"
        ):
            load_qc_rules(base)


class TestShippedDischargeCeiling:
    """Bind the SHIPPED discharge ceiling by VALUE, on every surface that carries it.

    The loose-first posture (`docs/v1-scope.md` § QC posture) is a decision about a
    number, so a test asserting only that a `range_check` rule EXISTS cannot detect
    the number changing back. An independent review found the observation surfaces
    had no such binding and that a fourth surface still carried the old value.
    """

    @pytest.mark.parametrize(
        "relative_path",
        ("config.toml", "docs/spec/config-reference.toml"),
    )
    def test_both_toml_surfaces_ship_the_loose_ceiling(
        self, relative_path: str
    ) -> None:
        """⛔ The reference config is bound too. Review round 2 found that binding
        `config.toml` alone let the documented one drift back silently."""
        rules = load_qc_rules(_REPO_ROOT / relative_path)

        ceilings = {
            rule.time_step: rule.thresholds["value_max"]
            for rule in rules.rules
            if rule.rule_id == "range_check" and rule.parameter == "discharge"
        }

        assert ceilings == {
            timedelta(seconds=600): 100000.0,
            timedelta(seconds=3600): 100000.0,
            timedelta(seconds=86400): 100000.0,
        }

    def test_the_swiss_defaults_agree_with_the_shipped_config(self) -> None:
        """They are separate surfaces, and only a test keeps them from drifting."""
        defaults = {
            rule.time_step: rule.thresholds["value_max"]
            for rule in _default_swiss_qc_rules().rules
            if rule.rule_id == "range_check" and rule.parameter == "discharge"
        }
        shipped = {
            rule.time_step: rule.thresholds["value_max"]
            for rule in load_qc_rules(_REPO_ROOT / "config.toml").rules
            if rule.rule_id == "range_check" and rule.parameter == "discharge"
        }

        assert defaults == shipped


# ---------------------------------------------------------------------------
# Plan 323 T2 — the hourly (3600 s) rows
# ---------------------------------------------------------------------------

_HOURLY = timedelta(seconds=3600)
_HOURLY_START = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)

# D1/D2 as T1 measured them (plan 323 § T1 result): copied 600 s rows, except
# the two derived values (water_temperature max_rate, discharge spike tolerance).
_HOURLY_ROWS: dict[tuple[str, str], dict[str, float]] = {
    ("range_check", "discharge"): {"value_min": 0.0, "value_max": 100000.0},
    ("rate_of_change", "discharge"): {"max_rate": 50.0},
    ("spike", "discharge"): {"tolerance": 0.75},
    ("gross_outlier", "discharge"): {"k_sigma": 5.0},
    ("range_check", "water_level"): {"value_min": -2.0, "value_max": 20.0},
    ("rate_of_change", "water_level"): {"max_rate": 0.5},
    ("spike", "water_level"): {"max_delta": 1.0},
    ("gross_outlier", "water_level"): {"k_sigma": 5.0},
    ("range_check", "water_temperature"): {"value_min": -2.0, "value_max": 40.0},
    ("rate_of_change", "water_temperature"): {"max_rate": 2.358},
    ("gross_outlier", "water_temperature"): {"k_sigma": 4.0},
}


def _hourly_rows(
    rules: Iterable[QcRuleParams],
) -> set[tuple[str, str, str, str | None, frozenset[tuple[str, float]]]]:
    return {
        (
            rule.rule_id,
            rule.parameter,
            rule.rule_version,
            rule.network,
            frozenset(rule.thresholds.items()),
        )
        for rule in rules
        if rule.time_step == _HOURLY
    }


def _hourly_series(
    parameter: str, values: list[float], station_id: StationId | None = None
) -> list[Observation]:
    station_id = station_id or StationId(uuid4())
    return [
        Observation(
            id=ObservationId(uuid4()),
            station_id=station_id,
            timestamp=ensure_utc(_HOURLY_START + timedelta(hours=i)),
            parameter=parameter,
            value=value,
            source=ObservationSource.MEASURED,
            rating_curve_id=None,
            rating_curve_correction_version=None,
            qc_status=QcStatus.RAW,
            qc_flags=[],
            qc_rule_version=None,
            created_at=_HOURLY_START,
        )
        for i, value in enumerate(values)
    ]


def _flagged_rules(
    parameter: str,
    values: list[float],
    *,
    baseline: tuple[float, float] | None = None,
) -> set[str]:
    """The rule ids that flag ANY reading of a shipped-config hourly series.

    Water-level values are given as the rules see them (datum-shifted), so
    `range_check` and `gross_outlier` are not skipped here.
    """
    observations = _hourly_series(parameter, values)
    station_id = observations[0].station_id
    baselines = (
        [
            ClimBaseline(
                station_id=station_id,
                parameter=parameter,
                day_of_year=observations[0].timestamp.timetuple().tm_yday,
                rolling_mean=baseline[0],
                rolling_std=baseline[1],
                sample_count=30,
            )
        ]
        if baseline
        else []
    )
    flags = Stage1QualityChecker().check(
        observations,
        load_qc_rules(_REPO_ROOT / "config.toml"),
        [],
        baselines,
        station_networks={station_id: "bafu"},
    )
    return {flag.rule_id for row in flags.values() for flag in row}


class TestHourlyRules:
    @pytest.mark.parametrize(
        ("parameter", "datum", "expected_ids"),
        (
            (
                "discharge",
                None,
                {"range_check", "rate_of_change", "spike", "gross_outlier"},
            ),
            (
                "water_level",
                480.0,
                {"range_check", "rate_of_change", "spike", "gross_outlier"},
            ),
            ("water_level", None, {"rate_of_change", "spike"}),
            (
                "water_temperature",
                None,
                {"range_check", "rate_of_change", "gross_outlier"},
            ),
        ),
    )
    def test_an_hourly_group_selects_its_declared_rules(
        self, parameter: str, datum: float | None, expected_ids: set[str]
    ) -> None:
        """⛔ Loads the SHIPPED `config.toml` — a hand-built rule set would prove
        nothing about the file this plan edits. Fails on the empty selection."""
        rules = load_qc_rules(_REPO_ROOT / "config.toml")
        observations = _hourly_series(parameter, [1.0, 1.1, 1.2])
        station_id = observations[0].station_id
        skipped = obs_skipped_rules(parameter, datum)

        ((step, count),) = resolve_selection(
            observations,
            rules,
            station_networks={station_id: "bafu"},
            skipped_rule_ids=skipped,
        ).values()

        selected = {
            rule.rule_id
            for rule in rules.rules_for(parameter, _HOURLY, network="bafu")
            if rule.rule_id not in skipped
        }
        assert step == _HOURLY
        assert count == len(expected_ids)
        assert selected == expected_ids

    @pytest.mark.parametrize("network", ("bafu", "dhm"))
    @pytest.mark.parametrize(
        ("parameter", "expected_ids"),
        (
            (
                "discharge",
                {
                    "range_check",
                    "rate_of_change",
                    "frozen_sensor",
                    "spike",
                    "gross_outlier",
                },
            ),
            (
                "water_level",
                {
                    "range_check",
                    "rate_of_change",
                    "frozen_sensor",
                    "spike",
                    "gross_outlier",
                },
            ),
            (
                "water_temperature",
                {"range_check", "rate_of_change", "frozen_sensor", "gross_outlier"},
            ),
        ),
    )
    def test_a_600_s_groups_selection_is_unchanged(
        self, network: str, parameter: str, expected_ids: set[str]
    ) -> None:
        """The addition must not perturb the stations that were already checked."""
        rules = load_qc_rules(_REPO_ROOT / "config.toml")

        selected = rules.rules_for(parameter, timedelta(seconds=600), network=network)

        assert {rule.rule_id for rule in selected} == expected_ids

    def test_the_hourly_rows_are_the_d2_set_with_no_frozen_sensor(self) -> None:
        rows = load_qc_rules(_REPO_ROOT / "config.toml").rules

        hourly = {
            (rule.rule_id, rule.parameter): dict(rule.thresholds)
            for rule in rows
            if rule.time_step == _HOURLY
        }

        assert hourly == _HOURLY_ROWS

    def test_the_three_surfaces_agree_on_the_eleven_hourly_rows(self) -> None:
        """The existing parity tests cover the discharge ceiling and network rows
        only — nothing else would catch a row missing from one surface."""
        config = _hourly_rows(load_qc_rules(_REPO_ROOT / "config.toml").rules)
        reference = _hourly_rows(
            load_qc_rules(_REPO_ROOT / "docs/spec/config-reference.toml").rules
        )
        defaults = _hourly_rows(_default_swiss_qc_rules().rules)

        assert len(config) == 11
        assert config == reference == defaults

    def test_every_hourly_row_carries_rule_version_1_0_0_and_no_network(self) -> None:
        rows = _hourly_rows(load_qc_rules(_REPO_ROOT / "config.toml").rules)

        assert {(row[2], row[3]) for row in rows} == {("1.0.0", None)}

    def test_the_rule_set_version_moves_with_the_added_rows(self) -> None:
        config = load_qc_rules(_REPO_ROOT / "config.toml")
        reference = load_qc_rules(_REPO_ROOT / "docs/spec/config-reference.toml")

        assert config.version == reference.version == "1.2.0"

    def test_an_ordinary_hourly_series_passes_and_an_impossible_one_fails(
        self,
    ) -> None:
        assert _flagged_rules("discharge", [2.9, 3.0, 3.1]) == set()
        assert "range_check" in _flagged_rules("discharge", [2.9, -1.0, 3.1])

    @pytest.mark.parametrize(
        ("parameter", "key", "bound", "below", "above"),
        (
            ("discharge", "value_min", 0.0, 0.0, -0.001),
            ("discharge", "value_max", 100000.0, 100000.0, 100000.001),
            ("water_level", "value_min", -2.0, -2.0, -2.001),
            ("water_level", "value_max", 20.0, 20.0, 20.001),
            ("water_temperature", "value_min", -2.0, -2.0, -2.001),
            ("water_temperature", "value_max", 40.0, 40.0, 40.001),
        ),
    )
    def test_each_range_bound_is_tested_at_its_own_side(
        self, parameter: str, key: str, bound: float, below: float, above: float
    ) -> None:
        assert _HOURLY_ROWS[("range_check", parameter)][key] == bound
        assert "range_check" not in _flagged_rules(parameter, [below] * 3)
        assert "range_check" in _flagged_rules(parameter, [above] * 3)

    @pytest.mark.parametrize(
        ("parameter", "base", "p999", "beyond"),
        (
            ("discharge", 10.0, 0.411, 50.001),
            ("water_level", 10.0, 0.076, 0.501),
            ("water_temperature", 5.0, 1.179, 2.359),
        ),
    )
    def test_rate_of_change_passes_at_the_measured_p999_and_flags_just_beyond(
        self, parameter: str, base: float, p999: float, beyond: float
    ) -> None:
        assert "rate_of_change" not in _flagged_rules(
            parameter, [base, base + p999, base + p999]
        )
        assert "rate_of_change" in _flagged_rules(
            parameter, [base, base + beyond, base + beyond]
        )

    @pytest.mark.parametrize(
        ("parameter", "base", "p999", "inside", "beyond"),
        (
            ("water_level", 10.0, 0.041, 0.999, 1.001),
            ("discharge", 10.0, 3.75, 7.4, 7.6),
        ),
    )
    def test_spike_passes_at_the_measured_p999_and_flags_just_beyond(
        self, parameter: str, base: float, p999: float, inside: float, beyond: float
    ) -> None:
        """Discharge is relative: 0.75 x |prev| = 7.5 at base 10; T1's relative
        p99.9 was 0.375, i.e. 3.75 here."""
        assert "spike" not in _flagged_rules(parameter, [base, base + p999, base])
        assert "spike" not in _flagged_rules(parameter, [base, base + inside, base])
        assert "spike" in _flagged_rules(parameter, [base, base + beyond, base])

    def test_water_temperature_has_no_hourly_spike_row(self) -> None:
        assert ("spike", "water_temperature") not in _HOURLY_ROWS
        assert "spike" not in _flagged_rules("water_temperature", [5.0, 25.0, 5.0])

    @pytest.mark.parametrize(
        ("parameter", "k_sigma"),
        (("discharge", 5.0), ("water_level", 5.0), ("water_temperature", 4.0)),
    )
    def test_gross_outlier_uses_the_copied_k_sigma_on_both_sides(
        self, parameter: str, k_sigma: float
    ) -> None:
        mean, std = 10.0, 1.0
        inside = mean + k_sigma * std - 0.001
        beyond = mean + k_sigma * std + 0.001

        assert "gross_outlier" not in _flagged_rules(
            parameter, [mean, mean, inside], baseline=(mean, std)
        )
        assert "gross_outlier" in _flagged_rules(
            parameter, [mean, mean, beyond], baseline=(mean, std)
        )
