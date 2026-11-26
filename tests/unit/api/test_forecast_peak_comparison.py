from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from fastapi.responses import JSONResponse

from sapphire_flow.api import app
from sapphire_flow.api.forecast_peak_comparison import (
    ForecastPeakReader,
    get_forecast_peak_reader,
)
from sapphire_flow.api.provisional_forecasts import (
    ProvisionalHeader,
    get_provisional_reader,
    rating_metadata,
)
from sapphire_flow.api.publication_gate import PublicationGate, get_publication_gate
from sapphire_flow.api.security import require_principal
from sapphire_flow.config.provisional_rating import NativeRatingBinding
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    AccessTokenRole,
    AggregationMethod,
    EnsembleRepresentation,
    InterpolationMethod,
)
from sapphire_flow.types.forecast_period import ForecastPeriodDefinition
from sapphire_flow.types.ids import RatingCurveId, StationId
from sapphire_flow.types.rating_reference import (
    curve_snapshot,
)
from sapphire_flow.types.time_grid import PeriodLabel, TimeGrid
from tests.conftest import make_station_config
from tests.fakes.fake_stores import FakeStationStore
from tests.unit.api.conftest import ADMIN_PRINCIPAL
from tests.unit.api.test_forecast_publication_api import FakePublicationStore
from tests.unit.api.test_provisional_forecasts import case
from tests.unit.services.test_forecast_peaks import AT, STATION, forecast
from tests.unit.services.test_provisional_discharge import inputs
from tests.unit.types.test_rating_reference import assumed_reference

if TYPE_CHECKING:
    from sapphire_flow.types.rating_curve import RatingCurve


class CurveStore:
    def __init__(self, curve: RatingCurve | None) -> None:
        self.curve, self.reads = curve, 0

    def fetch_curve_by_id(
        self, station_id: StationId, curve_id: RatingCurveId
    ) -> RatingCurve | None:
        self.reads += 1
        assert station_id == STATION
        assert self.curve is None or curve_id == self.curve.id
        return self.curve


def setup_reader() -> tuple[ForecastPeakReader, NativeRatingBinding, CurveStore]:
    _, source_curve, _, source_reference = inputs()
    curve = replace(
        source_curve,
        station_id=STATION,
        valid_to=None,
        points=[
            {"water_level": 1.0, "discharge": 10.0},
            {"water_level": 3.0, "discharge": 90.0},
        ],
    )
    reference = replace(
        source_reference, station_id=STATION, curve=curve_snapshot(curve), offset_m=0.25
    )
    item = forecast([[1, 2, 3, 50, 60, 70, 100]])
    station = replace(
        make_station_config(station_id=STATION, code="example"),
        tenant_id=reference.tenant_id,
    )
    binding = NativeRatingBinding(
        tenant_id=station.tenant_id,
        station_id=STATION,
        station_code=station.code,
        model_ids=frozenset({item.model_id}),
        reference=reference,
        custody_start=None,
        custody_end=None,
        custody_reference=None,
        reconciliation_days=7,
    )
    stations = FakeStationStore()
    stations.store_station(station)
    curves = CurveStore(curve)
    return (
        ForecastPeakReader(
            stores={"station_store": stations, "rating_curve_store": curves},
            bindings=(binding,),
            as_of=AT,
        ),
        binding,
        curves,
    )


