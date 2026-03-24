from __future__ import annotations

import polars as pl
import pytest

from sapphire_flow.types.enums import ForcingProvenance
from sapphire_flow.types.model import (
    forcing_provenance_columns,
    parameter_columns,
    validate_forcing_provenance,
)


class TestParameterColumns:
    def test_excludes_timestamp_and_provenance(self) -> None:
        df = pl.DataFrame(
            {
                "timestamp": [1],
                "precipitation": [10.0],
                "precipitation_provenance": ["observed"],
                "temperature": [5.0],
                "temperature_provenance": ["nwp_direct"],
            }
        )
        assert parameter_columns(df) == ["precipitation", "temperature"]

    def test_empty_dataframe(self) -> None:
        df = pl.DataFrame({"timestamp": [1]})
        assert parameter_columns(df) == []


class TestForcingProvenanceColumns:
    def test_returns_only_provenance_columns(self) -> None:
        df = pl.DataFrame(
            {
                "timestamp": [1],
                "precipitation": [10.0],
                "precipitation_provenance": ["observed"],
                "temperature": [5.0],
                "temperature_provenance": ["nwp_direct"],
            }
        )
        result = forcing_provenance_columns(df)
        assert result == ["precipitation_provenance", "temperature_provenance"]

    def test_no_provenance_columns(self) -> None:
        df = pl.DataFrame({"timestamp": [1], "precipitation": [10.0]})
        assert forcing_provenance_columns(df) == []


class TestValidateForcingProvenance:
    def test_passes_complete_provenance(self) -> None:
        df = pl.DataFrame(
            {
                "timestamp": [1],
                "precipitation": [10.0],
                "precipitation_provenance": ["observed"],
                "temperature": [5.0],
                "temperature_provenance": ["nwp_direct"],
            }
        )
        validate_forcing_provenance(df)  # should not raise

    def test_fails_missing_provenance_column(self) -> None:
        df = pl.DataFrame(
            {
                "timestamp": [1],
                "precipitation": [10.0],
                "temperature": [5.0],
                "temperature_provenance": ["nwp_direct"],
            }
        )
        with pytest.raises(ValueError, match="Missing provenance columns"):
            validate_forcing_provenance(df)

    def test_fails_orphaned_provenance_column(self) -> None:
        df = pl.DataFrame(
            {
                "timestamp": [1],
                "precipitation": [10.0],
                "precipitation_provenance": ["observed"],
                "extra_provenance": ["unknown"],
            }
        )
        with pytest.raises(ValueError, match="Orphaned provenance columns"):
            validate_forcing_provenance(df)

    def test_passes_empty_parameters(self) -> None:
        df = pl.DataFrame({"timestamp": [1]})
        validate_forcing_provenance(df)  # no params, no provenance needed


class TestForcingProvenanceEnum:
    def test_all_values_are_lowercase_strings(self) -> None:
        for member in ForcingProvenance:
            assert member.value == member.value.lower()
            assert isinstance(member.value, str)

    def test_expected_member_count(self) -> None:
        assert len(ForcingProvenance) == 8

    def test_polars_enum_dtype_accepts_valid_values(self) -> None:
        prov_dtype = pl.Enum([e.value for e in ForcingProvenance])
        s = pl.Series("prov", ["observed", "nwp_direct"], dtype=prov_dtype)
        assert len(s) == 2

    def test_polars_enum_dtype_rejects_invalid_value(self) -> None:
        prov_dtype = pl.Enum([e.value for e in ForcingProvenance])
        with pytest.raises(pl.exceptions.InvalidOperationError):
            pl.Series("prov", ["invalid_value"], dtype=prov_dtype)
