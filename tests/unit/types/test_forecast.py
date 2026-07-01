from __future__ import annotations

import random
from datetime import UTC, datetime
from uuid import uuid4

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForecastStatus,
    NwpCycleSource,
)
from sapphire_flow.types.forecast import ForecastProvenance, OperationalForecast
from sapphire_flow.types.ids import ForecastId, ModelId, StationId
from tests.conftest import make_forecast_ensemble

_EPOCH = ensure_utc(datetime(2025, 1, 1, tzinfo=UTC))
_CYCLE = ensure_utc(datetime(2024, 12, 31, 18, tzinfo=UTC))


def _make_forecast(
    *,
    source: NwpCycleSource,
    reference_time: datetime | None,
) -> OperationalForecast:
    station_id = StationId(uuid4())
    ensemble = make_forecast_ensemble(
        station_id=station_id,
        representation=EnsembleRepresentation.MEMBERS,
        n_members=3,
        n_steps=5,
        rng=random.Random(7),
    )
    return OperationalForecast(
        id=ForecastId(uuid4()),
        station_id=station_id,
        model_id=ModelId("m"),
        model_artifact_id=None,
        issued_at=_EPOCH,
        nwp_cycle_reference_time=reference_time,  # type: ignore[arg-type]
        nwp_cycle_source=source,
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


class TestForecastProvenanceRecord:
    """M4: forward-compatible provenance value object at the domain level."""

    def test_runoff_only_has_null_reference_time(self) -> None:
        prov = ForecastProvenance(
            nwp_cycle_source=NwpCycleSource.RUNOFF_ONLY,
            nwp_cycle_reference_time=None,
        )
        assert prov.nwp_cycle_source is NwpCycleSource.RUNOFF_ONLY
        assert prov.nwp_cycle_reference_time is None

    def test_primary_carries_real_reference_time(self) -> None:
        prov = ForecastProvenance(
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            nwp_cycle_reference_time=_CYCLE,
        )
        assert prov.nwp_cycle_source is NwpCycleSource.PRIMARY
        assert prov.nwp_cycle_reference_time == _CYCLE

    def test_fallback_source(self) -> None:
        prov = ForecastProvenance(
            nwp_cycle_source=NwpCycleSource.FALLBACK,
            nwp_cycle_reference_time=_CYCLE,
        )
        assert prov.nwp_cycle_source is NwpCycleSource.FALLBACK
        assert prov.nwp_cycle_reference_time == _CYCLE

    def test_record_is_frozen_value_object(self) -> None:
        a = ForecastProvenance(
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            nwp_cycle_reference_time=_CYCLE,
        )
        b = ForecastProvenance(
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            nwp_cycle_reference_time=_CYCLE,
        )
        assert a == b


class TestOperationalForecastProvenance:
    """The domain forecast exposes source + a NULLABLE reference time."""

    def test_runoff_only_forecast_has_null_reference_time(self) -> None:
        fc = _make_forecast(
            source=NwpCycleSource.RUNOFF_ONLY,
            reference_time=None,
        )
        assert fc.nwp_cycle_source is NwpCycleSource.RUNOFF_ONLY
        assert fc.nwp_cycle_reference_time is None

    def test_primary_forecast_carries_reference_time(self) -> None:
        fc = _make_forecast(
            source=NwpCycleSource.PRIMARY,
            reference_time=_CYCLE,
        )
        assert fc.nwp_cycle_source is NwpCycleSource.PRIMARY
        assert fc.nwp_cycle_reference_time == _CYCLE

    def test_provenance_property_reflects_runoff_only(self) -> None:
        fc = _make_forecast(
            source=NwpCycleSource.RUNOFF_ONLY,
            reference_time=None,
        )
        assert fc.provenance == ForecastProvenance(
            nwp_cycle_source=NwpCycleSource.RUNOFF_ONLY,
            nwp_cycle_reference_time=None,
        )

    def test_provenance_property_reflects_primary(self) -> None:
        fc = _make_forecast(
            source=NwpCycleSource.PRIMARY,
            reference_time=_CYCLE,
        )
        assert fc.provenance == ForecastProvenance(
            nwp_cycle_source=NwpCycleSource.PRIMARY,
            nwp_cycle_reference_time=_CYCLE,
        )