class TestComparisonReader:
    def test_large_finite_members_remain_json_serializable(self) -> None:
        reader, _, _ = setup_reader()
        result = reader.read(
            forecast([[1e308] * 5], representation=EnsembleRepresentation.MEMBERS)
        )
        response = JSONResponse(content=result.model_dump(mode="json"))
        assert [
            p["discharge"] for p in json.loads(response.body)["remaining_peaks"]
        ] == [1e308, 1e308]

    @pytest.mark.parametrize("frame", ["gauge_zero", "masl", "assumed"])
    def test_output_frame_is_measured_frame_not_curve_frame(self, frame: str) -> None:
        reader, binding, _ = setup_reader()
        proof = binding.reference
        reference = (
            assumed_reference(proof)
            if frame == "assumed"
            else replace(
                proof,
                level_reference=frame,
                curve_reference="masl" if frame == "gauge_zero" else "gauge_zero",
            )
        )
        reader.bindings = (replace(binding, reference=reference),)
        result = reader.read(forecast([[1, 2, 3, 50, 60, 70, 100]]))
        peak = result.remaining_peaks[0]
        assert peak.water_level_reference == (None if frame == "assumed" else frame)
        assert peak.water_level_equivalent == (2.0 if frame == "assumed" else 1.75)
        assert result.reference_basis == (
            "owner_assumed_same_zero" if frame == "assumed" else "verified"
        )
        if frame == "assumed":
            assert "owner_assumed_same_zero_unverified" in peak.warnings

    def test_maximum_support_does_not_multiply_curve_reads(self) -> None:
        reader, _, curves = setup_reader()
        result = reader.read(forecast([[1, 2, 3, 50, 60, 70, 100]] * 512))
        assert result.remaining_peaks[1].discharge == 70
        assert curves.reads == 1

    def test_offset_and_safe_fields_for_both_peaks(self) -> None:
        reader, _, curves = setup_reader()
        result = reader.read(forecast([[1, 2, 3, 50, 60, 70, 100]]))
        assert [p.water_level_equivalent for p in result.remaining_peaks] == [
            1.75,
            2.25,
        ]
        assert result.remaining_peaks[0].water_level_reference == "gauge_zero"
        assert curves.reads == 1
        assert result.data_use == "provisional_rating"
        text = result.model_dump_json()
        for forbidden in (
            "uploaded_by",
            "points",
            "endpoint",
            "evidence_reference",
            "sha256",
            "example.invalid",
        ):
            assert forbidden not in text

    def test_missing_p90_does_not_remove_median_or_claim_an_upper_level(self) -> None:
        reader, _, _ = setup_reader()
        item = forecast(
            [[10, 20, 30]],
            representation=EnsembleRepresentation.MEMBERS,
        )
        result = reader.read(item)
        assert result.remaining_peaks[0].water_level_equivalent is not None
        assert result.remaining_peaks[1].conversion_status == "insufficient_members"

    @pytest.mark.parametrize(
        "change,reason",
        [({"valid_to": AT}, "curve_changed"), ({"created_at": AT}, "curve_changed")],
    )
    def test_snapshot_changes_refuse(self, change: dict[str, Any], reason: str) -> None:
        reader, _, curves = setup_reader()
        curves.curve = replace(curves.curve, **change)
        result = reader.read(forecast([[1, 2, 3, 50, 60, 70, 100]]))
        assert result.comparison_unavailable_reason == reason
        assert all(p.water_level_equivalent is None for p in result.remaining_peaks)

    @pytest.mark.parametrize(
        "scenario,reason",
        [
            ("absent", "reference_not_configured"),
            ("duplicate", "reference_ambiguous"),
            ("code", "reference_scope_mismatch"),
            ("future", "reference_from_future"),
            ("missing_curve", "curve_missing"),
        ],
    )
    def test_refusal_reasons(self, scenario: str, reason: str) -> None:
        reader, binding, curves = setup_reader()
        if scenario == "absent":
            reader.bindings = ()
        if scenario == "duplicate":
            reader.bindings = (binding, binding)
        if scenario == "code":
            reader.bindings = (replace(binding, station_code="different"),)
        if scenario == "future":
            reader.bindings = (
                replace(
                    binding,
                    reference=replace(
                        binding.reference,
                        verified_at=ensure_utc(AT + timedelta(seconds=1)),
                    ),
                ),
            )
        if scenario == "missing_curve":
            curves.curve = None
        assert (
            reader.read(
                forecast([[1, 2, 3, 50, 60, 70, 100]])
            ).comparison_unavailable_reason
            == reason
        )

    @pytest.mark.parametrize("method", list(InterpolationMethod))
    def test_provisional_policy_is_linear_only(
        self, method: InterpolationMethod
    ) -> None:
        reader, binding, curves = setup_reader()
        curves.curve = replace(curves.curve, interpolation=method)
        reader.bindings = (
            replace(
                binding,
                reference=replace(
                    binding.reference, curve=curve_snapshot(curves.curve)
                ),
            ),
        )
        result = reader.read(forecast([[1, 2, 3, 50, 60, 70, 100]]))
        assert result.comparison_unavailable_reason == (
            None
            if method is InterpolationMethod.LINEAR
            else "unsupported_interpolation"
        )

    @pytest.mark.parametrize(
        "valid_from,valid_to,expected",
        [
            (AT, AT, "expired"),
            (AT, None, "valid"),
            (ensure_utc(AT + timedelta(days=1)), None, "not_yet_valid"),
        ],
    )
    def test_exact_curve_validity_boundaries(
        self, valid_from: Any, valid_to: Any, expected: str
    ) -> None:
        reader, binding, curves = setup_reader()
        curves.curve = replace(curves.curve, valid_from=valid_from, valid_to=valid_to)
        reader.bindings = (
            replace(
                binding,
                reference=replace(
                    binding.reference,
                    curve=curve_snapshot(curves.curve),
                    verified_at=AT,
                ),
            ),
        )
        result = reader.read(forecast([[1, 2, 3, 50, 60, 70, 100]]))
        assert result.curve.validity == expected
        assert (result.remaining_peaks[0].water_level_equivalent is None) == (
            expected == "not_yet_valid"
        )

    def test_range_refusal_is_per_peak(self) -> None:
        reader, _, _ = setup_reader()
        result = reader.read(forecast([[1, 2, 3, 50, 60, 100, 110]]))
        assert result.remaining_peaks[0].water_level_equivalent == 1.75
        assert result.remaining_peaks[1].water_level_equivalent is None
        assert result.remaining_peaks[1].range_flag == "above"

    def test_daily_period_crossing_expiry_is_disclosed(self) -> None:
        reader, binding, curves = setup_reader()
        curves.curve = replace(
            curves.curve, valid_to=ensure_utc(AT + timedelta(hours=3))
        )
        reader.bindings = (
            replace(
                binding,
                reference=replace(
                    binding.reference, curve=curve_snapshot(curves.curve)
                ),
            ),
        )
        period = ForecastPeriodDefinition(
            grid=TimeGrid(step=timedelta(days=1), phase=timedelta(hours=18)),
            label=PeriodLabel.START,
            aggregation=AggregationMethod.MEAN,
        )
        result = reader.read(forecast([[1, 2, 3, 50, 60, 70, 100]], period=period))
        assert result.remaining_peaks[0].curve_validity == "crosses_expiry"
        assert result.remaining_peaks[0].warnings == ["rating_crosses_expiry_at_peak"]
        assert result.remaining_peaks[0].water_level_equivalent == 1.75


