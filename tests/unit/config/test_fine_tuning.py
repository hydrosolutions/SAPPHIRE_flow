from __future__ import annotations

from copy import deepcopy

import pytest

from sapphire_flow.config.fine_tuning import parse_fine_tuning


def request_document() -> dict:
    return {
        "schema_version": 1,
        "request_id": "11111111-1111-4111-8111-111111111111",
        "deployment": {
            "name": "train-models/train-models",
            "tenant": "bafu",
            "operator": "admin",
        },
        "model": {
            "id": "cmal_small",
            "group_id": "22222222-2222-4222-8222-222222222222",
            "base_artifact_id": "33333333-3333-4333-8333-333333333333",
        },
        "data": {
            "weather_source": "hybrid",
            "time_step_hours": 24,
            "fitting_start": "2010-01-01T00:00:00Z",
            "fitting_end": "2015-01-01T00:00:00Z",
            "evaluation_start": "2016-01-01T00:00:00Z",
            "evaluation_end": "2017-01-01T00:00:00Z",
        },
        "training": {"seed": 42, "parameters": {"trainer": {"max_epochs": 1}}},
    }


class TestFineTuningConfig:
    def test_roundtrip_retains_explicit_source_and_model_parameters(self) -> None:
        request = parse_fine_tuning(request_document())
        restored = parse_fine_tuning(request.payload())
        assert restored == request
        assert restored.sha256 == request.sha256
        assert restored.weather_source.value == "hybrid"

    def test_rejects_overlap(self) -> None:
        document = request_document()
        document["data"]["evaluation_start"] = "2014-01-01T00:00:00Z"
        with pytest.raises(ValueError, match="evaluation must follow"):
            parse_fine_tuning(document)

    @pytest.mark.parametrize("value", [0, -1, True, "24"])
    def test_rejects_invalid_step(self, value: object) -> None:
        document = request_document()
        document["data"]["time_step_hours"] = value
        with pytest.raises(ValueError, match="time_step_hours"):
            parse_fine_tuning(document)

    def test_refuses_implicit_timezone(self) -> None:
        document = request_document()
        document["data"]["fitting_start"] = "2010-01-01"
        with pytest.raises(ValueError, match="timezone"):
            parse_fine_tuning(document)

    def test_refuses_unknown_option(self) -> None:
        document = request_document()
        document["promote"] = True
        with pytest.raises(ValueError, match="Extra inputs"):
            parse_fine_tuning(document)

    def test_digest_changes_when_model_parameters_change(self) -> None:
        document = request_document()
        changed = deepcopy(document)
        changed["training"]["parameters"]["trainer"]["max_epochs"] = 2
        assert parse_fine_tuning(document).sha256 != parse_fine_tuning(changed).sha256

    def test_rejects_nonfinite_model_parameter(self) -> None:
        document = request_document()
        document["training"]["parameters"]["learning_rate"] = float("nan")
        with pytest.raises(ValueError, match="Out of range float"):
            parse_fine_tuning(document)
