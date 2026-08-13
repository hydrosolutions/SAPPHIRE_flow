"""Plan 157 T1 (G3/G9/G15) — the aquacast shim packaging CONTRACT, proven
against a reference implementation.

The REAL `sapphire-aquacast` distribution (the torch runtime, the trained
`cmal_pool_PT` weights) lives OUTSIDE this repo, in a separate distribution
owned by hydrosolutions (Plan 135 decision 3: no torch/GPL runtime
dependency in this repo's `pyproject.toml`). It does not exist yet — that is
blocked on Plan 152/155 (the selected artifact + Swiss data readiness), not
on this plan, and publishing a new cross-org distribution is not something
this repo's test suite can do or should fake having done.

What Plan 157 T1 owns, and what THIS file proves, is the packaging
CONTRACT any such shim must satisfy before it can be discovered, onboarded
and predicted against by SAP3:

- **G3 — zero-argument construction.** `discover_models()` calls `cls()`
  with no arguments (`services/model_registry.py:87`); the config must bind
  at construction time.
- **G15 — the NAME boundary.** aquacast's native computation uses
  `mean_temperature`; SAP3's canonical forcing name is `temperature`
  (`config/deployment.py:132`). The shim must expose `temperature` outward
  and translate internally — never the reverse.
- **G9 — the UNIT boundary.** aquacast's native discharge is `mm/day`; SAP3
  has no canonical unit for it (`_FI_UNIT_TO_CANONICAL` deliberately omits
  `MM_PER_DAY`). The shim must expose `M3_PER_S` outward and convert
  numerically, AREA-aware, in both directions — and return `ModelFailure`
  (never raise) for an invalid supplied area.

`AquacastShimReference` below is a torch-free WORKED REFERENCE — it proves
the discovery + translation + conversion MECHANISM the real shim must also
implement, using a synthetic native computation (no real ML, no trained
weights). This is consistent with the plan's own framing: "every task here
is testable against SYNTHETIC single-resolution FI models." A future real
`hydrosolutions/sapphire-aquacast` shim installs as a genuine external
entry point (mirroring how `recap-dg-client` installs today —
`Dockerfile`'s private-git-dependency pattern) and must satisfy exactly the
assertions below; nothing here is production wiring.

Red-first (plan text): "assert NUMBERS, not mappability." `M3_PER_S` and
`MM` are already valid SAP3 canonical units — a test that merely proves
`fi_unit_to_canonical` succeeds on them passes today and proves nothing.
The tests below assert a NUMERIC, area-aware round trip instead.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from sapphire_flow.adapters import forecast_interface as fi_boundary
from sapphire_flow.types.enums import AlertEligibility, ArtifactScope, ModelTier

_STEP = timedelta(hours=24)
_NATIVE_TEMPERATURE_NAME = "mean_temperature"


def mm_per_day_to_m3_per_s(value_mm_per_day: float, area_km2: float) -> float:
    """Area-aware `mm/day -> m³/s`.

    1 mm accumulated over `area_km2` km² is `area_km2 * 1_000_000 m² *
    0.001 m` = `area_km2 * 1000` m³ of volume per day; dividing by the
    86 400 seconds in a day converts that daily volume to a rate.
    """
    return value_mm_per_day * area_km2 * 1000.0 / 86400.0


def m3_per_s_to_mm_per_day(value_m3_per_s: float, area_km2: float) -> float:
    """The exact inverse of `mm_per_day_to_m3_per_s`."""
    return value_m3_per_s * 86400.0 / (area_km2 * 1000.0)


class AquacastShimReference:
    """Torch-free reference proving the T1 packaging contract — NOT the
    real `sapphire-aquacast` distribution (see module docstring)."""

    model_tier = ModelTier.SKILL
    alert_eligibility = AlertEligibility.SKILL_FORECAST
    display_name = "Aquacast Shim Reference (Plan 157 T1 contract proof)"
    description = "Torch-free reference proving the aquacast packaging contract."
    # D1: the config ships as shim package data; its hash is declared here
    # exactly as a real shim would, so Plan 157 T3's config/artifact drift
    # check has something real to compare against.
    config_hash = "aquacast-shim-reference-config-v1"

    def __init__(self) -> None:
        # Config binds at construction (G3) — a real shim would load its
        # bundled YAML package data here; this reference has none to load.
        self.artifact_scope = fi_boundary.FIArtifactScope.GROUP
        self.last_predict_internal_names: list[str] = []

    @property
    def input_requirement(self) -> fi_boundary.InputRequirement:
        return fi_boundary.InputRequirement(
            targets={
                "discharge": fi_boundary.TargetSpec(
                    unit=fi_boundary.Unit.M3_PER_S,
                    representations=frozenset(
                        {fi_boundary.OutputRepresentation.DETERMINISTIC}
                    ),
                )
            },
            dynamic={
                _STEP: fi_boundary.SpatialInputSpec(
                    data={
                        fi_boundary.FISpatialRepresentation.POINT: (
                            fi_boundary.DynamicInputSpec(
                                past_known={
                                    "obs": {
                                        # G15: the canonical SAP3 name, NOT
                                        # aquacast's native `mean_temperature`.
                                        "temperature": fi_boundary.PastKnownVariable(
                                            lookback=2,
                                            max_nan=0,
                                            unit=fi_boundary.Unit.DEG_C,
                                        ),
                                    }
                                },
                                future_known={
                                    "nwp": {
                                        "precipitation": (
                                            fi_boundary.FutureKnownVariable(
                                                future_steps=1,
                                                max_nan=0,
                                                unit=fi_boundary.Unit.MM,
                                            )
                                        ),
                                    }
                                },
                            )
                        )
                    }
                )
            },
            static=frozenset({"area"}),
        )

    def train(
        self, inputs: object, *, config: object, rng: object
    ) -> object:  # pragma: no cover
        raise NotImplementedError(
            "AquacastShimReference proves the packaging contract only — "
            "training is out of scope (Plan 152 D3: import-only for v1)"
        )

    def predict(
        self,
        artifact: object,
        *,
        inputs: fi_boundary.ModelInputs,
        issue_datetime: datetime,
        rng: object,
    ) -> fi_boundary.ModelResult:
        self.last_predict_internal_names = []
        variables: dict[str, dict[str, fi_boundary.VariableOutput]] = {}

        for station_code, station_inputs in inputs.stations.items():
            area_km2 = station_inputs.static.get("area")
            if (
                not isinstance(area_km2, (int, float))
                or isinstance(area_km2, bool)
                or not math.isfinite(area_km2)
                or area_km2 <= 0
            ):
                # G9: an INVALID supplied area is the shim's to own — return
                # ModelFailure, never raise (FI contract; a MISSING static
                # is SAP3's to reject earlier, before predict() even runs).
                return fi_boundary.ModelFailure(
                    model_name="aquacast_shim_reference",
                    issue_datetime=issue_datetime,
                    cause=fi_boundary.FailureCause.INPUT_DATA,
                    message=(
                        f"station {station_code!r}: invalid area {area_km2!r} — "
                        "area must be a positive, finite number"
                    ),
                )

            spatial = station_inputs.dynamic[_STEP].data[
                fi_boundary.FISpatialRepresentation.POINT
            ]
            # G15: read the CANONICAL "temperature" key off the FI
            # boundary — translate to the native name ONLY internally.
            temperature_series = spatial.past_known["obs"]["temperature"]
            mean_temperature = float(temperature_series.data["value"].to_list()[-1])
            self.last_predict_internal_names.append(_NATIVE_TEMPERATURE_NAME)

            precip_series = spatial.future_known["nwp"]["precipitation"]
            precip_mm = float(precip_series.data["value"].to_list()[0])

            # Synthetic native computation, deliberately NOT real hydrology —
            # this reference proves the conversion pipeline, not a trained
            # model. aquacast's real native output is mm/day.
            native_discharge_mm_per_day = max(
                precip_mm * 0.3 + mean_temperature * 0.01, 0.0
            )
            discharge_m3_per_s = mm_per_day_to_m3_per_s(
                native_discharge_mm_per_day, float(area_km2)
            )

            out_df = pl.DataFrame(
                {
                    "issue_datetime": [issue_datetime],
                    "datetime": [issue_datetime + _STEP],
                    "value": [discharge_m3_per_s],
                }
            )
            variables[station_code] = {
                "discharge": fi_boundary.VariableOutput(
                    metadata=fi_boundary.VariableMetadata(
                        unit=fi_boundary.Unit.M3_PER_S,
                        timedelta=_STEP,
                        forecast_horizon=1,
                        offset=0,
                    ),
                    deterministic=fi_boundary.DeterministicData(data=out_df),
                    status=fi_boundary.VariableStatus.SUCCESS,
                )
            }

        return fi_boundary.ModelSuccess(
            output=fi_boundary.ModelOutput(
                model_name="aquacast_shim_reference",
                issue_datetime=issue_datetime,
                variables=variables,
            )
        )

    def serialize_artifact(self, artifact: object) -> bytes:
        return b"aquacast-shim-reference-artifact"

    def deserialize_artifact(self, raw: bytes) -> object:
        return raw


def _station_inputs(
    *, area: object, temperature_value: float, precip_value: float, issue_dt: datetime
) -> fi_boundary.StationInputs:
    temp_df = pl.DataFrame(
        {
            "datetime": [issue_dt - _STEP, issue_dt],
            "value": [temperature_value - 1.0, temperature_value],
        }
    )
    precip_df = pl.DataFrame(
        {
            "datetime": [issue_dt + _STEP],
            "value": [precip_value],
        }
    )
    static: dict[str, int | float | str] = {}
    if area is not None:
        static["area"] = area  # type: ignore[assignment]
    return fi_boundary.StationInputs(
        dynamic={
            _STEP: fi_boundary.SpatialInputs(
                data={
                    fi_boundary.FISpatialRepresentation.POINT: (
                        fi_boundary.DynamicInputs(
                            past_known={
                                "obs": {
                                    "temperature": fi_boundary.InputSeries(
                                        unit=fi_boundary.Unit.DEG_C, data=temp_df
                                    )
                                }
                            },
                            future_known={
                                "nwp": {
                                    "precipitation": fi_boundary.InputSeries(
                                        unit=fi_boundary.Unit.MM, data=precip_df
                                    )
                                }
                            },
                        )
                    )
                }
            )
        },
        static=static,
    )


class TestNumericAreaAwareUnitConversion:
    """G9 — 'assert NUMBERS, not mappability' (plan text, verbatim)."""

    def test_mm_per_day_to_m3_per_s_is_area_aware(self) -> None:
        # 10 mm/day over a 100 km² catchment.
        result = mm_per_day_to_m3_per_s(10.0, 100.0)
        assert result == pytest.approx(11.574074, rel=1e-6)

        # Doubling the area must double the discharge — a bare relabel
        # (no actual conversion) would return the SAME number regardless of
        # area, which this catches.
        doubled = mm_per_day_to_m3_per_s(10.0, 200.0)
        assert doubled == pytest.approx(result * 2, rel=1e-9)

    def test_m3_per_s_to_mm_per_day_is_the_exact_inverse(self) -> None:
        original = 42.5
        area = 317.0
        round_tripped = m3_per_s_to_mm_per_day(
            mm_per_day_to_m3_per_s(original, area), area
        )
        assert round_tripped == pytest.approx(original, rel=1e-9)


class TestPredictReturnsModelFailureForInvalidArea:
    """G9 — an invalid SUPPLIED area returns ModelFailure, never raises."""

    @pytest.mark.parametrize("bad_area", [0.0, -5.0, float("nan"), float("inf")])
    def test_invalid_area_returns_model_failure_not_raise(
        self, bad_area: float
    ) -> None:
        model = AquacastShimReference()
        issue_dt = datetime(2026, 1, 1, tzinfo=UTC)
        inputs = fi_boundary.ModelInputs(
            stations={
                "station-a": _station_inputs(
                    area=bad_area,
                    temperature_value=5.0,
                    precip_value=10.0,
                    issue_dt=issue_dt,
                )
            }
        )

        result = model.predict(
            artifact=None, inputs=inputs, issue_datetime=issue_dt, rng=None
        )

        assert isinstance(result, fi_boundary.ModelFailure)
        assert result.cause is fi_boundary.FailureCause.INPUT_DATA

    def test_missing_area_key_also_returns_model_failure(self) -> None:
        model = AquacastShimReference()
        issue_dt = datetime(2026, 1, 1, tzinfo=UTC)
        inputs = fi_boundary.ModelInputs(
            stations={
                "station-a": _station_inputs(
                    area=None,
                    temperature_value=5.0,
                    precip_value=10.0,
                    issue_dt=issue_dt,
                )
            }
        )

        result = model.predict(
            artifact=None, inputs=inputs, issue_datetime=issue_dt, rng=None
        )

        assert isinstance(result, fi_boundary.ModelFailure)


class TestPredictNumericRoundTripEndToEnd:
    """The genuinely red assertion: a known discharge computation, at a
    known area, arrives at SAP3's boundary as the correct m³/s value — not
    merely a value that happens to deserialize."""

    def test_output_discharge_matches_the_area_aware_conversion_exactly(
        self,
    ) -> None:
        model = AquacastShimReference()
        issue_dt = datetime(2026, 1, 1, tzinfo=UTC)
        area_km2 = 250.0
        temperature_value = 8.0
        precip_value = 20.0
        inputs = fi_boundary.ModelInputs(
            stations={
                "station-a": _station_inputs(
                    area=area_km2,
                    temperature_value=temperature_value,
                    precip_value=precip_value,
                    issue_dt=issue_dt,
                )
            }
        )

        result = model.predict(
            artifact=None, inputs=inputs, issue_datetime=issue_dt, rng=None
        )

        assert isinstance(result, fi_boundary.ModelSuccess)
        variable = result.output.variables["station-a"]["discharge"]
        assert variable.status is fi_boundary.VariableStatus.SUCCESS
        assert variable.deterministic is not None

        actual_m3_per_s = variable.deterministic.data["value"].to_list()[0]
        expected_native_mm_per_day = precip_value * 0.3 + temperature_value * 0.01
        expected_m3_per_s = mm_per_day_to_m3_per_s(expected_native_mm_per_day, area_km2)
        assert actual_m3_per_s == pytest.approx(expected_m3_per_s, rel=1e-9)

        # Independent signal (Plan 156 G9 pattern): a DIFFERENT area must
        # produce a DIFFERENT output — catches "declared M3_PER_S but never
        # actually multiplied by area" (a bare relabel).
        larger_area_inputs = fi_boundary.ModelInputs(
            stations={
                "station-a": _station_inputs(
                    area=area_km2 * 3,
                    temperature_value=temperature_value,
                    precip_value=precip_value,
                    issue_dt=issue_dt,
                )
            }
        )
        larger_result = model.predict(
            artifact=None, inputs=larger_area_inputs, issue_datetime=issue_dt, rng=None
        )
        assert isinstance(larger_result, fi_boundary.ModelSuccess)
        larger_m3_per_s = (
            larger_result.output.variables["station-a"]["discharge"]
            .deterministic.data["value"]
            .to_list()[0]
        )
        assert larger_m3_per_s == pytest.approx(actual_m3_per_s * 3, rel=1e-9)


class TestPredictTranslatesTheNameBoundary:
    """G15 — the shim exposes canonical `temperature` outward and
    translates to aquacast's native `mean_temperature` ONLY internally."""

    def test_input_requirement_declares_canonical_temperature_not_native_name(
        self,
    ) -> None:
        model = AquacastShimReference()
        spec = next(iter(model.input_requirement.dynamic.values()))
        point = spec.data[fi_boundary.FISpatialRepresentation.POINT]
        past_names = {name for group in point.past_known.values() for name in group}
        assert "temperature" in past_names
        assert _NATIVE_TEMPERATURE_NAME not in past_names

    def test_predict_reads_the_canonical_key_and_records_the_native_translation(
        self,
    ) -> None:
        model = AquacastShimReference()
        issue_dt = datetime(2026, 1, 1, tzinfo=UTC)
        inputs = fi_boundary.ModelInputs(
            stations={
                "station-a": _station_inputs(
                    area=100.0,
                    temperature_value=12.0,
                    precip_value=5.0,
                    issue_dt=issue_dt,
                )
            }
        )

        result = model.predict(
            artifact=None, inputs=inputs, issue_datetime=issue_dt, rng=None
        )

        assert isinstance(result, fi_boundary.ModelSuccess)
        # predict() only succeeds by reading the "temperature" key off the
        # FI boundary (there is no "mean_temperature" key to read) — the
        # translation happens ONLY internally, recorded here for the test.
        assert model.last_predict_internal_names == [_NATIVE_TEMPERATURE_NAME]


