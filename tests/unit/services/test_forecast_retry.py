"""Plan 327 § D1 — the retry decision table, evaluated in order."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import polars as pl

from sapphire_flow.services.forecast_retry import (
    REFUSING_ROWS,
    SUPERSEDING_ROWS,
    ForecastRetryRow,
    classify_forecast_retry,
    describe_difference,
)
from sapphire_flow.types.datetime import ensure_utc
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import (
    EnsembleRepresentation,
    ForecastStatus,
    NwpCycleSource,
    QcStatus,
)
from sapphire_flow.types.forecast import OperationalForecast
from sapphire_flow.types.forecast_evidence import incomplete_evidence
from sapphire_flow.types.ids import ArtifactId, ForecastId, ModelId, StationId
from tests.conftest import make_forecast_ensemble

_NOW = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
_ISSUED = ensure_utc(datetime(2026, 1, 1, tzinfo=UTC))
_STATION = StationId(uuid4())
_ARTIFACT = ArtifactId(uuid4())


def _forecast(**overrides: object) -> OperationalForecast:
    defaults: dict[str, object] = {
        "id": ForecastId(uuid4()),
        "station_id": _STATION,
        "model_id": ModelId("linreg_v1"),
        "model_artifact_id": _ARTIFACT,
        "issued_at": _ISSUED,
        "nwp_cycle_reference_time": _ISSUED,
        "nwp_cycle_source": NwpCycleSource.PRIMARY,
        "representation": EnsembleRepresentation.MEMBERS,
        "status": ForecastStatus.RAW,
        "version": 1,
        "warm_up_source": None,
        "warm_up_state_age_hours": None,
        "observation_staleness_hours": None,
        "ensemble": make_forecast_ensemble(
            station_id=_STATION, n_members=3, n_steps=4, rng=random.Random(7)
        ),
        "created_at": _NOW,
        "updated_at": _NOW,
        "qc_status": QcStatus.QC_PASSED,
    }
    defaults.update(overrides)
    return OperationalForecast(**defaults)  # type: ignore[arg-type]


class TestClassifyForecastRetry:
    def test_a_recomputation_of_the_same_forecast_is_row_4_identical(self) -> None:
        stored = _forecast()
        recomputed = _forecast()

        assert (
            classify_forecast_retry(stored=stored, recomputed=recomputed)
            is ForecastRetryRow.IDENTICAL
        )

    def test_row_4_ignores_the_row_id_and_timestamps(self) -> None:
        stored = _forecast()
        recomputed = replace(
            stored,
            id=ForecastId(uuid4()),
            created_at=ensure_utc(datetime(2026, 6, 1, tzinfo=UTC)),
            updated_at=ensure_utc(datetime(2026, 6, 1, tzinfo=UTC)),
        )

        assert (
            classify_forecast_retry(stored=stored, recomputed=recomputed)
            is ForecastRetryRow.IDENTICAL
        )

    def test_row_1_when_a_single_value_differs(self) -> None:
        stored = _forecast()
        recomputed = _forecast(
            ensemble=make_forecast_ensemble(
                station_id=_STATION, n_members=3, n_steps=4, rng=random.Random(8)
            )
        )

        assert (
            classify_forecast_retry(stored=stored, recomputed=recomputed)
            is ForecastRetryRow.VALUES_DIFFER
        )

    def test_row_1_wins_over_a_differing_artifact(self) -> None:
        """First match wins: rows are not independent predicates."""
        stored = _forecast()
        recomputed = _forecast(
            model_artifact_id=ArtifactId(uuid4()),
            ensemble=make_forecast_ensemble(
                station_id=_STATION, n_members=3, n_steps=4, rng=random.Random(8)
            ),
        )

        assert (
            classify_forecast_retry(stored=stored, recomputed=recomputed)
            is ForecastRetryRow.VALUES_DIFFER
        )

    def test_row_2_when_only_the_model_artifact_differs(self) -> None:
        stored = _forecast()
        recomputed = replace(stored, model_artifact_id=ArtifactId(uuid4()))

        assert (
            classify_forecast_retry(stored=stored, recomputed=recomputed)
            is ForecastRetryRow.ARTIFACT_DIFFERS
        )

    def test_row_3_when_only_the_qc_verdict_differs(self) -> None:
        stored = _forecast()
        recomputed = replace(stored, qc_status=QcStatus.QC_SUSPECT)

        assert (
            classify_forecast_retry(stored=stored, recomputed=recomputed)
            is ForecastRetryRow.QC_VERDICT_DIFFERS
        )

    def test_row_3_when_only_a_qc_rule_version_differs(self) -> None:
        flag = QcFlag(
            rule_id="range",
            rule_version="1",
            status=QcStatus.QC_PASSED,
            detail=None,
        )
        stored = _forecast(qc_flags=(flag,))
        recomputed = replace(stored, qc_flags=(replace(flag, rule_version="2"),))

        assert (
            classify_forecast_retry(stored=stored, recomputed=recomputed)
            is ForecastRetryRow.QC_VERDICT_DIFFERS
        )

    def test_free_text_qc_detail_alone_is_not_a_difference(self) -> None:
        flag = QcFlag(
            rule_id="range",
            rule_version="1",
            status=QcStatus.QC_PASSED,
            detail="checked 12 steps",
        )
        stored = _forecast(qc_flags=(flag,))
        recomputed = replace(stored, qc_flags=(replace(flag, detail="checked again"),))

        assert (
            classify_forecast_retry(stored=stored, recomputed=recomputed)
            is ForecastRetryRow.IDENTICAL
        )

    def test_row_1_when_the_units_differ(self) -> None:
        stored = _forecast()
        values = stored.ensemble.values
        recomputed = _forecast(
            ensemble=ForecastEnsemble.from_members(
                station_id=_STATION,
                issued_at=stored.ensemble.issued_at,
                parameter=stored.ensemble.parameter,
                units="l/s",
                time_step=stored.ensemble.time_step,
                values=values,
            )
        )

        assert (
            classify_forecast_retry(stored=stored, recomputed=recomputed)
            is ForecastRetryRow.VALUES_DIFFER
        )

    def test_nan_values_do_not_make_a_forecast_differ_from_itself(self) -> None:
        """A NaN never equals itself; without special handling an identical
        re-run of a forecast carrying one would be refused forever."""
        with_nan = ForecastEnsemble.from_members(
            station_id=_STATION,
            issued_at=_ISSUED,
            parameter="discharge",
            units="m³/s",
            time_step=make_forecast_ensemble(n_steps=2, n_members=1).time_step,
            values=pl.DataFrame(
                {
                    "valid_time": [
                        ensure_utc(datetime(2026, 1, 1, 1, tzinfo=UTC)),
                        ensure_utc(datetime(2026, 1, 1, 2, tzinfo=UTC)),
                    ],
                    "member_id": [0, 0],
                    "value": [float("nan"), 3.0],
                }
            ).with_columns(
                pl.col("valid_time").cast(pl.Datetime("us", "UTC")),
                pl.col("member_id").cast(pl.Int32),
            ),
        )
        stored = _forecast(ensemble=with_nan)
        recomputed = _forecast(ensemble=with_nan)

        assert (
            classify_forecast_retry(stored=stored, recomputed=recomputed)
            is ForecastRetryRow.IDENTICAL
        )

    def test_evidence_state_is_not_a_classifier(self) -> None:
        """⛔ Plan 327: every operational forecast carries an incomplete-evidence
        marker, so classifying on evidence would refuse EVERY resume."""
        stored = _forecast(evidence=incomplete_evidence("runtime_image_bytes_unpinned"))
        recomputed = replace(
            stored,
            id=ForecastId(uuid4()),
            evidence=incomplete_evidence("capture_failed:RuntimeError"),
        )

        assert (
            classify_forecast_retry(stored=stored, recomputed=recomputed)
            is ForecastRetryRow.IDENTICAL
        )

    def test_a_missing_evidence_row_is_not_a_classifier_either(self) -> None:
        stored = _forecast(evidence=None)
        recomputed = replace(
            stored,
            id=ForecastId(uuid4()),
            evidence=incomplete_evidence("runtime_image_bytes_unpinned"),
        )

        assert (
            classify_forecast_retry(stored=stored, recomputed=recomputed)
            is ForecastRetryRow.IDENTICAL
        )


class TestDescribeDifference:
    def test_names_the_row_and_the_artifacts(self) -> None:
        stored = _forecast()
        recomputed = replace(stored, model_artifact_id=ArtifactId(uuid4()))
        row = classify_forecast_retry(stored=stored, recomputed=recomputed)

        detail = describe_difference(row, stored=stored, recomputed=recomputed)

        assert "row 2" in detail
        assert str(stored.model_artifact_id) in detail
        assert str(recomputed.model_artifact_id) in detail

    def test_names_the_qc_verdicts(self) -> None:
        stored = _forecast()
        recomputed = replace(stored, qc_status=QcStatus.QC_SUSPECT)
        row = classify_forecast_retry(stored=stored, recomputed=recomputed)

        detail = describe_difference(row, stored=stored, recomputed=recomputed)

        assert "row 3" in detail
        assert QcStatus.QC_SUSPECT.value in detail


class TestRowPartition:
    """Plan 328 — the decision table is split in two, and the split must stay
    TOTAL and DISJOINT.

    ⛔ Without this, a row added to Plan 327's table later would land silently
    on the refusing side of ``_resolve_retry``'s fall-through and nobody would
    be told which side it belongs on.
    """

    def test_superseding_and_refusing_rows_are_disjoint(self) -> None:
        assert not set(SUPERSEDING_ROWS) & set(REFUSING_ROWS)

    def test_every_non_identical_row_is_classified_exactly_once(self) -> None:
        acted_on = set(SUPERSEDING_ROWS) | set(REFUSING_ROWS)
        assert acted_on == set(ForecastRetryRow) - {ForecastRetryRow.IDENTICAL}

    def test_row_3_is_the_only_refusal(self) -> None:
        """⛔ Row 3 is the one Plan 328 does NOT take."""
        assert set(REFUSING_ROWS) == {ForecastRetryRow.QC_VERDICT_DIFFERS}
