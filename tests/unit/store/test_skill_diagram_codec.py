from __future__ import annotations

import json
import math
from collections import OrderedDict, defaultdict
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, NamedTuple, Never, cast
from uuid import UUID

import numpy as np
import pytest

from sapphire_flow.exceptions import SkillDiagramEncodingError
from sapphire_flow.store.skill_diagram_codec import (
    decode_skill_diagram_data,
    encode_skill_diagram_data,
)
from tests.integration.store.test_skill_diagram_jsonb import CaseName, producer_payload


def strict_parse(text: str) -> object:
    def reject(token: str) -> None:
        raise ValueError("nonfinite numeric constant")

    return json.loads(text, parse_constant=reject)


class TestSkillDiagramCodec:
    @pytest.mark.parametrize(
        "case",
        [
            "reliability_nan",
            "roc_no_events",
            "roc_all_events",
            "merged_reliability",
            "merged_roc_no_events",
            "merged_roc_all_events",
        ],
    )
    def test_actual_producer_and_merge_contract(self, case: CaseName) -> None:
        kind, payload = producer_payload(case)
        before = json.dumps(payload)
        encoded = encode_skill_diagram_data(kind, payload)
        strict_parse(json.dumps(encoded, allow_nan=False))
        decoded = decode_skill_diagram_data(kind, encoded)
        assert json.dumps(decoded) == before
        assert json.dumps(payload) == before
        assert encoded is not payload

    @pytest.mark.parametrize(
        "mutation",
        [
            "missing_bins",
            "missing_forecast",
            "extra",
            "unequal",
            "empty",
            "grid_string",
            "grid_element",
            "bool_rate",
            "string_rate",
        ],
    )
    def test_unrecognized_plain_shape_preserves_null_but_rejects_nan(
        self, mutation: str
    ) -> None:
        data: dict[Any, Any] = {
            "bins": [0.5],
            "forecast_freq": [0.5],
            "sample_counts": [0],
            "observed_freq": [None],
        }
        if mutation == "missing_bins":
            data.pop("bins")
        elif mutation == "missing_forecast":
            data.pop("forecast_freq")
        elif mutation == "extra":
            data["extra"] = 1
        elif mutation == "unequal":
            data["bins"] = [0.2, 0.8]
        elif mutation == "empty":
            data["bins"] = []
        elif mutation == "grid_string":
            data["bins"] = "0.5"
        elif mutation == "grid_element":
            data["bins"] = ["0.5"]
        else:
            data["observed_freq"] = [False if mutation == "bool_rate" else "0", None]
            data["bins"] = [0.2, 0.8]
            data["forecast_freq"] = [0.2, 0.8]
            data["sample_counts"] = [0, 0]
        assert decode_skill_diagram_data("reliability", data) is data
        invalid = deepcopy(data)
        rates = cast("list[object]", invalid["observed_freq"])
        rates[-1] = float("nan")
        with pytest.raises(SkillDiagramEncodingError, match="unsupported_"):
            encode_skill_diagram_data("reliability", invalid)

    @pytest.mark.parametrize(
        "missing",
        ["thresholds", "hit_rate", "false_alarm_rate", "n_events", "n_non_events"],
    )
    def test_partial_roc_not_normalized(self, missing: str) -> None:
        data: dict[Any, Any] = {
            "thresholds": [0.0],
            "hit_rate": [None],
            "false_alarm_rate": [None],
            "n_events": 0.0,
            "n_non_events": 0.0,
        }
        data.pop(missing)
        assert decode_skill_diagram_data("roc", data) is data
        target = "false_alarm_rate" if missing == "hit_rate" else "hit_rate"
        data[target] = [float("nan")]
        with pytest.raises(SkillDiagramEncodingError, match="unsupported_nan"):
            encode_skill_diagram_data("roc", data)

    def test_tuple_normalization_is_lazy_and_does_not_mutate(self) -> None:
        data: dict[Any, Any] = {
            "bins": (0.5,),
            "forecast_freq": (0.5,),
            "sample_counts": (0.0,),
            "observed_freq": (float("nan"),),
        }
        out = encode_skill_diagram_data("reliability", data)
        assert out["observed_freq"] == (None,)
        assert out["bins"] is data["bins"]
        assert math.isnan(data["observed_freq"][0])
        assert math.isnan(
            cast(
                "list[float]",
                decode_skill_diagram_data("reliability", out)["observed_freq"],
            )[0]
        )

    def test_null_zero_normalization_and_positive_support_unknown(self) -> None:
        data: dict[Any, Any] = {
            "bins": [0.2, 0.8],
            "forecast_freq": [0.2, 0.8],
            "sample_counts": [0, 1],
            "observed_freq": [None, None],
        }
        out: dict[str, Any] = decode_skill_diagram_data("reliability", data)
        assert math.isnan(out["observed_freq"][0])
        assert out["observed_freq"][1] is None
        assert data["observed_freq"] == [None, None]

    @pytest.mark.parametrize("zero_rate", ["hit_rate", "false_alarm_rate"])
    @pytest.mark.parametrize("opposite_support", [0.0, 1.0])
    def test_roc_integral_float_support_allows_opposite_zero_normalization(
        self, zero_rate: str, opposite_support: float
    ) -> None:
        data: dict[Any, Any] = {
            "thresholds": [0.0],
            "hit_rate": [None],
            "false_alarm_rate": [None],
            "n_events": 0.0,
            "n_non_events": 0.0,
        }
        other_support = "n_non_events" if zero_rate == "hit_rate" else "n_events"
        data[other_support] = opposite_support
        decoded: dict[str, Any] = decode_skill_diagram_data("roc", data)
        assert math.isnan(decoded[zero_rate][0])
        assert data[zero_rate] == [None]
        invalid = deepcopy(data)
        invalid[zero_rate] = [float("nan")]
        encoded = encode_skill_diagram_data("roc", invalid)
        assert encoded is not invalid
        assert encoded[zero_rate] == [None]

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_nested_nonfinite_error_is_bounded_and_does_not_expose_keys(
        self, value: float
    ) -> None:
        data: dict[Any, Any] = {
            "secret-token-" * 100: [{"observed_freq": [value], "sample_counts": [0]}],
            "metadata": "sensitive-fixture-leaf-value",
        }
        with pytest.raises(SkillDiagramEncodingError, match="key#0") as error:
            encode_skill_diagram_data("reliability", data)
        assert len(str(error.value)) <= 192
        assert "secret" not in str(error.value)
        assert "sensitive-fixture-leaf-value" not in str(error.value)

    @pytest.mark.parametrize(
        "case",
        [
            "finite_rank",
            "finite_reliability",
            "finite_roc",
            "legacy_reliability",
            "legacy_roc",
        ],
    )
    def test_finite_producers_and_legacy_are_identity_passthrough(
        self, case: CaseName
    ) -> None:
        kind, data = producer_payload(case)
        assert encode_skill_diagram_data(kind, data) is data
        assert decode_skill_diagram_data(kind, data) is data

    def test_default_key_serialization_is_not_strict_python_key_validation(
        self,
    ) -> None:
        data: dict[Any, Any] = {
            float("nan"): 1,
            float("inf"): 2,
            1: "numeric",
            "1": "string",
            None: (True, np.float64(0.5)),
        }
        out = encode_skill_diagram_data("reliability", data)
        assert out is data
        assert json.dumps(out) == json.dumps(data)
        strict_parse(json.dumps(out))

    @pytest.mark.parametrize(
        "container", ["ordered", "default", "namedtuple", "dict", "list", "tuple"]
    )
    @pytest.mark.parametrize("nonfinite", ["finite", "nan"])
    def test_container_subclasses_delegate_entire_payload(
        self, container: str, nonfinite: Literal["finite", "nan"]
    ) -> None:
        class CustomDict(dict[str, float]):
            pass

        class CustomList(list[float]):
            pass

        class CustomTuple(tuple[float, ...]):
            pass

        value = float("nan") if nonfinite == "nan" else 1.0

        class Point(NamedTuple):
            x: float

        constructors = {
            "ordered": lambda: OrderedDict[str, float](x=value),
            "default": lambda: defaultdict(int, x=value),
            "namedtuple": lambda: Point(value),
            "dict": lambda: CustomDict(x=value),
            "list": lambda: CustomList([value]),
            "tuple": lambda: CustomTuple([value]),
        }
        data: dict[Any, Any] = {
            "opaque": constructors[container](),
            "observed_freq": [float("nan")],
        }
        assert encode_skill_diagram_data("reliability", data) is data
        assert decode_skill_diagram_data("reliability", data) is data

    def test_custom_container_hooks_not_called(self) -> None:
        class Opaque(dict[str, float]):
            def items(self) -> Never:
                raise AssertionError("codec must not inspect opaque container")

        data: dict[Any, Any] = {"opaque": Opaque(x=float("nan"))}
        assert encode_skill_diagram_data("reliability", data) is data

    @pytest.mark.parametrize(
        "leaf",
        [Decimal("1"), UUID(int=1), datetime(2026, 1, 1, tzinfo=UTC), np.int64(1)],
    )
    def test_unsupported_leaves_delegate_without_coercion(self, leaf: object) -> None:
        data: dict[Any, Any] = {"value": leaf}
        assert encode_skill_diagram_data("roc", data) is data
        with pytest.raises(TypeError, match="not JSON serializable"):
            json.dumps(data)

    @pytest.mark.parametrize("shape", ["self", "tuple"])
    def test_cycles_delegate_to_native_serializer(self, shape: str) -> None:
        values: list[object] = []
        values.append(values if shape == "self" else (values,))
        data: dict[Any, Any] = {"values": values}
        assert encode_skill_diagram_data("roc", data) is data
        with pytest.raises(ValueError, match="Circular reference"):
            json.dumps(data)

    def test_deep_tree_and_shared_subtree_not_newly_rejected(self) -> None:
        leaf = [None, 1.0]
        data: dict[Any, Any] = {"a": leaf, "b": leaf}
        for _ in range(100):
            data: dict[Any, Any] = {"inner": data}
        assert encode_skill_diagram_data("roc", data) is data
        assert json.dumps(data)
        for _ in range(10000):
            data: dict[Any, Any] = {"inner": data}
        assert encode_skill_diagram_data("roc", data) is data
        with pytest.raises(RecursionError, match="recursion"):
            json.dumps(data)

    @pytest.mark.parametrize("value", [float("inf"), float("-inf")])
    def test_infinity_at_zero_support_is_not_an_undefined_rate(
        self, value: float
    ) -> None:
        data: dict[str, object] = {
            "bins": [0.5],
            "forecast_freq": [0.5],
            "sample_counts": [0],
            "observed_freq": [value],
        }
        with pytest.raises(SkillDiagramEncodingError, match="unsupported_infinity"):
            encode_skill_diagram_data("reliability", data)

    def test_finite_out_of_range_is_not_repaired_and_strings_are_not_numbers(
        self,
    ) -> None:
        data: dict[str, object] = {
            "bins": [5.0],
            "forecast_freq": [-2.0],
            "sample_counts": [0],
            "observed_freq": [2.0],
        }
        assert encode_skill_diagram_data("reliability", data) is data
        assert decode_skill_diagram_data("reliability", data) is data
        strings: dict[str, object] = {"observed_freq": ["NaN", "Infinity", None]}
        assert encode_skill_diagram_data("reliability", strings) is strings
        assert decode_skill_diagram_data("reliability", strings) is strings

    @pytest.mark.parametrize(
        "bad_support",
        [
            pytest.param(False, id="false"),
            pytest.param(True, id="true"),
            pytest.param("0", id="string"),
            pytest.param(None, id="null"),
            pytest.param(-1, id="negative"),
            pytest.param(0.5, id="fractional"),
            pytest.param(float("nan"), id="nan"),
            pytest.param(float("inf"), id="infinity"),
            pytest.param(float("-inf"), id="negative_infinity"),
            pytest.param([0], id="list"),
            pytest.param((0,), id="tuple"),
            pytest.param({"value": 0}, id="dict"),
        ],
    )
    def test_bad_reliability_support_blocks_separate_eligible_bin(
        self, bad_support: object
    ) -> None:
        data: dict[Any, Any] = {
            "bins": [0.25, 0.75],
            "forecast_freq": [0.25, 0.75],
            "sample_counts": [0, bad_support],
            "observed_freq": [None, None],
        }
        before = json.dumps(data)
        assert decode_skill_diagram_data("reliability", data) is data
        assert json.dumps(data) == before
        invalid = deepcopy(data)
        invalid["observed_freq"][0] = float("nan")
        with pytest.raises(SkillDiagramEncodingError, match="unsupported_"):
            encode_skill_diagram_data("reliability", invalid)

    @pytest.mark.parametrize("bad_denominator", ["n_events", "n_non_events"])
    @pytest.mark.parametrize(
        "bad_support",
        [
            pytest.param(False, id="false"),
            pytest.param(True, id="true"),
            pytest.param("0", id="string"),
            pytest.param(None, id="null"),
            pytest.param(-1, id="negative"),
            pytest.param(0.5, id="fractional"),
            pytest.param(float("nan"), id="nan"),
            pytest.param(float("inf"), id="infinity"),
            pytest.param(float("-inf"), id="negative_infinity"),
            pytest.param([0], id="list"),
            pytest.param((0,), id="tuple"),
            pytest.param({"value": 0}, id="dict"),
        ],
    )
    def test_bad_roc_support_blocks_opposite_eligible_rate(
        self, bad_denominator: str, bad_support: object
    ) -> None:
        data: dict[Any, Any] = {
            "thresholds": [0.0, 1.0],
            "hit_rate": [None, None],
            "false_alarm_rate": [None, None],
            "n_events": 0.0,
            "n_non_events": 0.0,
        }
        data[bad_denominator] = bad_support
        before = json.dumps(data)
        assert decode_skill_diagram_data("roc", data) is data
        assert json.dumps(data) == before
        eligible_rate = (
            "false_alarm_rate" if bad_denominator == "n_events" else "hit_rate"
        )
        invalid = deepcopy(data)
        invalid[eligible_rate][0] = float("nan")
        with pytest.raises(SkillDiagramEncodingError, match="unsupported_"):
            encode_skill_diagram_data("roc", invalid)

    @pytest.mark.parametrize(
        "field, malformed",
        [
            pytest.param("extra", 1, id="extra_key"),
            *[
                pytest.param(field, value, id=f"{field}_{label}")
                for field in ("thresholds", "hit_rate", "false_alarm_rate")
                for label, value in (("empty", []), ("unequal", [0.0]))
            ],
            pytest.param("thresholds", "grid", id="grid_container_string"),
            *[
                pytest.param("thresholds", [value, 1.0], id=f"grid_{label}")
                for label, value in (
                    ("string", "0"),
                    ("null", None),
                    ("bool", False),
                    ("nan", float("nan")),
                    ("inf", float("inf")),
                    ("negative_inf", float("-inf")),
                )
            ],
            *[
                pytest.param(field, [value, None], id=f"{field}_{label}")
                for field in ("hit_rate", "false_alarm_rate")
                for label, value in (("bool", False), ("string", "0"))
            ],
        ],
    )
    def test_malformed_roc_blocks_whole_shape(
        self, field: str, malformed: object
    ) -> None:
        data: dict[Any, Any] = {
            "thresholds": [0.0, 1.0],
            "hit_rate": [None, None],
            "false_alarm_rate": [None, None],
            "n_events": 0.0,
            "n_non_events": 0.0,
        }
        data[field] = malformed
        before = json.dumps(data)
        assert decode_skill_diagram_data("roc", data) is data
        assert json.dumps(data) == before
        eligible_rate = "false_alarm_rate" if field == "hit_rate" else "hit_rate"
        invalid = deepcopy(data)
        invalid[eligible_rate][0] = float("nan")
        with pytest.raises(SkillDiagramEncodingError, match="unsupported_"):
            encode_skill_diagram_data("roc", invalid)

    @pytest.mark.parametrize("positive_rate", ["hit_rate", "false_alarm_rate"])
    def test_positive_roc_denominator_nan_is_refused(self, positive_rate: str) -> None:
        data: dict[Any, Any] = {
            "thresholds": [0.0],
            "hit_rate": [None],
            "false_alarm_rate": [None],
            "n_events": 0.0,
            "n_non_events": 0.0,
        }
        denominator = "n_events" if positive_rate == "hit_rate" else "n_non_events"
        data[denominator] = 1.0
        data[positive_rate][0] = float("nan")
        with pytest.raises(SkillDiagramEncodingError, match="unsupported_nan"):
            encode_skill_diagram_data("roc", data)

    def test_positive_reliability_support_nan_is_refused(self) -> None:
        data: dict[Any, Any] = {
            "bins": [0.25, 0.75],
            "forecast_freq": [0.25, 0.75],
            "sample_counts": [0, 1],
            "observed_freq": [None, float("nan")],
        }
        with pytest.raises(SkillDiagramEncodingError, match="unsupported_nan"):
            encode_skill_diagram_data("reliability", data)

    @pytest.mark.parametrize("zero_rate", ["hit_rate", "false_alarm_rate"])
    def test_roc_tuple_sequences_preserve_kind_and_inputs(self, zero_rate: str) -> None:
        data: dict[Any, Any] = {
            "thresholds": (0.0, 1.0),
            "hit_rate": (0.0, 1.0),
            "false_alarm_rate": (0.0, 1.0),
            "n_events": 1.0,
            "n_non_events": 1.0,
        }
        denominator = "n_events" if zero_rate == "hit_rate" else "n_non_events"
        data[denominator] = 0.0
        data[zero_rate] = (float("nan"), float("nan"))
        before = json.dumps(data)
        encoded = encode_skill_diagram_data("roc", data)
        assert encoded[zero_rate] == (None, None)
        assert isinstance(encoded[zero_rate], tuple)
        decoded: dict[str, Any] = decode_skill_diagram_data("roc", encoded)
        assert isinstance(decoded[zero_rate], tuple)
        assert all(math.isnan(value) for value in decoded[zero_rate])
        assert json.dumps(data) == before
        assert encoded["thresholds"] is data["thresholds"]

    def test_nested_null_lookalike_is_not_normalized(self) -> None:
        data: dict[Any, Any] = {
            "metadata": {
                "bins": [0.5],
                "forecast_freq": [0.5],
                "sample_counts": [0],
                "observed_freq": [None],
            }
        }
        assert decode_skill_diagram_data("reliability", data) is data
        assert data["metadata"]["observed_freq"] == [None]
