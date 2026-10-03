from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from sapphire_flow.adapters import forecast_interface as fi
from sapphire_flow.types.enums import EnsembleRepresentation, ForcingType, QcStatus
from tests.integration.services.hindcast_isolation_fixture import (
    ARTIFACT,
    CLOCK,
    MODEL,
    RUN,
    SID_A,
    SID_B,
    STEP,
    Outcome,
    T,
    run_case,
)
from tests.integration.services.hindcast_isolation_fixture import (
    owned_harness as owned_harness,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sapphire_flow.types.ids import StationId
    from tests.integration.db.test_role_bootstrap import (
        _RoleBootstrapHarness,  # pyright: ignore[reportPrivateUsage]
    )


@pytest.fixture(autouse=True)
def artifact_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SAPPHIRE_DATA_DIR", str(tmp_path))


_EXPECTED = {
    SID_A: (10.0, 15.5, 19.5, 23.5, 27.5, 31.5),
    SID_B: (25.0, 35.5, 39.5, 43.5, 47.5, 51.5),
}


def assert_inputs(
    outcome: Outcome, station_ids: frozenset[StationId], *, perturbation: float = 0.0
) -> None:
    assert len(outcome.calls) == 4
    for index, (issued_at, inputs) in enumerate(outcome.calls):
        assert issued_at == T + index * STEP
        assert set(inputs.stations) == {
            "station"
            if outcome.scope is fi.FIArtifactScope.STATION
            else f"hindcast-{sid.int}"
            for sid in station_ids
        }
        for sid in station_ids:
            key = (
                "station"
                if outcome.scope is fi.FIArtifactScope.STATION
                else f"hindcast-{sid.int}"
            )
            station = inputs.stations[key]
            assert station.static == {}
            assert set(station.dynamic) == {STEP}
            assert set(station.dynamic[STEP].data) == {fi.FISpatialRepresentation.POINT}
            dynamic = station.dynamic[STEP].data[fi.FISpatialRepresentation.POINT]
            assert set(dynamic.past_known) == {"obs"}
            assert set(dynamic.past_known["obs"]) == {"discharge"}
            assert set(dynamic.future_known) == {"nwp"}
            assert set(dynamic.future_known["nwp"]) == {"precipitation_forecast"}
            target = dynamic.past_known["obs"]["discharge"]
            future = dynamic.future_known["nwp"]["precipitation_forecast"]
            assert target.unit is fi.Unit.M3_PER_S
            assert future.unit is fi.Unit.MM
            values = [
                value + (perturbation if sid == SID_A and bucket == 2 else 0.0)
                for bucket, value in enumerate(_EXPECTED[sid])
            ][index : index + 3]
            expected_target = pl.DataFrame(
                {
                    "datetime": [issued_at + (step - 3) * STEP for step in range(3)],
                    "discharge": values,
                }
            )
            expected_future = pl.DataFrame(
                {
                    "datetime": [issued_at + step * STEP for step in range(3)],
                    "precipitation_forecast": [3.0] * 3,
                }
            )
            assert_frame_equal(target.data, expected_target)
            assert_frame_equal(future.data, expected_future)


def assert_forecasts(
    outcome: Outcome,
    healthy: frozenset[StationId],
    missing: frozenset[StationId] = frozenset(),
) -> None:
    assert set(outcome.steps) == healthy | missing
    assert len(outcome.forecasts) == 4 * len(healthy)
    assert len({forecast.id for forecast in outcome.forecasts}) == len(
        outcome.forecasts
    )
    assert outcome.writes == ["sapphire_worker"] * len(outcome.forecasts)
    for sid, steps in outcome.steps.items():
        assert [step.issue_time for step in steps] == [
            T + index * STEP for index in range(4)
        ]
        assert [step.success for step in steps] == [sid in healthy] * 4
        assert [step.error for step in steps] == [
            None if sid in healthy else "insufficient data"
        ] * 4
    for sid in healthy:
        forecasts = sorted(
            [forecast for forecast in outcome.forecasts if forecast.station_id == sid],
            key=lambda forecast: forecast.hindcast_step,
        )
        assert [forecast.hindcast_step for forecast in forecasts] == [
            T + index * STEP for index in range(4)
        ]
        for forecast in forecasts:
            assert forecast.model_id == MODEL
            assert forecast.model_artifact_id == ARTIFACT
            assert forecast.hindcast_run_id == RUN
            assert forecast.created_at == CLOCK
            assert forecast.forcing_type is ForcingType.REANALYSIS
            assert forecast.qc_status is QcStatus.RAW
            assert forecast.qc_flags == ()
            assert forecast.representation is EnsembleRepresentation.MEMBERS
            ensemble = forecast.ensemble
            assert ensemble.station_id == sid
            assert ensemble.parameter == "discharge" and ensemble.units == "m³/s"
            assert ensemble.time_step == STEP and ensemble.forecast_horizon_steps == 3
            assert ensemble.member_count == 1
            assert ensemble.issued_at == forecast.hindcast_step
            assert ensemble.values.height == 3
            assert ensemble.values["valid_time"].sort().to_list() == [
                forecast.hindcast_step + index * STEP for index in (1, 2, 3)
            ]
            assert ensemble.values["value"].is_finite().all()


def content(outcome: Outcome) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for forecast in sorted(
        outcome.forecasts,
        key=lambda forecast: (str(forecast.station_id), forecast.hindcast_step),
    ):
        row = asdict(forecast)
        del row["id"]  # New row UUIDs differ across fully rolled-back scenarios.
        ensemble = asdict(forecast.ensemble)
        ensemble["values"] = forecast.ensemble.values.sort(
            "valid_time", "member_id"
        ).to_dicts()
        row["ensemble"] = ensemble
        result.append(row)
    return result


@pytest.mark.parametrize(
    "scope", [fi.FIArtifactScope.STATION, fi.FIArtifactScope.GROUP]
)
def test_mixed_provisional_history_cannot_change_actual_hindcast_inputs(
    owned_harness: _RoleBootstrapHarness,
    scope: fi.FIArtifactScope,
) -> None:
    healthy = (
        frozenset({SID_A})
        if scope is fi.FIArtifactScope.STATION
        else frozenset({SID_A, SID_B})
    )
    clean = run_case(owned_harness, scope, provisional="absent")
    mixed = run_case(owned_harness, scope, provisional="present")
    perturbed = run_case(owned_harness, scope, provisional="present", delta=8.0)
    for outcome in (clean, mixed, perturbed):
        assert_forecasts(outcome, healthy)
    assert_inputs(clean, healthy)
    assert_inputs(mixed, healthy)
    assert_inputs(perturbed, healthy, perturbation=2.0)
    assert content(clean) == content(mixed)
    # The reference model ignores Q numerically; target sensitivity is asserted above.
    assert content(mixed) == content(perturbed)


def test_provisional_only_station_does_not_invent_hindcast_history(
    owned_harness: _RoleBootstrapHarness,
) -> None:
    outcome = run_case(
        owned_harness,
        fi.FIArtifactScope.STATION,
        provisional="present",
        absent=frozenset({SID_A}),
    )
    assert outcome.calls == []
    assert_forecasts(outcome, frozenset(), frozenset({SID_A}))


def test_group_keeps_healthy_member_and_reports_provisional_only_member(
    owned_harness: _RoleBootstrapHarness,
) -> None:
    outcome = run_case(
        owned_harness,
        fi.FIArtifactScope.GROUP,
        provisional="present",
        absent=frozenset({SID_B}),
    )
    assert_inputs(outcome, frozenset({SID_A}))
    assert_forecasts(outcome, frozenset({SID_A}), frozenset({SID_B}))


def test_public_hindcast_store_accepts_aligned_first_valid_time(
    owned_harness: _RoleBootstrapHarness,
) -> None:
    outcome = run_case(
        owned_harness,
        fi.FIArtifactScope.STATION,
        provisional="present",
        aligned_store_check="check",
    )
    assert len(outcome.forecasts) == 4
