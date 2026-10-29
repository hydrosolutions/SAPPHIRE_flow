from __future__ import annotations

import math

import pytest

from sapphire_flow.adapters.bafu_discharge_units import (
    normalize_discharge,
    parse_discharge_literal,
)
from sapphire_flow.types.observation_units import (
    CanonicalDischargeValue,
    DischargeSourceUnit,
    SourceDischargeValue,
)


class TestParseDischargeLiteral:
    @pytest.mark.parametrize(
        ("literal", "expected"),
        [
            ("22.000", 22.0),
            (".135", 0.135),
            ("+2.2e1", 22.0),
            ("1.", 1.0),
            ("-0.125", -0.125),
        ],
    )
    def test_parses_finite_decimal_or_scientific_literal(
        self, literal: str, expected: float
    ) -> None:
        assert parse_discharge_literal(literal) == SourceDischargeValue(value=expected)

    @pytest.mark.parametrize(
        "literal",
        [
            "NaN",
            "inf",
            "-Infinity",
            "1e9999",
            "1_000",
            " 22",
            "22 ",
            "",
            "true",
            "1,000",
            "١٢",
            "0x10",
        ],
    )
    def test_rejects_nonfinite_or_non_numeric_lexical_forms(self, literal: str) -> None:
        with pytest.raises(ValueError, match="discharge literal"):
            parse_discharge_literal(literal)

    @pytest.mark.parametrize("literal", ["-0", "-0.000", "-0e10"])
    def test_normalizes_signed_zero(self, literal: str) -> None:
        assert math.copysign(1.0, parse_discharge_literal(literal).value) == 1.0

    def test_equivalent_spellings_produce_same_numeric_value(self) -> None:
        assert parse_discharge_literal("22") == parse_discharge_literal("22.000")


class TestNormalizeDischarge:
    @pytest.mark.parametrize("literal", ["22.000", "0.135", "8.486", "0.0000137"])
    def test_litres_use_the_exact_shared_division(self, literal: str) -> None:
        result = normalize_discharge(
            parse_discharge_literal(literal), DischargeSourceUnit.LITRES_PER_SECOND
        )
        assert result.value.hex() == (float(literal) / 1000.0).hex()

    def test_cubic_metres_are_not_converted(self) -> None:
        result = normalize_discharge(
            parse_discharge_literal("17.645"),
            DischargeSourceUnit.CUBIC_METRES_PER_SECOND,
        )
        assert result == CanonicalDischargeValue(value=17.645)

    def test_negative_measurement_is_preserved_for_qc(self) -> None:
        result = normalize_discharge(
            parse_discharge_literal("-2"), DischargeSourceUnit.LITRES_PER_SECOND
        )
        assert result.value == -0.002

    def test_zero_remains_positive_zero(self) -> None:
        result = normalize_discharge(
            parse_discharge_literal("-0"), DischargeSourceUnit.LITRES_PER_SECOND
        )
        assert result.value == 0.0
        assert math.copysign(1.0, result.value) == 1.0


class TestDischargeValueInvariants:
    @pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
    def test_source_value_cannot_be_nonfinite(self, value: float) -> None:
        with pytest.raises(ValueError, match="source discharge must be finite"):
            SourceDischargeValue(value=value)

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
    def test_canonical_value_cannot_be_nonfinite(self, value: float) -> None:
        with pytest.raises(ValueError, match="canonical discharge must be finite"):
            CanonicalDischargeValue(value=value)


class TestDischargePrecisionGuards:
    @pytest.mark.parametrize("literal", ["1e-400", "-1e-400"])
    def test_nonzero_literal_cannot_underflow_to_zero(self, literal: str) -> None:
        with pytest.raises(ValueError, match="underflow"):
            parse_discharge_literal(literal)

    def test_conversion_cannot_underflow_to_zero(self) -> None:
        with pytest.raises(ValueError, match="underflow"):
            normalize_discharge(
                SourceDischargeValue(value=1e-323),
                DischargeSourceUnit.LITRES_PER_SECOND,
            )

    def test_integer_values_have_float_semantics(self) -> None:
        assert type(SourceDischargeValue(value=22).value) is float
        assert type(CanonicalDischargeValue(value=22).value) is float
