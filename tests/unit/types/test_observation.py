from __future__ import annotations

import random

import pytest

from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import ObservationId, StationId
from sapphire_flow.types.observation import Observation
from tests.conftest import _EPOCH, _uuid, make_observation


def _make_obs(value: float | None, qc_status: QcStatus) -> Observation:
    rng = random.Random(42)
    return Observation(
        id=ObservationId(_uuid(rng)),
        station_id=StationId(_uuid(rng)),
        timestamp=_EPOCH,
        parameter="discharge",
        value=value,
        source=ObservationSource.MEASURED,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=qc_status,
        qc_flags=[],
        qc_rule_version=None,
        created_at=_EPOCH,
    )


class TestObservationInvariant:
    def test_normal_observation(self) -> None:
        obs = make_observation(value=5.0, qc_status=QcStatus.QC_PASSED)
        assert obs.value == 5.0
        assert obs.qc_status == QcStatus.QC_PASSED

    def test_missing_observation_requires_none_value(self) -> None:
        obs = make_observation(value=None, qc_status=QcStatus.MISSING)
        assert obs.value is None
        assert obs.qc_status == QcStatus.MISSING

    def test_missing_observation_rejects_float_value(self) -> None:
        with pytest.raises(ValueError, match="None when qc_status is MISSING"):
            _make_obs(value=5.0, qc_status=QcStatus.MISSING)

    def test_non_missing_observation_rejects_none_value(self) -> None:
        with pytest.raises(ValueError, match="not be None when qc_status is not"):
            _make_obs(value=None, qc_status=QcStatus.QC_PASSED)
