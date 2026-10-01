from dataclasses import replace
from uuid import uuid4

import pytest

from sapphire_flow.services.forecast_retry import (
    ForecastRetryRow,
    classify_forecast_retry,
)
from sapphire_flow.types.enums import ForecastDataUse, QcStatus
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.forecast_lineage import ForecastInputLineage
from tests.unit.services.test_forecast_retry import _forecast


def _test_forecast(version: str = "a") -> OperationalForecast:
    return _forecast(
        data_use=ForecastDataUse.EXPIRED_RATING_TEST,
        input_lineage=ForecastInputLineage(
            provisional_discharge_fingerprints=(version * 64,),
            transformation_versions=("daily-mean-v1",),
        ),
    )


class TestForecastDataUseRetry:
    def test_standard_default_is_not_a_validity_certificate(self) -> None:
        assert _forecast().data_use is ForecastDataUse.STANDARD

    def test_equal_values_changed_lineage_supersedes(self) -> None:
        assert (
            classify_forecast_retry(
                stored=_test_forecast(), recomputed=_test_forecast("b")
            )
            is ForecastRetryRow.INPUT_LINEAGE_DIFFERS
        )

    def test_qc_conflict_wins_over_lineage_change(self) -> None:
        assert (
            classify_forecast_retry(
                stored=_test_forecast(),
                recomputed=replace(_test_forecast("b"), qc_status=QcStatus.QC_SUSPECT),
            )
            is ForecastRetryRow.QC_VERDICT_DIFFERS
        )

    def test_cross_class_comparison_refused(self) -> None:
        with pytest.raises(ValueError, match="data use"):
            classify_forecast_retry(stored=_forecast(), recomputed=_test_forecast())

    def test_test_forecast_requires_lineage(self) -> None:
        with pytest.raises(ValueError, match="lineage"):
            _forecast(data_use=ForecastDataUse.EXPIRED_RATING_TEST)

    def test_identical_lineage_keeps_identity(self) -> None:
        assert (
            classify_forecast_retry(
                stored=_test_forecast(), recomputed=_test_forecast()
            )
            is ForecastRetryRow.IDENTICAL
        )


class TestForecastLineage:
    def test_snapshot_capture_clock_is_not_retry_identity(self) -> None:
        from datetime import timedelta

        from sapphire_flow.types.forecast_lineage import snapshot_consumed_input
        from tests.conftest import make_observation

        source = make_observation()
        first = snapshot_consumed_input(source, units="m3/s")
        again = snapshot_consumed_input(
            replace(source, created_at=source.created_at + timedelta(hours=1)),
            units="m3/s",
        )
        assert first == again
        changed = snapshot_consumed_input(replace(source, value=9.7), units="m3/s")
        lineage = ForecastInputLineage(
            snapshots=(first,), transformation_versions=("identity-v1",)
        )
        assert lineage.fingerprint != replace(lineage, snapshots=(changed,)).fingerprint
        assert ForecastInputLineage.from_content(lineage.content) == lineage

    def test_source_order_is_canonical_but_pipeline_order_matters(self) -> None:
        from sapphire_flow.types.forecast_lineage import snapshot_consumed_input
        from tests.conftest import make_observation

        snapshots = tuple(
            snapshot_consumed_input(make_observation(), units="m3/s") for _ in range(2)
        )
        lineage = ForecastInputLineage(
            snapshots=snapshots, transformation_versions=("a", "b")
        )
        assert (
            lineage.fingerprint
            == replace(lineage, snapshots=tuple(reversed(snapshots))).fingerprint
        )
        assert (
            lineage.fingerprint
            != replace(lineage, transformation_versions=("b", "a")).fingerprint
        )

    def test_unknown_or_mutable_lineage_rejected(self) -> None:
        with pytest.raises(ValueError, match="consumed inputs"):
            ForecastInputLineage(transformation_versions=("v1",))
        with pytest.raises(ValueError, match="immutable tuples"):
            ForecastInputLineage(transformation_versions=["v1"], snapshots=[])  # type: ignore[arg-type]

    def test_fake_store_enforces_purpose(self) -> None:
        from tests.fakes.fake_stores import FakeForecastStore

        standard = FakeForecastStore()
        test = FakeForecastStore(data_use=ForecastDataUse.EXPIRED_RATING_TEST)
        with pytest.raises(ValueError, match="data use"):
            standard.store_forecast(_test_forecast())
        with pytest.raises(ValueError, match="data use"):
            test.store_forecast(_forecast())
        first = _test_forecast()
        assert test.store_forecast(first) == first.id
        changed = replace(
            first,
            id=uuid4(),
            input_lineage=replace(first.input_lineage, transformation_versions=("v2",)),
        )
        assert test.store_forecast(changed) == changed.id

    def test_snapshot_rejects_unrecognized_fields_and_missing_source_identity(
        self,
    ) -> None:
        import json

        from sapphire_flow.types.forecast_lineage import (
            ForecastInputSnapshot,
            snapshot_consumed_input,
        )
        from sapphire_flow.types.rating_reference import canonical_content
        from tests.conftest import make_observation

        snapshot = snapshot_consumed_input(make_observation(), units="m3/s")
        data = json.loads(snapshot.content)
        with pytest.raises(ValueError, match="complete source"):
            ForecastInputSnapshot(
                kind="observation",
                units="m3/s",
                content=canonical_content(data | {"run_id": "arbitrary"}),
            )
        with pytest.raises(ValueError, match="source is missing"):
            replace(snapshot, content=canonical_content(data | {"source": ""}))
        with pytest.raises(ValueError, match="finite or missing"):
            replace(snapshot, content=canonical_content(data | {"value": True}))


class TestConsumedStaticAttributes:
    def test_static_content_or_source_version_changes_test_retry(self) -> None:
        from sapphire_flow.types.forecast_lineage import ForecastStaticAttributes

        first = _test_forecast()
        attributes = ForecastStaticAttributes(
            station_id=first.station_id,
            source="basin-package",
            version="immutable-fixture-v1",
            values=(("area", 20.0), ("slope", 0.2)),
        )
        lineage = replace(first.input_lineage, static_attributes=(attributes,))
        first = replace(first, input_lineage=lineage)
        reordered = replace(
            lineage,
            static_attributes=(
                replace(attributes, values=tuple(reversed(attributes.values))),
            ),
        )
        assert (
            classify_forecast_retry(
                stored=first, recomputed=replace(first, input_lineage=reordered)
            )
            is ForecastRetryRow.IDENTICAL
        )
        for changed in (
            replace(attributes, version="immutable-fixture-v2"),
            replace(attributes, values=(("area", 22.0), ("slope", 0.2))),
        ):
            assert (
                classify_forecast_retry(
                    stored=first,
                    recomputed=replace(
                        first,
                        input_lineage=replace(lineage, static_attributes=(changed,)),
                    ),
                )
                is ForecastRetryRow.INPUT_LINEAGE_DIFFERS
            )
        assert (
            ForecastInputLineage.from_content(lineage.content).fingerprint
            == lineage.fingerprint
        )

    @pytest.mark.parametrize(
        "values",
        [
            (("area", 1.0), ("area", 2.0)),
            (("area", float("nan")),),
            (("area", float("inf")),),
            (("area", "unknown"),),
        ],
    )
    def test_ambiguous_or_unsupported_static_values_refused(
        self, values: tuple
    ) -> None:
        from sapphire_flow.types.forecast_lineage import ForecastStaticAttributes

        with pytest.raises(ValueError, match="unique|finite"):
            ForecastStaticAttributes(
                station_id=_test_forecast().station_id,
                source="fixture",
                version="v1",
                values=values,
            )
