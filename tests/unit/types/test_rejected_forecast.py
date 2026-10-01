from __future__ import annotations

import threading
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from sapphire_flow.types.enums import ForecastDataUse, QcStatus
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from sapphire_flow.types.ids import ModelId
from sapphire_flow.types.rejected_forecast import (
    RejectedAssignmentPayload,
    RejectedForecastEntry,
    RejectedParameterPayload,
)
from tests.conftest import make_forecast_ensemble
from tests.fakes.fake_stores import FakeRejectedForecastStore


def test_fake_rejection_store_preserves_class_lineage_and_batch_atomicity() -> None:
    ensemble = make_forecast_ensemble()
    entry = RejectedForecastEntry(
        attempt_id=uuid4(),
        payload=RejectedAssignmentPayload(
            station_id=ensemble.station_id,
            model_id=ModelId("fixture"),
            model_artifact_id=None,
            issued_at=ensemble.issued_at,
            parameters=(
                RejectedParameterPayload(
                    ensemble=ensemble, qc_status=QcStatus.QC_FAILED
                ),
            ),
        ),
    )
    lineage = ForecastInputLineage(
        provisional_discharge_fingerprints=("a" * 64,), transformation_versions=("v1",)
    )
    test = replace(
        entry,
        payload=replace(
            entry.payload,
            data_use=ForecastDataUse.EXPIRED_RATING_TEST,
            input_lineage=lineage,
        ),
    )
    standard_store = FakeRejectedForecastStore()
    test_store = FakeRejectedForecastStore(data_use=ForecastDataUse.EXPIRED_RATING_TEST)
    window = (
        ensemble.station_id,
        ensemble.issued_at - timedelta(days=1),
        ensemble.issued_at + timedelta(days=1),
    )
    with pytest.raises(ValueError, match="purpose"):
        standard_store.write_batch([entry, test], abandon=threading.Event())
    assert standard_store.fetch_rejected_forecasts(*window) == ([], 0)
    test_store.write_batch([test], abandon=threading.Event())
    rows, count = test_store.fetch_rejected_forecasts(*window)
    assert count == 1
    assert rows[0].data_use is ForecastDataUse.EXPIRED_RATING_TEST
    assert rows[0].input_lineage == lineage
    invalid = replace(test, payload=replace(test.payload, input_lineage=None))
    with pytest.raises(ValueError, match="lineage"):
        test_store.write_batch([test, invalid], abandon=threading.Event())
    assert test_store.fetch_rejected_forecasts(*window)[1] == 1