class TestPositiveDiscovery:
    """G3 — the shim's model IS discoverable (a positive assertion, not
    merely 'constructs without raising') — Plan 156 made an unsupported
    entry point SILENTLY absent from discover_models() rather than a loud
    failure, so a passing construction test alone would prove nothing about
    whether the model actually surfaces to callers."""

    def test_discover_models_finds_the_shim_via_its_entry_point(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sapphire_flow.services.model_registry import discover_models
        from sapphire_flow.types.ids import ModelId

        class _AquacastEntryPoint:
            name = "aquacast_shim_reference"

            def load(self) -> type[AquacastShimReference]:
                return AquacastShimReference

        monkeypatch.setattr(
            "importlib.metadata.entry_points",
            lambda group: [_AquacastEntryPoint()],  # noqa: ARG005
        )

        discovered = discover_models()

        model_id = ModelId("aquacast_shim_reference")
        assert model_id in discovered
        adapted = discovered[model_id]
        assert isinstance(adapted, fi_boundary.ForecastInterfaceAdapter)
        assert adapted.artifact_scope is ArtifactScope.GROUP
        assert adapted.data_requirements.target_parameters == frozenset({"discharge"})
        assert adapted.data_requirements.static_features == frozenset({"area"})
        # G15 — data_requirements is projected from input_requirement, which
        # declares the canonical name.
        assert "temperature" in adapted.data_requirements.past_dynamic_features
