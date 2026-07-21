from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from sapphire_flow.services.component_derivation import (
    DERIVATION_RULE_ID,
    DERIVATION_RULE_VERSION,
    derive_point,
    propagate_qc_status,
    select_by_precedence,
)
from sapphire_flow.types.calculated_station import ComponentWeight
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.enums import ObservationSource, QcStatus
from sapphire_flow.types.ids import FormulaId, ObservationId, StationId
from sapphire_flow.types.observation import Observation

_NOW = ensure_utc(datetime(2025, 6, 1, 12, tzinfo=UTC))


def _weight(component: StationId, weight: float) -> ComponentWeight:
    return ComponentWeight(
        id=FormulaId(uuid.uuid4()),
        calculated_station_id=StationId(uuid.uuid4()),
        component_station_id=component,
        parameter="discharge",
        weight=weight,
        effective_from=_NOW,
        effective_to=None,
        created_at=_NOW,
    )


def _obs(
    station: StationId,
    value: float | None,
    *,
    source: ObservationSource = ObservationSource.MEASURED,
    status: QcStatus = QcStatus.QC_PASSED,
) -> Observation:
    return Observation(
        id=ObservationId(uuid.uuid4()),
        station_id=station,
        timestamp=_NOW,
        parameter="discharge",
        value=value,
        source=source,
        rating_curve_id=None,
        rating_curve_correction_version=None,
        qc_status=status,
        qc_flags=[],
        qc_rule_version=None,
        created_at=_NOW,
    )


class TestSelectByPrecedence:
    def test_measured_beats_all_lower_sources(self) -> None:
        sid = StationId(uuid.uuid4())
        rows = [
            _obs(sid, 3.0, source=ObservationSource.COMPONENT_DERIVED),
            _obs(sid, 2.0, source=ObservationSource.MANUAL_IMPORT),
            _obs(sid, 4.0, source=ObservationSource.RATING_CURVE_DERIVED),
            _obs(sid, 1.0, source=ObservationSource.MEASURED),
        ]
        winner = select_by_precedence(rows)
        assert winner is not None
        assert winner.source == ObservationSource.MEASURED
        assert winner.value == pytest.approx(1.0)

    def test_rating_curve_beats_manual_and_component(self) -> None:
        sid = StationId(uuid.uuid4())
        rows = [
            _obs(sid, 2.0, source=ObservationSource.MANUAL_IMPORT),
            _obs(sid, 4.0, source=ObservationSource.RATING_CURVE_DERIVED),
            _obs(sid, 3.0, source=ObservationSource.COMPONENT_DERIVED),
        ]
        winner = select_by_precedence(rows)
        assert winner is not None
        assert winner.source == ObservationSource.RATING_CURVE_DERIVED

    def test_empty_returns_none(self) -> None:
        assert select_by_precedence([]) is None


class TestPropagateQcStatus:
    def test_all_passed_is_passed(self) -> None:
        assert (
            propagate_qc_status([QcStatus.QC_PASSED, QcStatus.QC_PASSED])
            == QcStatus.QC_PASSED
        )

    def test_any_suspect_is_suspect(self) -> None:
        assert (
            propagate_qc_status([QcStatus.QC_PASSED, QcStatus.QC_SUSPECT])
            == QcStatus.QC_SUSPECT
        )

    def test_empty_is_passed(self) -> None:
        assert propagate_qc_status([]) == QcStatus.QC_PASSED


class TestDerivePoint:
    def test_weighted_sum_all_passed(self) -> None:
        c1, c2 = StationId(uuid.uuid4()), StationId(uuid.uuid4())
        resolved = [
            (_weight(c1, 0.6), _obs(c1, 10.0)),
            (_weight(c2, 0.4), _obs(c2, 20.0)),
        ]
        point = derive_point(resolved)
        assert point.value == pytest.approx(0.6 * 10.0 + 0.4 * 20.0)
        assert point.qc_status == QcStatus.QC_PASSED
        assert len(point.qc_flags) == 2
        assert all(f.rule_id == DERIVATION_RULE_ID for f in point.qc_flags)
        assert all(f.rule_version == DERIVATION_RULE_VERSION for f in point.qc_flags)

    def test_signed_weights_produce_difference(self) -> None:
        c1, c2 = StationId(uuid.uuid4()), StationId(uuid.uuid4())
        resolved = [
            (_weight(c1, 1.0), _obs(c1, 30.0)),
            (_weight(c2, -1.0), _obs(c2, 12.0)),
        ]
        point = derive_point(resolved)
        assert point.value == pytest.approx(18.0)

    def test_any_suspect_component_makes_derived_suspect(self) -> None:
        c1, c2 = StationId(uuid.uuid4()), StationId(uuid.uuid4())
        resolved = [
            (_weight(c1, 0.5), _obs(c1, 10.0, status=QcStatus.QC_PASSED)),
            (_weight(c2, 0.5), _obs(c2, 20.0, status=QcStatus.QC_SUSPECT)),
        ]
        point = derive_point(resolved)
        assert point.value == pytest.approx(15.0)
        assert point.qc_status == QcStatus.QC_SUSPECT

    def test_missing_component_yields_placeholder(self) -> None:
        c1, c2 = StationId(uuid.uuid4()), StationId(uuid.uuid4())
        resolved = [
            (_weight(c1, 0.5), _obs(c1, 10.0)),
            (_weight(c2, 0.5), None),
        ]
        point = derive_point(resolved)
        assert point.value is None
        assert point.qc_status == QcStatus.MISSING
        assert point.qc_flags == []

    def test_failed_component_yields_placeholder(self) -> None:
        c1, c2 = StationId(uuid.uuid4()), StationId(uuid.uuid4())
        resolved = [
            (_weight(c1, 0.5), _obs(c1, 10.0)),
            (_weight(c2, 0.5), _obs(c2, 20.0, status=QcStatus.QC_FAILED)),
        ]
        assert derive_point(resolved).qc_status == QcStatus.MISSING

    def test_raw_component_yields_placeholder(self) -> None:
        c1 = StationId(uuid.uuid4())
        resolved = [(_weight(c1, 1.0), _obs(c1, 10.0, status=QcStatus.RAW))]
        assert derive_point(resolved).qc_status == QcStatus.MISSING

    def test_provenance_flag_detail_encodes_component_and_weight(self) -> None:
        c1 = StationId(uuid.uuid4())
        weight = _weight(c1, 0.7)
        point = derive_point([(weight, _obs(c1, 10.0, status=QcStatus.QC_SUSPECT))])
        assert len(point.qc_flags) == 1
        detail = json.loads(point.qc_flags[0].detail or "{}")
        assert detail["component_station_id"] == str(c1)
        assert detail["component_status"] == "qc_suspect"
        assert detail["weight"] == pytest.approx(0.7)
