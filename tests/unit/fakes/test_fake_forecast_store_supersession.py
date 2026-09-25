"""Plan 328 — `FakeForecastStore` must agree with `PgForecastStore`.

⚠️ Every flow test that exercises a re-run runs against this fake. Where it
diverges from Postgres, those tests prove nothing — the failure class Plan
327's original fake already walked into once.

Each case below is paired with a Postgres assertion of the SAME fact in
`tests/integration/store/test_forecast_supersession.py::TestPostgresParity`.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import (
    ForecastStatus,
    NwpCycleSource,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.ids import ArtifactId, ForecastId, ModelId, StationId
from tests.conftest import make_forecast_ensemble
from tests.fakes.fake_stores import FakeForecastStore

_ISSUED = ensure_utc(datetime(2026, 4, 1, tzinfo=UTC))
_STATION = StationId(uuid4())
_MODEL = ModelId("linreg_v1")
_ARTIFACT = ArtifactId(uuid4())


def _forecast(
    *,
    seed: int = 1,
    forecast_id: ForecastId | None = None,
    status: ForecastStatus = ForecastStatus.RAW,
) -> OperationalForecast:
    ensemble = replace(
        make_forecast_ensemble(station_id=_STATION, rng=random.Random(seed)),
        issued_at=_ISSUED,
    )
    return OperationalForecast(
        id=forecast_id or ForecastId(uuid4()),
        station_id=_STATION,
        model_id=_MODEL,
        model_artifact_id=_ARTIFACT,
        issued_at=_ISSUED,
        nwp_cycle_reference_time=_ISSUED,
        nwp_cycle_source=NwpCycleSource.PRIMARY,
        representation=ensemble.representation,
        status=status,
        version=1,
        warm_up_source=None,
        warm_up_state_age_hours=None,
        observation_staleness_hours=None,
        ensemble=ensemble,
        created_at=_ISSUED,
        updated_at=_ISSUED,
    )


class TestTheKeyMapMirrorsThePartialIndex:
    def test_a_seeded_superseded_row_does_not_occupy_the_natural_key(self) -> None:
        """🔴 The divergence: reading `_by_key` directly would return the
        SUPERSEDED id here, where Postgres excludes it from the lookup and
        inserts a new forecast."""
        store = FakeForecastStore()
        superseded = _forecast(seed=1, status=ForecastStatus.SUPERSEDED)
        store.seed_pre_capture_forecast(superseded)

        # Same numbers, same key — but nothing CURRENT holds the key.
        arriving = _forecast(seed=1)
        returned = store.store_forecast(arriving)

        assert returned == arriving.id
        assert returned != superseded.id
        assert store.fetch_latest_forecast(_STATION) is not None
        kept = store.fetch_forecast(superseded.id)
        assert kept is not None
        assert kept.status is ForecastStatus.SUPERSEDED

    def test_a_row_1_rerun_after_a_supersession_replaces_the_replacement(self) -> None:
        store = FakeForecastStore()
        first = _forecast(seed=1)
        store.store_forecast(first)
        second = _forecast(seed=2)
        store.store_forecast(second)
        third = _forecast(seed=3)
        store.store_forecast(third)

        current = store.fetch_latest_forecast(_STATION)
        assert current is not None
        assert current.id == third.id
        for older in (first, second):
            stale = store.fetch_forecast(older.id)
            assert stale is not None
            assert stale.status is ForecastStatus.SUPERSEDED


class TestTheIdIsAPrimaryKey:
    def test_a_row_1_rerun_reusing_the_original_id_is_rejected(self) -> None:
        """⛔ Postgres rejects the PK collision and rolls the mark back with
        it. Overwriting instead would destroy the original AND its evidence —
        the append-only guarantee migration 0057 enforces."""
        store = FakeForecastStore()
        original = _forecast(seed=1)
        store.store_forecast(original)
        evidence_before = store.fetch_evidence(original.id)

        colliding = _forecast(seed=2, forecast_id=original.id)
        with pytest.raises(IntegrityError, match="forecasts_pkey"):
            store.store_forecast(colliding)

        survivor = store.fetch_forecast(original.id)
        assert survivor is not None
        assert survivor.ensemble.values.equals(original.ensemble.values)
        assert store.fetch_evidence(original.id) == evidence_before

    def test_resubmitting_the_very_same_forecast_still_resumes(self) -> None:
        """A row-4 resume never reaches the INSERT, so the PK guard must not
        fire on it — that would turn every identical re-run into a failure."""
        store = FakeForecastStore()
        original = _forecast(seed=1)
        store.store_forecast(original)

        assert store.store_forecast(original) == original.id
