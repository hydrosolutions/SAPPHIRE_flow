"""LOCKED tests for the ensemble fan-out component (epic-088 M2 / M3).

Pins the contract of ``sapphire_flow.services.ensemble_fanout.fan_out_ensemble``:
a member-suffixed ``future_dynamic`` (``precipitation_0``, ``precipitation_1``,
...) is exploded into per-member ``StationModelInputs``, each run through a
1-member ``predict_fn``, and reassembled into a single N-member ensemble.

RED reason (pre-implementation): the module ``ensemble_fanout`` does not exist.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl
import pytest

from sapphire_flow.services.ensemble_fanout import fan_out_ensemble
from sapphire_flow.services.forecast_combination import combine_ensembles_pooled
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.ids import ModelId, StationId
from sapphire_flow.types.model import StationInputData, StationModelInputs

_STATION_ID = StationId(uuid4())
_ISSUE = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_STEP = timedelta(days=1)
_FUTURE_FEATURES = frozenset({"precipitation", "temperature"})
_PARAM = "discharge"


def _valid_times(n: int) -> list[datetime]:
    return [ensure_utc(_ISSUE + (i + 1) * _STEP) for i in range(n)]


def _empty_ts_frame() -> pl.DataFrame:
    return pl.DataFrame({"timestamp": []}).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )


def _make_inputs(future_dynamic: pl.DataFrame, horizon: int) -> StationModelInputs:
    return StationModelInputs(
        station_id=_STATION_ID,
        data=StationInputData(
            past_targets=_empty_ts_frame(),
            past_dynamic=_empty_ts_frame(),
            future_dynamic=future_dynamic,
            static=None,
        ),
        issue_time=_ISSUE,
        forecast_horizon_steps=horizon,
        time_step=_STEP,
    )


def _member_suffixed_frame(k_members: int, n_steps: int) -> pl.DataFrame:
    """Columns: timestamp, precipitation_{k}, temperature_{k} for k in 0..K-1.

    precipitation_{k} is the constant ``100 + k`` so a predict_fn keyed on precip
    yields a distinct known value per member.
    """
    data: dict[str, list] = {"timestamp": _valid_times(n_steps)}
    for k in range(k_members):
        data[f"precipitation_{k}"] = [100.0 + k] * n_steps
        data[f"temperature_{k}"] = [10.0 + k] * n_steps
    return pl.DataFrame(data).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC"))
    )


def _one_member_from_precip(
    inputs: StationModelInputs, rng: random.Random
) -> dict[str, ForecastEnsemble]:
    """Deterministic 1-member predict_fn: value == the bare precipitation column.

    The bare ``precipitation`` column only exists after the fan-out has sliced a
    single member's ``precipitation_{k}`` and renamed it. The output value
    therefore reveals which member's slice reached predict.
    """
    del rng  # exact known-answer path; no randomness
    fd = inputs.data.future_dynamic
    values = fd.select(
        pl.col("timestamp").alias("valid_time"),
        pl.lit(1).cast(pl.Int32).alias("member_id"),
        pl.col("precipitation").cast(pl.Float64).alias("value"),
    )
    ensemble = ForecastEnsemble.from_members(
        station_id=inputs.station_id,
        issued_at=inputs.issue_time,
        parameter=_PARAM,
        units="m³/s",
        time_step=inputs.time_step,
        values=values,
    )
    return {_PARAM: ensemble}


def _one_member_noisy(
    inputs: StationModelInputs, rng: random.Random
) -> dict[str, ForecastEnsemble]:
    """1-member predict_fn that consumes ``rng`` — threads seed determinism."""
    fd = inputs.data.future_dynamic
    times = fd["timestamp"].to_list()
    precip = fd["precipitation"].to_list()
    rows = [
        {"valid_time": t, "member_id": 1, "value": float(p) + rng.random()}
        for t, p in zip(times, precip, strict=True)
    ]
    values = pl.DataFrame(rows).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        pl.col("member_id").cast(pl.Int32),
        pl.col("value").cast(pl.Float64),
    )
    ensemble = ForecastEnsemble.from_members(
        station_id=inputs.station_id,
        issued_at=inputs.issue_time,
        parameter=_PARAM,
        units="m³/s",
        time_step=inputs.time_step,
        values=values,
    )
    return {_PARAM: ensemble}


class TestFanOutMemberSuffixedColumns:
    def test_k_members_produce_k_member_ensemble_with_distinct_values(self) -> None:
        inputs = _make_inputs(_member_suffixed_frame(k_members=3, n_steps=2), horizon=2)

        result = fan_out_ensemble(
            _one_member_from_precip,
            inputs,
            random.Random(0),
            future_features=_FUTURE_FEATURES,
        )

        ensemble = result[_PARAM]
        assert ensemble.member_count == 3
        vals = ensemble.values
        assert sorted(vals["member_id"].unique().to_list()) == [0, 1, 2]
        # Known-answer: member k carries the SOURCE index k and precipitation_k ==
        # 100 + k at every step (0-based, matches the pooling offset convention).
        member_values = {
            mid: sorted(vals.filter(pl.col("member_id") == mid)["value"].to_list())
            for mid in (0, 1, 2)
        }
        assert member_values == {
            0: [100.0, 100.0],
            1: [101.0, 101.0],
            2: [102.0, 102.0],
        }

    def test_member_ids_span_full_derived_set(self) -> None:
        inputs = _make_inputs(_member_suffixed_frame(k_members=5, n_steps=2), horizon=2)

        result = fan_out_ensemble(
            _one_member_from_precip,
            inputs,
            random.Random(0),
            future_features=_FUTURE_FEATURES,
        )

        ensemble = result[_PARAM]
        assert ensemble.member_count == 5
        assert sorted(ensemble.values["member_id"].unique().to_list()) == [
            0,
            1,
            2,
            3,
            4,
        ]

    def test_deterministic_under_same_seed(self) -> None:
        inputs = _make_inputs(_member_suffixed_frame(k_members=3, n_steps=2), horizon=2)

        first = fan_out_ensemble(
            _one_member_noisy,
            inputs,
            random.Random(1234),
            future_features=_FUTURE_FEATURES,
        )
        second = fan_out_ensemble(
            _one_member_noisy,
            inputs,
            random.Random(1234),
            future_features=_FUTURE_FEATURES,
        )

        assert first[_PARAM].member_count == 3
        assert first[_PARAM].values.equals(second[_PARAM].values)


class TestFanOutBareColumns:
    def test_bare_columns_call_predict_once_single_member(self) -> None:
        bare = pl.DataFrame(
            {
                "timestamp": _valid_times(2),
                "precipitation": [7.0, 7.0],
                "temperature": [3.0, 3.0],
            }
        ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
        inputs = _make_inputs(bare, horizon=2)

        result = fan_out_ensemble(
            _one_member_from_precip,
            inputs,
            random.Random(0),
            future_features=_FUTURE_FEATURES,
        )
        direct = _one_member_from_precip(inputs, random.Random(0))

        assert result[_PARAM].member_count == 1
        assert result[_PARAM].values.equals(direct[_PARAM].values)


class TestFanOutMixedModeColumns:
    """``ensemble_mode`` is declared PER future variable, so ``future_dynamic`` may
    carry BOTH member-suffixed columns (``precipitation_0..N``) AND bare single-mode
    columns (a covariate ``temperature`` with no suffix). Each per-member slice must
    replace only the suffixed columns and carry the bare covariate through verbatim.

    RED under the drop-bare slice: the bare ``temperature`` never reaches predict.
    """

    def test_bare_covariate_carried_into_every_member_slice(self) -> None:
        frame = pl.DataFrame(
            {
                "timestamp": _valid_times(2),
                "precipitation_0": [100.0, 100.0],
                "precipitation_1": [101.0, 101.0],
                "precipitation_2": [102.0, 102.0],
                "temperature": [3.0, 3.0],  # bare, single-mode covariate
            }
        ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
        inputs = _make_inputs(frame, horizon=2)

        seen_temperatures: list[list[float]] = []
        seen_precip_first: list[float] = []

        def _recording_predict(
            member_inputs: StationModelInputs, rng: random.Random
        ) -> dict[str, ForecastEnsemble]:
            del rng
            fd = member_inputs.data.future_dynamic
            # Required: the bare covariate survived the per-member slice.
            assert "temperature" in fd.columns
            assert "precipitation" in fd.columns
            seen_temperatures.append(fd["temperature"].to_list())
            seen_precip_first.append(float(fd["precipitation"][0]))
            values = fd.select(
                pl.col("timestamp").alias("valid_time"),
                pl.lit(1).cast(pl.Int32).alias("member_id"),
                pl.col("precipitation").cast(pl.Float64).alias("value"),
            )
            return {
                _PARAM: ForecastEnsemble.from_members(
                    station_id=member_inputs.station_id,
                    issued_at=member_inputs.issue_time,
                    parameter=_PARAM,
                    units="m³/s",
                    time_step=member_inputs.time_step,
                    values=values,
                )
            }

        result = fan_out_ensemble(
            _recording_predict,
            inputs,
            random.Random(0),
            future_features=_FUTURE_FEATURES,
        )

        assert result[_PARAM].member_count == 3
        # The bare covariate reached predict on every member call, shared/identical.
        assert seen_temperatures == [[3.0, 3.0], [3.0, 3.0], [3.0, 3.0]]
        # Each member's precipitation slice is distinct (100/101/102).
        assert sorted(seen_precip_first) == [100.0, 101.0, 102.0]


def _native_zero_based_ensemble(n_members: int, n_steps: int) -> ForecastEnsemble:
    """A native ensemble with 0-based contiguous ids (``0..n_members-1``).

    Models that emit their own ensemble number members 0-based; this mimics one
    pooled alongside a fanned ensemble.
    """
    rows = [
        {"valid_time": t, "member_id": m, "value": 500.0 + m}
        for m in range(n_members)
        for t in _valid_times(n_steps)
    ]
    values = pl.DataFrame(rows).with_columns(
        pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
        pl.col("member_id").cast(pl.Int32),
        pl.col("value").cast(pl.Float64),
    )
    return ForecastEnsemble.from_members(
        station_id=_STATION_ID,
        issued_at=_ISSUE,
        parameter=_PARAM,
        units="m³/s",
        time_step=_STEP,
        values=values,
    )


class TestFanOutPoolingNoCollision:
    """Regression: a fanned ensemble pooled with a native 0-based ensemble must
    yield ``2N`` unique members. ``combine_ensembles_pooled`` offsets each model's
    members by ``n_unique`` assuming 0-based contiguous ids. Under the old 1-based
    fan-out (``member + 1`` → ``1..N``) the offset boundary collides with the native
    ``0..N-1`` set at id N, losing one member (``2N-1`` instead of ``2N``).
    """

    def test_fanned_pooled_with_native_yields_2n_unique_members(self) -> None:
        n = 21
        inputs = _make_inputs(_member_suffixed_frame(k_members=n, n_steps=2), horizon=2)
        fanned = fan_out_ensemble(
            _one_member_from_precip,
            inputs,
            random.Random(0),
            future_features=_FUTURE_FEATURES,
        )[_PARAM]
        native = _native_zero_based_ensemble(n_members=n, n_steps=2)

        pooled = combine_ensembles_pooled(
            {
                ModelId("fanned"): {_PARAM: fanned},
                ModelId("native"): {_PARAM: native},
            }
        )[_PARAM]

        assert pooled.values["member_id"].n_unique() == 2 * n


class TestFanOutRaggedMembers:
    def test_ragged_member_sets_across_features_raise(self) -> None:
        ragged = pl.DataFrame(
            {
                "timestamp": _valid_times(2),
                "precipitation_0": [1.0, 1.0],
                "precipitation_1": [2.0, 2.0],
                "precipitation_2": [3.0, 3.0],
                "temperature_0": [10.0, 10.0],
                "temperature_1": [11.0, 11.0],
            }
        ).with_columns(pl.col("timestamp").cast(pl.Datetime("us", "UTC")))
        inputs = _make_inputs(ragged, horizon=2)

        with pytest.raises(
            ValueError, match=r"(?i)(ragged|inconsistent|member|mismatch)"
        ):
            fan_out_ensemble(
                _one_member_from_precip,
                inputs,
                random.Random(0),
                future_features=_FUTURE_FEATURES,
            )
