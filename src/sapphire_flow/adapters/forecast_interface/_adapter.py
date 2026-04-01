from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, get_args

import polars as pl
import structlog
from forecast_interface.output import (
    DeterministicData,
    ForecastFlag,
    ModelOutput,
    QuantileData,
    TrajectoryData,
    Unit,
    VariableOutput,
    VariableStatus,
)

from sapphire_flow.exceptions import ModelOutputError
from sapphire_flow.types.datetime import UtcDatetime, ensure_utc
from sapphire_flow.types.domain import ForecastParameter, QcFlag
from sapphire_flow.types.ensemble import ForecastEnsemble
from sapphire_flow.types.enums import QcStatus

if TYPE_CHECKING:
    from datetime import timedelta

    from sapphire_flow.types.ids import ModelId, StationId

log = structlog.get_logger()

_VALID_PARAMETERS: frozenset[str] = frozenset(get_args(ForecastParameter))


class ForecastInterfaceAdapter:
    _UNIT_MAP: ClassVar[dict[Unit, str]] = {
        Unit.M3_PER_S: "m³/s",
        Unit.MM_PER_DAY: "mm/day",
        Unit.MM_PER_S: "mm/s",
        Unit.MM: "mm",
        Unit.CM: "cm",
        Unit.M: "m",
        Unit.DEG_C: "°C",
        Unit.UNITLESS: "-",
    }

    _FLAG_RULE_MAP: ClassVar[dict[ForecastFlag, str]] = {
        ForecastFlag.HIGH_EPISTEMIC_UNCERTAINTY: "fi_high_epistemic_uncertainty",
        ForecastFlag.DATA_AVAILABILITY: "fi_data_availability",
    }

    def convert_output(
        self,
        model_output: ModelOutput,
        *,
        station_id: StationId,
        model_id: ModelId | None = None,
        target_parameters: frozenset[str],
    ) -> tuple[dict[str, ForecastEnsemble], list[QcFlag]]:
        if not model_output.variables:
            raise ModelOutputError("Model produced empty variables dict")

        for key in model_output.variables:
            if key not in _VALID_PARAMETERS or key not in target_parameters:
                raise ModelOutputError(f"Unknown or untargeted parameter: '{key}'")

        issued_at: UtcDatetime = ensure_utc(model_output.issue_datetime)
        forecasts: dict[str, ForecastEnsemble] = {}
        qc_flags: list[QcFlag] = []

        for param_name, var_output in model_output.variables.items():
            if var_output.status == VariableStatus.FAILURE:
                log.warning(
                    "fi_variable_failure",
                    param=param_name,
                    station_id=station_id,
                )
                qc_flags.append(
                    QcFlag(
                        rule_id="fi_variable_failure",
                        rule_version="1",
                        status=QcStatus.QC_FAILED,
                        detail=f"FI variable '{param_name}' status: FAILURE",
                    )
                )
                continue

            if var_output.status == VariableStatus.PARTIAL:
                log.warning(
                    "fi_variable_partial",
                    param=param_name,
                    station_id=station_id,
                )
                qc_flags.append(
                    QcFlag(
                        rule_id="fi_partial_output",
                        rule_version="1",
                        status=QcStatus.QC_SUSPECT,
                        detail=f"FI variable '{param_name}' status: PARTIAL",
                    )
                )

            for flag in var_output.flags:
                if flag in self._FLAG_RULE_MAP:
                    qc_flags.append(
                        QcFlag(
                            rule_id=self._FLAG_RULE_MAP[flag],
                            rule_version="1",
                            status=QcStatus.QC_SUSPECT,
                        )
                    )

            units = self._UNIT_MAP[var_output.metadata.unit]
            time_step = var_output.metadata.timedelta

            try:
                ensemble = self._pick_and_convert(
                    var_output,
                    station_id=station_id,
                    issued_at=issued_at,
                    param_name=param_name,
                    units=units,
                    time_step=time_step,
                    model_id=model_id,
                )
                forecasts[param_name] = ensemble
            except ModelOutputError:
                log.warning(
                    "fi_variable_conversion_failed",
                    param=param_name,
                    station_id=station_id,
                )
                qc_flags.append(
                    QcFlag(
                        rule_id="fi_variable_failure",
                        rule_version="1",
                        status=QcStatus.QC_FAILED,
                        detail=(
                            f"FI variable '{param_name}' failed all conversion attempts"
                        ),
                    )
                )

        if not forecasts:
            raise ModelOutputError("All variables failed conversion")

        return forecasts, qc_flags

    def _pick_and_convert(
        self,
        var_output: VariableOutput,
        *,
        station_id: StationId,
        issued_at: UtcDatetime,
        param_name: str,
        units: str,
        time_step: timedelta,
        model_id: ModelId | None,
    ) -> ForecastEnsemble:
        if var_output.trajectories is not None:
            try:
                return self._convert_trajectories(
                    var_output.trajectories,
                    station_id=station_id,
                    issued_at=issued_at,
                    param_name=param_name,
                    units=units,
                    time_step=time_step,
                    model_id=model_id,
                )
            except ValueError as exc:
                log.warning("fi_trajectories_conversion_failed", reason=str(exc))

        if var_output.quantiles is not None:
            try:
                return self._convert_quantiles(
                    var_output.quantiles,
                    station_id=station_id,
                    issued_at=issued_at,
                    param_name=param_name,
                    units=units,
                    time_step=time_step,
                    model_id=model_id,
                )
            except ValueError as exc:
                log.warning("fi_quantiles_conversion_failed", reason=str(exc))

        if var_output.deterministic is not None:
            try:
                return self._convert_deterministic(
                    var_output.deterministic,
                    station_id=station_id,
                    issued_at=issued_at,
                    param_name=param_name,
                    units=units,
                    time_step=time_step,
                    model_id=model_id,
                )
            except ValueError as exc:
                log.warning("fi_deterministic_conversion_failed", reason=str(exc))

        raise ModelOutputError(
            f"No convertible representation for variable '{param_name}'"
        )

    def _convert_trajectories(
        self,
        data: TrajectoryData,
        *,
        station_id: StationId,
        issued_at: UtcDatetime,
        param_name: str,
        units: str,
        time_step: timedelta,
        model_id: ModelId | None,
    ) -> ForecastEnsemble:
        member_cols = [str(i) for i in range(1, data.num_samples + 1)]
        df = (
            data.data.drop("issue_datetime")
            .rename({"datetime": "valid_time"})
            .unpivot(
                on=member_cols,
                index="valid_time",
                variable_name="member_id",
                value_name="value",
            )
            .with_columns(
                pl.col("member_id").cast(pl.Int32),
                pl.col("value").cast(pl.Float64),
            )
        )
        return ForecastEnsemble.from_members(
            station_id=station_id,
            issued_at=issued_at,
            parameter=param_name,
            units=units,
            time_step=time_step,
            values=df,
            model_id=model_id,
        )

    def _convert_quantiles(
        self,
        data: QuantileData,
        *,
        station_id: StationId,
        issued_at: UtcDatetime,
        param_name: str,
        units: str,
        time_step: timedelta,
        model_id: ModelId | None,
    ) -> ForecastEnsemble:
        quantile_cols = [str(q) for q in data.quantile_levels]
        df = (
            data.data.drop("issue_datetime")
            .rename({"datetime": "valid_time"})
            .unpivot(
                on=quantile_cols,
                index="valid_time",
                variable_name="quantile",
                value_name="value",
            )
            .with_columns(
                pl.col("quantile").cast(pl.Float64),
                pl.col("value").cast(pl.Float64),
            )
        )
        return ForecastEnsemble.from_quantiles(
            station_id=station_id,
            issued_at=issued_at,
            parameter=param_name,
            units=units,
            time_step=time_step,
            values=df,
            model_id=model_id,
        )

    def _convert_deterministic(
        self,
        data: DeterministicData,
        *,
        station_id: StationId,
        issued_at: UtcDatetime,
        param_name: str,
        units: str,
        time_step: timedelta,
        model_id: ModelId | None,
    ) -> ForecastEnsemble:
        df = (
            data.data.drop("issue_datetime")
            .rename({"datetime": "valid_time"})
            .with_columns(pl.lit(1).cast(pl.Int32).alias("member_id"))
            .with_columns(pl.col("value").cast(pl.Float64))
            .select(["valid_time", "member_id", "value"])
        )
        return ForecastEnsemble.from_members(
            station_id=station_id,
            issued_at=issued_at,
            parameter=param_name,
            units=units,
            time_step=time_step,
            values=df,
            model_id=model_id,
        )
