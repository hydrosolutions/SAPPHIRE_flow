from dataclasses import replace
from datetime import timedelta

import polars as pl
import pytest

from sapphire_flow.services.daily_output_integrity import assess_daily_output
from sapphire_flow.types.daily_output import DailyOutputContract, DailyOutputEligibility
from sapphire_flow.types.domain import QcFlag
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import EnsembleRepresentation, QcStatus
from sapphire_flow.types.forecast_reference import ForecastOutputKey
from sapphire_flow.types.ids import ModelId
from tests.conftest import make_forecast_ensemble


@pytest.fixture
def output() -> tuple[ForecastEnsemble, ForecastOutputKey, DailyOutputContract]:
    ensemble = make_forecast_ensemble(
        n_steps=2, n_members=2, model_id=ModelId("cmal_small")
    )
    key = ForecastOutputKey(
        station_id=ensemble.station_id,
        model_id=ModelId("cmal_small"),
        issued_at=ensemble.issued_at,
        parameter=ensemble.parameter,
    )
    contract = DailyOutputContract(
        parameter="discharge",
        units="m³/s",
        representation=EnsembleRepresentation.MEMBERS,
        time_step=timedelta(hours=1),
        first_lead=timedelta(hours=1),
        horizon_steps=2,
        series_keys=(0.0, 1.0),
    )
    return ensemble, key, contract


class TestAssessDailyOutput:
    def test_structurally_valid_passed_output_is_usable(
        self, output: tuple[ForecastEnsemble, ForecastOutputKey, DailyOutputContract]
    ) -> None:
        ensemble, key, contract = output
        assert (
            assess_daily_output(ensemble, key, contract, QcStatus.QC_PASSED, ())
            is DailyOutputEligibility.USABLE
        )

    @pytest.mark.parametrize("rule", ["negative_value", "range_check"])
    def test_plausibility_failure_is_not_ordinary_validity(
        self,
        output: tuple[ForecastEnsemble, ForecastOutputKey, DailyOutputContract],
        rule: str,
    ) -> None:
        ensemble, key, contract = output
        flags = (
            QcFlag(
                rule_id=rule,
                rule_version="1",
                status=QcStatus.QC_FAILED,
                detail="fixture",
            ),
        )
        assert (
            assess_daily_output(ensemble, key, contract, QcStatus.QC_FAILED, flags)
            is DailyOutputEligibility.PLAUSIBILITY_FLAGGED
        )

    @pytest.mark.parametrize("rule", ["quantile_crossing", "unknown_future_rule"])
    def test_hard_or_unknown_failed_rule_is_excluded(
        self,
        output: tuple[ForecastEnsemble, ForecastOutputKey, DailyOutputContract],
        rule: str,
    ) -> None:
        ensemble, key, contract = output
        flags = (
            QcFlag(
                rule_id=rule,
                rule_version="1",
                status=QcStatus.QC_FAILED,
                detail="fixture",
            ),
        )
        assert (
            assess_daily_output(ensemble, key, contract, QcStatus.QC_FAILED, flags)
            is DailyOutputEligibility.INTEGRITY_REJECTED
        )

    @pytest.mark.parametrize(
        "damage",
        [
            "missing",
            "duplicate",
            "nan",
            "inf",
            "units",
            "cadence",
            "model",
            "offset",
            "horizon",
        ],
    )
    def test_structural_faults_never_become_plausibility_outputs(
        self,
        output: tuple[ForecastEnsemble, ForecastOutputKey, DailyOutputContract],
        damage: str,
    ) -> None:
        ensemble, key, contract = output
        if damage == "missing":
            ensemble = replace(ensemble, values=ensemble.values.head(3))
        elif damage == "duplicate":
            ensemble = replace(
                ensemble,
                values=pl.concat([ensemble.values.head(3), ensemble.values.head(1)]),
            )
        elif damage in ("nan", "inf"):
            ensemble = replace(
                ensemble,
                values=ensemble.values.with_columns(
                    pl.lit(float(damage)).alias("value")
                ),
            )
        elif damage == "units":
            ensemble = replace(ensemble, units="m")
        elif damage == "cadence":
            ensemble = replace(ensemble, time_step=timedelta(days=1))
        elif damage == "model":
            ensemble = replace(ensemble, model_id=ModelId("other"))
        elif damage == "offset":
            ensemble = replace(
                ensemble,
                values=ensemble.values.with_columns(
                    pl.col("valid_time") + timedelta(hours=1)
                ),
            )
        else:
            ensemble = replace(ensemble, forecast_horizon_steps=3)
        assert (
            assess_daily_output(ensemble, key, contract, QcStatus.QC_PASSED, ())
            is DailyOutputEligibility.INTEGRITY_REJECTED
        )

    def test_missing_qc_evidence_fails_closed(
        self, output: tuple[ForecastEnsemble, ForecastOutputKey, DailyOutputContract]
    ) -> None:
        ensemble, key, contract = output
        assert (
            assess_daily_output(ensemble, key, contract, QcStatus.QC_FAILED, ())
            is DailyOutputEligibility.QC_UNASSESSED
        )

    def test_crossing_quantiles_fail_without_a_qc_flag(
        self, output: tuple[ForecastEnsemble, ForecastOutputKey, DailyOutputContract]
    ) -> None:
        ensemble, key, contract = output
        quantiles = make_forecast_ensemble(
            station_id=ensemble.station_id,
            representation=EnsembleRepresentation.QUANTILES,
            n_steps=2,
            model_id=ModelId("cmal_small"),
        )
        quantiles = replace(
            quantiles,
            values=quantiles.values.with_columns(
                (pl.col("quantile") * 100).alias("value")
            ),
        )
        contract = replace(
            contract,
            representation=EnsembleRepresentation.QUANTILES,
            series_keys=tuple(sorted(quantiles.values["quantile"].unique().to_list())),
        )
        assert (
            assess_daily_output(quantiles, key, contract, QcStatus.QC_PASSED, ())
            is DailyOutputEligibility.USABLE
        )
        crossed = replace(
            quantiles,
            values=quantiles.values.with_columns(
                pl.when(pl.col("quantile") == 0.02)
                .then(100.0)
                .otherwise(pl.col("value"))
                .alias("value")
            ),
        )
        assert (
            assess_daily_output(crossed, key, contract, QcStatus.QC_PASSED, ())
            is DailyOutputEligibility.INTEGRITY_REJECTED
        )


