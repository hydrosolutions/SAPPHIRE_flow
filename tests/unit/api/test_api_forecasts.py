from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from fastapi import HTTPException

from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import InputQualityFlag
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForecastDataUse,
    ForecastStatus,
    InputQualityCategory,
    InputQualityLevel,
    NwpCycleSource,
)
from sapphire_flow.types.ids import (
    ArtifactId,
    ForecastId,
    ModelId,
    StationId,
)
from tests.conftest import make_forecast_ensemble, make_station_config

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))


def _make_operational_forecast(
    *,
    station_id: StationId,
    issued_at: UtcDatetime | None = None,
    model_artifact_id: ArtifactId | None = None,
    rng: random.Random | None = None,
) -> Any:
    from sapphire_flow.types.forecast import OperationalForecast

    rng = rng or random.Random(99)
    iat = issued_at or _EPOCH
    ensemble = make_forecast_ensemble(
        station_id=station_id, rng=rng, n_members=3, n_steps=5
    )
    return OperationalForecast(
        id=ForecastId(uuid4()),
        station_id=station_id,
        model_id=ModelId("test_model"),
        model_artifact_id=model_artifact_id,
        issued_at=iat,
        nwp_cycle_reference_time=iat,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        representation=EnsembleRepresentation.MEMBERS,
        status=ForecastStatus.RAW,
        version=1,
        warm_up_source=None,
        warm_up_state_age_hours=None,
        observation_staleness_hours=None,
        ensemble=ensemble,
        created_at=iat,
        updated_at=iat,
    )