class TestComparisonRoute:
    @pytest.mark.parametrize("admitted", [True, False])
    def test_provisional_parent_admission_precedes_enrichment(
        self, client: Any, fake_stores: dict[str, Any], admitted: bool
    ) -> None:
        from fastapi import HTTPException

        original, row, _ = case()
        reader, binding, curves = setup_reader()
        item = replace(
            original, station_id=STATION, model_id=next(iter(binding.model_ids))
        )
        metadata = rating_metadata(ProvisionalHeader.from_row(row), item.input_lineage)

        class ProvisionalReader:
            store = fake_stores["forecast_store"]

            def detail(self, _: Any) -> Any:
                if not admitted:
                    raise HTTPException(404, "Forecast not found")
                return item, metadata

        app.dependency_overrides[get_provisional_reader] = ProvisionalReader
        app.dependency_overrides[get_forecast_peak_reader] = lambda: reader
        response = client.get(
            f"/api/v1/forecasts/{item.id}?data_use=provisional_rating&rating_comparison=provisional_rating"
        )
        assert response.status_code == (200 if admitted else 404)
        assert curves.reads == (1 if admitted else 0)
        if admitted:
            assert response.json()["data_use"] == "provisional_rating"
            assert (
                response.json()["peak_water_level_comparison"]["data_use"]
                == "provisional_rating"
            )

    @pytest.mark.parametrize(
        "mode",
        ["no_gate", "inactive", "published", "hidden", "withdrawn", "out_of_scope"],
    )
    def test_admission_before_curve_read(
        self, client: Any, fake_stores: dict[str, Any], mode: str
    ) -> None:
        reader, binding, curves = setup_reader()
        station = reader.stores["station_store"].fetch_station(STATION)
        fake_stores["station_store"].store_station(station)
        item = forecast([[1, 2, 3, 50, 60, 70, 100]])
        fake_stores["forecast_store"].store_forecast(item)
        app.dependency_overrides[get_forecast_peak_reader] = lambda: reader
        active = mode in {"published", "hidden", "withdrawn"}
        app.dependency_overrides[get_publication_gate] = lambda: PublicationGate(
            active_tenant_ids=frozenset({binding.tenant_id})
            if active
            else frozenset({UUID(int=999)})
            if mode == "inactive"
            else frozenset()
        )
        pub = FakePublicationStore(fake_stores["forecast_store"])
        fake_stores["publication_store"] = pub
        if mode in {"published", "withdrawn"}:
            pub.add_publish(item, binding.tenant_id)
        if mode == "withdrawn":
            pub.add_withdraw(item)
        if mode == "out_of_scope":
            app.dependency_overrides[require_principal] = lambda: replace(
                ADMIN_PRINCIPAL,
                role=AccessTokenRole.REVIEWER,
                station_ids=frozenset({StationId(UUID(int=99))}),
            )
        response = client.get(
            f"/api/v1/forecasts/{item.id}?rating_comparison=provisional_rating"
        )
        expected = (
            404
            if mode in {"hidden", "out_of_scope"}
            else 410
            if mode == "withdrawn"
            else 200
        )
        assert response.status_code == expected
        assert curves.reads == (1 if expected == 200 else 0)
        assert "no-store" in response.headers["cache-control"]
        if expected != 200:
            assert "peak_water_level_comparison" not in response.json()

    def test_consumer_denied_before_parent_lookup(
        self, client: Any, fake_stores: dict[str, Any]
    ) -> None:
        app.dependency_overrides[require_principal] = lambda: replace(
            ADMIN_PRINCIPAL, role=AccessTokenRole.CONSUMER
        )

        class ForbiddenStore:
            def fetch_forecast(self, _: Any) -> Any:
                raise AssertionError("parent lookup before role admission")

        fake_stores["forecast_store"] = ForbiddenStore()
        assert (
            client.get(
                f"/api/v1/forecasts/{UUID(int=3)}?rating_comparison=provisional_rating"
            ).status_code
            == 403
        )

    def test_default_does_not_read_curve(
        self, client: Any, fake_stores: dict[str, Any]
    ) -> None:
        reader, _, curves = setup_reader()
        app.dependency_overrides[get_forecast_peak_reader] = lambda: reader
        item = forecast([[1, 2, 3, 50, 60, 70, 100]])
        fake_stores["forecast_store"].store_forecast(item)
        response = client.get(f"/api/v1/forecasts/{item.id}")
        assert response.json()["peak_water_level_comparison"] is None
        assert curves.reads == 0