class TestDailyOutputContract:
    @pytest.mark.parametrize(
        "changes",
        [
            {"series_keys": ()},
            {"series_keys": (0.0, 0.0)},
            {"series_keys": (float("nan"),)},
            {"horizon_steps": 0},
            {"time_step": timedelta(0)},
            {"first_lead": timedelta(hours=-1)},
        ],
    )
    def test_invalid_contract_cannot_certify_outputs(
        self,
        output: tuple[ForecastEnsemble, ForecastOutputKey, DailyOutputContract],
        changes: dict[str, object],
    ) -> None:
        with pytest.raises(ValueError, match="daily"):
            replace(output[2], **changes)


class TestDeclaredHorizonBounds:
    def test_at_most_horizon_accepts_complete_shorter_output(
        self,
        output: tuple[ForecastEnsemble, ForecastOutputKey, DailyOutputContract],
    ) -> None:
        ensemble, key, contract = output
        bounded = replace(contract, horizon_steps=10, minimum_horizon_steps=1)
        assert (
            assess_daily_output(ensemble, key, bounded, QcStatus.QC_PASSED, ())
            is DailyOutputEligibility.USABLE
        )

    def test_at_most_horizon_still_rejects_missing_cells(
        self,
        output: tuple[ForecastEnsemble, ForecastOutputKey, DailyOutputContract],
    ) -> None:
        ensemble, key, contract = output
        bounded = replace(contract, horizon_steps=10, minimum_horizon_steps=1)
        damaged = replace(ensemble, values=ensemble.values.head(3))
        assert (
            assess_daily_output(damaged, key, bounded, QcStatus.QC_PASSED, ())
            is DailyOutputEligibility.INTEGRITY_REJECTED
        )

    def test_horizon_below_declared_minimum_is_rejected(
        self,
        output: tuple[ForecastEnsemble, ForecastOutputKey, DailyOutputContract],
    ) -> None:
        ensemble, key, contract = output
        bounded = replace(contract, horizon_steps=10, minimum_horizon_steps=3)
        assert (
            assess_daily_output(ensemble, key, bounded, QcStatus.QC_PASSED, ())
            is DailyOutputEligibility.INTEGRITY_REJECTED
        )

    @pytest.mark.parametrize("minimum", [0, 3])
    def test_incoherent_horizon_bounds_are_invalid(
        self,
        output: tuple[ForecastEnsemble, ForecastOutputKey, DailyOutputContract],
        minimum: int,
    ) -> None:
        _, _, contract = output
        with pytest.raises(ValueError, match="minimum horizon"):
            replace(contract, minimum_horizon_steps=minimum)
