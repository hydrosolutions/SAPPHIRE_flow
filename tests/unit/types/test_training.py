from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from sapphire_flow.types.ids import ModelId, StationGroupId, StationId
from sapphire_flow.types.training import TrainingUnit
from tests.conftest import _EPOCH, make_training_unit

_STATION = StationId(uuid4())
_GROUP = StationGroupId(uuid4())
_MODEL = ModelId("test_model")


class TestTrainingUnit:
    def test_station_scoped_unit(self) -> None:
        unit = TrainingUnit(
            model_id=_MODEL,
            station_id=_STATION,
            group_id=None,
            station_ids=frozenset({_STATION}),
            training_period_start=_EPOCH,
            training_period_end=_EPOCH,
            time_step=timedelta(days=1),
        )
        assert unit.station_id == _STATION
        assert unit.group_id is None

    def test_group_scoped_unit(self) -> None:
        unit = TrainingUnit(
            model_id=_MODEL,
            station_id=None,
            group_id=_GROUP,
            station_ids=frozenset({_STATION}),
            training_period_start=_EPOCH,
            training_period_end=_EPOCH,
            time_step=timedelta(days=1),
        )
        assert unit.group_id == _GROUP
        assert unit.station_id is None

    def test_both_set_raises(self) -> None:
        with pytest.raises(ValueError, match="Exactly one of station_id or group_id"):
            TrainingUnit(
                model_id=_MODEL,
                station_id=_STATION,
                group_id=_GROUP,
                station_ids=frozenset({_STATION}),
                training_period_start=_EPOCH,
                training_period_end=_EPOCH,
                time_step=timedelta(days=1),
            )

    def test_neither_set_raises(self) -> None:
        with pytest.raises(ValueError, match="Exactly one of station_id or group_id"):
            TrainingUnit(
                model_id=_MODEL,
                station_id=None,
                group_id=None,
                station_ids=frozenset({_STATION}),
                training_period_start=_EPOCH,
                training_period_end=_EPOCH,
                time_step=timedelta(days=1),
            )


class TestMakeTrainingUnit:
    def test_default_is_station_scoped(self) -> None:
        unit = make_training_unit()
        assert unit.station_id is not None
        assert unit.group_id is None

    def test_group_scoped_via_factory(self) -> None:
        unit = make_training_unit(group_id=_GROUP)
        assert unit.group_id == _GROUP
        assert unit.station_id is None


class TestTrainingUnitStationIdsValidation:
    def test_station_scoped_mismatched_station_ids_raises(self) -> None:
        other = StationId(uuid4())
        with pytest.raises(ValueError, match="station-scoped"):
            TrainingUnit(
                model_id=_MODEL,
                station_id=_STATION,
                group_id=None,
                station_ids=frozenset({other}),
                training_period_start=_EPOCH,
                training_period_end=_EPOCH,
                time_step=timedelta(days=1),
            )

    def test_group_scoped_empty_station_ids_raises(self) -> None:
        with pytest.raises(
            ValueError, match="group-scoped unit must have at least one"
        ):
            TrainingUnit(
                model_id=_MODEL,
                station_id=None,
                group_id=_GROUP,
                station_ids=frozenset(),
                training_period_start=_EPOCH,
                training_period_end=_EPOCH,
                time_step=timedelta(days=1),
            )