class TestGetForecast:
    def test_found(self, client: TestClient, fake_stores: dict[str, Any]) -> None:
        station = make_station_config(rng=random.Random(1))
        fc = _make_operational_forecast(station_id=station.id, rng=random.Random(2))
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(fc.id)
        assert body["station_id"] == str(station.id)
        assert body["model_id"] == str(fc.model_id)
        assert body["model_tier"] == "skill"
        assert body["representation"] == fc.representation.value
        assert body["status"] == fc.status.value
        assert body["version"] == fc.version
        assert body["nwp_cycle_source"] == fc.nwp_cycle_source.value
        assert body["model_artifact_id"] is None
        assert body["warm_up_source"] is None
        assert body["combination_strategy"] is None
        assert body["source_model_ids"] is None

    def test_fallback_model_exposes_model_tier(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        from dataclasses import replace

        station = make_station_config(rng=random.Random(1))
        fc = replace(
            _make_operational_forecast(station_id=station.id, rng=random.Random(2)),
            model_id=ModelId("climatology_fallback"),
        )
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        assert resp.status_code == 200
        assert resp.json()["model_tier"] == "fallback"

    def test_ensemble_shape(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config(rng=random.Random(1))
        fc = _make_operational_forecast(station_id=station.id, rng=random.Random(2))
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        body = resp.json()
        ens = body["ensemble"]
        assert ens["representation"] == "members"
        assert ens["parameter"] == fc.ensemble.parameter
        assert ens["units"] == fc.ensemble.units
        assert ens["forecast_horizon_steps"] == fc.ensemble.forecast_horizon_steps
        assert ens["time_step_seconds"] == int(fc.ensemble.time_step.total_seconds())
        assert ens["member_count"] == fc.ensemble.member_count
        assert isinstance(ens["valid_times"], list)
        assert len(ens["valid_times"]) == fc.ensemble.forecast_horizon_steps
        assert isinstance(ens["series"], dict)
        assert len(ens["series"]) == fc.ensemble.member_count
        for _member_key, values in ens["series"].items():
            assert len(values) == fc.ensemble.forecast_horizon_steps

    def test_runoff_only_exposes_null_reference_time(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        # epic-088 M4: a runoff-only forecast serialises source "runoff_only"
        # and a NULL nwp_cycle_reference_time. RED on main: the schema pins
        # nwp_cycle_reference_time: datetime (non-optional) and NwpCycleSource
        # has no RUNOFF_ONLY, so a null reference triggers a 500.
        from sapphire_flow.types.forecast import OperationalForecast

        station = make_station_config(rng=random.Random(1))
        ensemble = make_forecast_ensemble(
            station_id=station.id, rng=random.Random(5), n_members=3, n_steps=5
        )
        fc = OperationalForecast(
            id=ForecastId(uuid4()),
            station_id=station.id,
            model_id=ModelId("runoff_only_model"),
            model_artifact_id=None,
            issued_at=_EPOCH,
            nwp_cycle_reference_time=None,  # type: ignore[arg-type]
            nwp_cycle_source=NwpCycleSource.RUNOFF_ONLY,
            representation=EnsembleRepresentation.MEMBERS,
            status=ForecastStatus.RAW,
            version=1,
            warm_up_source=None,
            warm_up_state_age_hours=None,
            observation_staleness_hours=None,
            ensemble=ensemble,
            created_at=_EPOCH,
            updated_at=_EPOCH,
        )
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["nwp_cycle_source"] == "runoff_only"
        assert body["nwp_cycle_reference_time"] is None

    def test_not_found(self, client: TestClient) -> None:
        resp = client.get(f"/api/v1/forecasts/{uuid4()}")
        assert resp.status_code == 404
        body = resp.json()
        assert "error" in body

    def test_with_artifact_id(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config(rng=random.Random(1))
        artifact_id = ArtifactId(uuid4())
        fc = _make_operational_forecast(
            station_id=station.id,
            model_artifact_id=artifact_id,
            rng=random.Random(2),
        )
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["model_artifact_id"] == str(artifact_id)

    def test_quantile_representation(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        from sapphire_flow.types.forecast import OperationalForecast

        station = make_station_config(rng=random.Random(1))
        rng = random.Random(3)
        ensemble = make_forecast_ensemble(
            station_id=station.id,
            representation=EnsembleRepresentation.QUANTILES,
            rng=rng,
            n_steps=5,
        )
        fc = OperationalForecast(
            id=ForecastId(uuid4()),
            station_id=station.id,
            model_id=ModelId("quant_model"),
            model_artifact_id=None,
            issued_at=_EPOCH,
            nwp_cycle_reference_time=_EPOCH,
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            representation=EnsembleRepresentation.QUANTILES,
            status=ForecastStatus.RAW,
            version=1,
            warm_up_source=None,
            warm_up_state_age_hours=None,
            observation_staleness_hours=None,
            ensemble=ensemble,
            created_at=_EPOCH,
            updated_at=_EPOCH,
        )
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        assert resp.status_code == 200
        ens = resp.json()["ensemble"]
        assert ens["representation"] == "quantiles"
        assert isinstance(ens["series"], dict)
        for _q_key, values in ens["series"].items():
            assert len(values) == 5


class TestForecastDetailQcFlags:
    """Plan 402 T3: like input_quality (Plan 253 T1c), `_to_forecast_detail`
    builds its response explicitly — it does not inherit `qc_flags` wiring
    for free from `_to_forecast_summary`, so it needs its own proof."""

    def test_flagged_forecast_returns_its_flags(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        from dataclasses import replace

        from sapphire_flow.types.domain import QcFlag
        from sapphire_flow.types.enums import QcStatus

        station = make_station_config(rng=random.Random(1))
        fc = replace(
            _make_operational_forecast(station_id=station.id, rng=random.Random(2)),
            qc_flags=(
                QcFlag(
                    rule_id="range_check",
                    rule_version="1.0",
                    status=QcStatus.QC_SUSPECT,
                    detail="forecast flag",
                ),
            ),
        )
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        assert resp.status_code == 200
        flags = resp.json()["qc_flags"]
        assert len(flags) == 1
        assert flags[0] == {
            "rule_id": "range_check",
            "rule_version": "1.0",
            "status": "qc_suspect",
            "detail": "forecast flag",
        }

    def test_unflagged_forecast_returns_empty_list(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config(rng=random.Random(1))
        fc = _make_operational_forecast(station_id=station.id, rng=random.Random(2))
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        assert resp.json()["qc_flags"] == []

    def test_consumer_sees_qc_flags_too(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        """D13: qc_flags is visible to every authenticated role."""
        from sapphire_flow.api import app
        from sapphire_flow.api.security import Principal, require_principal
        from sapphire_flow.types.enums import AccessTokenRole
        from sapphire_flow.types.ids import AccessTokenId

        station = make_station_config(rng=random.Random(1))
        fc = _make_operational_forecast(station_id=station.id, rng=random.Random(2))
        fake_stores["forecast_store"].store_forecast(fc)

        app.dependency_overrides[require_principal] = lambda: Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.CONSUMER,
            tenant_id=None,
            station_ids=frozenset({station.id}),
        )
        try:
            resp = client.get(f"/api/v1/forecasts/{fc.id}")
        finally:
            app.dependency_overrides.pop(require_principal, None)
        assert resp.status_code == 200
        assert "qc_flags" in resp.json()


class TestForecastDetailInputQuality:
    """Plan 253 T1c: the detail serializer is separate from ForecastSummary
    and builds its response explicitly — it does NOT inherit the fields for
    free, so it needs the same wiring proven independently here."""

    def test_degraded_forecast_exposes_level_and_flags(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        from dataclasses import replace

        station = make_station_config(rng=random.Random(1))
        flags = (
            InputQualityFlag(
                category=InputQualityCategory.NWP,
                level=InputQualityLevel.PARTIAL,
                detail="NWP 10.0h stale (threshold: 6.0h)",
            ),
        )
        fc = replace(
            _make_operational_forecast(station_id=station.id, rng=random.Random(2)),
            input_quality=InputQualityLevel.DEGRADED,
            input_quality_flags=flags,
        )
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["input_quality"] == "degraded"
        assert body["input_quality_flags"] == [
            {
                "category": "nwp",
                "level": "partial",
                "detail": "NWP 10.0h stale (threshold: 6.0h)",
            }
        ]

    def test_legacy_forecast_serialises_as_null(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config(rng=random.Random(1))
        fc = _make_operational_forecast(station_id=station.id, rng=random.Random(2))
        assert fc.input_quality is None
        fake_stores["forecast_store"].store_forecast(fc)

        resp = client.get(f"/api/v1/forecasts/{fc.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["input_quality"] is None
        assert body["input_quality_flags"] is None


class TestSupersededForecastOverTheApi:
    """Plan 328 T3 — the API surfaces, asserted individually.

    ⛔ A dispositions table in a doc is not a test. Each of these is a reader
    the inventory calls HISTORICAL; if one silently started filtering, a
    superseded forecast's evidence would become unreachable over HTTP.
    """

    def _superseded_and_replacement(
        self, station_id: StationId, fake_stores: dict[str, Any]
    ) -> tuple[Any, Any]:
        original = _make_operational_forecast(
            station_id=station_id, rng=random.Random(11)
        )
        fake_stores["forecast_store"].store_forecast(original)
        replacement = _make_operational_forecast(
            station_id=station_id, rng=random.Random(12)
        )
        fake_stores["forecast_store"].store_forecast(replacement)
        return original, replacement

    def test_by_id_still_serves_it_and_marks_it_superseded(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        station = make_station_config(rng=random.Random(1))
        original, replacement = self._superseded_and_replacement(
            station.id, fake_stores
        )

        resp = client.get(f"/api/v1/forecasts/{original.id}")

        assert resp.status_code == 200, "by-id access must be PRESERVED"
        assert resp.json()["status"] == ForecastStatus.SUPERSEDED.value
        assert client.get(f"/api/v1/forecasts/{replacement.id}").json()["status"] == (
            ForecastStatus.RAW.value
        )

    def test_the_ensemble_of_a_superseded_forecast_is_still_served(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        """Its values are what the retained evidence is evidence OF."""
        station = make_station_config(rng=random.Random(1))
        original, _ = self._superseded_and_replacement(station.id, fake_stores)

        body = client.get(f"/api/v1/forecasts/{original.id}").json()

        assert body["ensemble"]["series"], "a superseded forecast keeps its values"
        assert body["ensemble"]["valid_times"]


class TestOrdinaryForecastReadBoundary:
    @pytest.mark.parametrize(
        "purpose", [None, "standard", ForecastDataUse.EXPIRED_RATING_TEST]
    )
    def test_wrong_purpose_refuses_before_detail_read(
        self, client: TestClient, fake_stores: dict[str, Any], purpose: object
    ) -> None:
        class Store:
            data_use = purpose

            def fetch_forecast(self, forecast_id: object) -> None:
                raise AssertionError("forecast query before purpose refusal")

        fake_stores["forecast_store"] = Store()
        response = client.get(f"/api/v1/forecasts/{uuid4()}")
        assert response.status_code == 503
        assert response.json() == {
            "error": "Ordinary forecast reads are unavailable",
            "detail": None,
        }
        assert response.headers["cache-control"] == "no-store"

    def test_unknown_purpose_refuses_before_detail_read(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        class Store:
            def fetch_forecast(self, forecast_id: object) -> None:
                raise AssertionError("forecast query before purpose refusal")

        fake_stores["forecast_store"] = Store()
        assert client.get(f"/api/v1/forecasts/{uuid4()}").status_code == 503

    @pytest.mark.parametrize(
        "purpose", [None, "standard", ForecastDataUse.EXPIRED_RATING_TEST]
    )
    def test_wrong_result_has_absent_body_without_reading_fields(
        self, client: TestClient, fake_stores: dict[str, Any], purpose: object
    ) -> None:
        class Result:
            data_use = purpose

            def __getattr__(self, name: str) -> object:
                raise AssertionError("protected-value-canary accessed: " + name)

        class Store:
            data_use = ForecastDataUse.STANDARD

            def fetch_forecast(self, forecast_id: object) -> Result:
                return Result()

        absent = client.get(f"/api/v1/forecasts/{uuid4()}")
        fake_stores["forecast_store"] = Store()
        response = client.get(f"/api/v1/forecasts/{uuid4()}")
        assert response.status_code == 404
        assert response.json() == absent.json()

    def test_detail_serializer_refuses_before_canary_fields(self) -> None:
        from sapphire_flow.api.routes.api_forecasts import to_forecast_detail

        class Result:
            data_use = ForecastDataUse.EXPIRED_RATING_TEST

            def __getattr__(self, name: str) -> object:
                raise AssertionError("protected-value-canary accessed: " + name)

        with pytest.raises(HTTPException, match="Forecast not found") as exc:
            to_forecast_detail(Result())  # type: ignore[arg-type]
        assert exc.value.status_code == 404


class TestForecastReadAuthorizationOrder:
    def test_missing_bearer_precedes_unknown_store(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        from sapphire_flow.api import app
        from sapphire_flow.api.security import require_principal

        app.dependency_overrides.pop(require_principal)
        fake_stores["forecast_store"] = object()
        assert client.get(f"/api/v1/forecasts/{uuid4()}").status_code == 401

    def test_healthy_standard_foreign_result_has_absent_body(
        self, client: TestClient, fake_stores: dict[str, Any]
    ) -> None:
        from sapphire_flow.api import app
        from sapphire_flow.api.security import Principal, require_principal
        from sapphire_flow.types.enums import AccessTokenRole
        from sapphire_flow.types.ids import AccessTokenId

        fc = _make_operational_forecast(station_id=StationId(uuid4()))
        fake_stores["forecast_store"].store_forecast(fc)
        app.dependency_overrides[require_principal] = lambda: Principal(
            token_id=AccessTokenId(uuid4()),
            role=AccessTokenRole.CONSUMER,
            tenant_id=None,
            station_ids=frozenset(),
        )
        absent = client.get(f"/api/v1/forecasts/{uuid4()}")
        foreign = client.get(f"/api/v1/forecasts/{fc.id}")
        assert foreign.status_code == absent.status_code == 404
        assert foreign.json() == absent.json()
