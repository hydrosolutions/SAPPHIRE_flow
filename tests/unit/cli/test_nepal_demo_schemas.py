import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import ValidationError

from sapphire_flow.cli.nepal_demo_schemas import DemoBundle

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests/fixtures/nepal_demo_v2"
FILES = {
    "manifest": "region.json",
    "series": "series.json",
    "station": "station.geojson",
    "basin": "basin.geojson",
}


@pytest.fixture
def bundle() -> dict[str, Any]:
    return {
        key: json.loads((FIXTURE / name).read_text()) for key, name in FILES.items()
    }


class TestDemoBundle:
    def test_example_and_committed_schema_match_boundary(
        self, bundle: dict[str, Any]
    ) -> None:
        schema = json.loads(
            (ROOT / "docs/spec/nepal-demo-bundle-v2.schema.json").read_text()
        )
        assert schema == DemoBundle.model_json_schema()
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(bundle, schema)
        DemoBundle.model_validate(bundle)

    @pytest.mark.parametrize(
        ("path", "value", "message"),
        [
            (("manifest", "schema_version"), "flow-map-region-bundle/v1", "v2"),
            (("manifest", "forecast_cycle", "cadence_seconds"), 3600, "10800"),
            (
                ("series", "observations", "window_end"),
                "2025-08-12T00:00:00Z",
                "half-open",
            ),
            (("manifest", "region"), "switzerland", "nepal"),
            (("manifest", "source_mode"), "operational", "illustrative"),
            (
                ("series", "forecasts", 0, "qc_status"),
                "qc_passed",
                "synthetic_eligible",
            ),
            (("series", "forecasts", 0, "issued_at"), "2025-08-12T00:00:00", "pattern"),
            (("series", "forecasts", 0, "issued_at"), "2025-02-30T00:00:00Z", "day"),
            (("series", "observations", "values", 0), float("nan"), "finite"),
            (("series", "forecasts", 0, "series", "0.25", 0), 10000, "ordered"),
            (("series", "forecasts", 0, "series", "0.5"), [1.0], "at least 24"),
            (("series", "observations", "values", 120), 0, "nulls"),
            (("series", "observations", "values", 185), 0, "nulls"),
            (
                ("series", "observations", "gaps", 0, "end"),
                "2025-08-10T07:00:00Z",
                "gap",
            ),
            (
                ("series", "forecasts", 0, "valid_times", 0),
                "2025-08-12T00:00:00Z",
                "valid times",
            ),
            (("series", "forecasts", 0, "forecast_id"), "different", "identity"),
            (
                ("series", "forecasts", 0, "horizon_end"),
                "2025-08-15T00:00:00Z",
                "half-open",
            ),
            (("basin", "features", 0, "id"), "wrong", "NP_1_00092"),
            (
                ("basin", "features", 0, "geometry", "coordinates", 0, 0, 1),
                [200, 27],
                "longitude",
            ),
            (
                ("basin", "features", 0, "geometry", "coordinates", 0, 0, 1),
                [86, float("inf")],
                "finite",
            ),
            (
                ("basin", "features", 0, "geometry", "coordinates", 0, 0, 0),
                [86, 27],
                "closed",
            ),
            (
                ("basin", "features", 0, "geometry", "coordinates"),
                [[[[0, 0], [1, 0], [1, 1], [0, 0]]]],
                "cover",
            ),
            (
                ("station", "features", 0, "geometry", "coordinates"),
                [0, 0],
                "coordinates",
            ),
        ],
    )
    def test_rejects_invalid_handoff(
        self,
        bundle: dict[str, Any],
        path: tuple[str | int, ...],
        value: object,
        message: str,
    ) -> None:
        changed = copy.deepcopy(bundle)
        parent = changed
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = value
        with pytest.raises(ValidationError, match=message):
            DemoBundle.model_validate(changed)

    def test_zero_is_preserved(self, bundle: dict[str, Any]) -> None:
        bundle["series"]["observations"]["values"][0] = 0
        parsed = DemoBundle.model_validate(bundle)
        assert parsed.series.observations.values[0] == 0

    def test_schema_rejects_invalid_nullable_value(
        self, bundle: dict[str, Any]
    ) -> None:
        bundle["series"]["observations"]["values"][0] = "invalid"
        with pytest.raises(jsonschema.ValidationError, match="not valid"):
            jsonschema.validate(bundle, DemoBundle.model_json_schema())

    @pytest.mark.parametrize("change", ["missing_issue", "reversed", "duplicate"])
    def test_rejects_invalid_issue_sequence(
        self, bundle: dict[str, Any], change: str
    ) -> None:
        forecasts = bundle["series"]["forecasts"]
        if change == "missing_issue":
            forecasts.pop()
            message = "at least 8"
        elif change == "reversed":
            forecasts[1], forecasts[2] = forecasts[2], forecasts[1]
            message = "6-hour"
        else:
            forecasts[1] = copy.deepcopy(forecasts[0])
            message = "unique"
        with pytest.raises(ValidationError, match=message):
            DemoBundle.model_validate(bundle)

    def test_rejects_truncated_observations(self, bundle: dict[str, Any]) -> None:
        history = bundle["series"]["observations"]
        history["valid_times"] = history["valid_times"][:168]
        history["values"] = history["values"][:168]
        with pytest.raises(ValidationError, match="at least 211"):
            DemoBundle.model_validate(bundle)

    @pytest.mark.parametrize("legacy", ["verification", "superseded"])
    def test_rejects_parallel_truth_series(
        self, bundle: dict[str, Any], legacy: str
    ) -> None:
        bundle["series"][legacy] = []
        with pytest.raises(ValidationError, match="Extra inputs"):
            DemoBundle.model_validate(bundle)

    def test_each_playback_step_has_history_and_full_predecessor_bands(
        self, bundle: dict[str, Any]
    ) -> None:
        parsed = DemoBundle.model_validate(bundle)
        issues = parsed.series.forecasts
        for index, active in enumerate(issues):
            visible = [f for f in issues if f.issued_at <= active.issued_at]
            observations = [
                t
                for t in parsed.series.observations.valid_times
                if t <= active.issued_at
            ]
            assert visible == issues[: index + 1]
            assert len(observations) == 169 + 6 * index
            assert observations[-1] == active.issued_at
            assert all(
                len(f.series.lower) == len(f.series.median) == len(f.series.upper) == 24
                for f in visible
            )

    def test_exported_schema_matches_reference(self) -> None:
        assert (
            json.loads((FIXTURE / "schema.json").read_text())
            == DemoBundle.model_json_schema()
        )
