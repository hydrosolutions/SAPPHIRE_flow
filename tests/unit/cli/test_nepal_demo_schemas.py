import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import ValidationError

from sapphire_flow.cli.nepal_demo_schemas import DemoBundle

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests/fixtures/nepal_demo"
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
            (ROOT / "docs/spec/nepal-demo-bundle-v1.schema.json").read_text()
        )
        assert schema == DemoBundle.model_json_schema()
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(bundle, schema)
        DemoBundle.model_validate(bundle)

    @pytest.mark.parametrize(
        ("path", "value", "message"),
        [
            (("manifest", "region"), "switzerland", "nepal"),
            (("manifest", "source_mode"), "operational", "illustrative"),
            (("manifest", "forecast", "qc_status"), "qc_passed", "synthetic_eligible"),
            (("manifest", "forecast", "issued_at"), "2025-08-12T00:00:00", "pattern"),
            (("manifest", "forecast", "issued_at"), "2025-02-30T00:00:00Z", "day"),
            (("series", "observations", "values", 0), float("nan"), "finite"),
            (("series", "forecast", "series", "0.25", 0), 10000, "ordered"),
            (("series", "forecast", "series", "0.5"), [1.0], "lengths"),
            (("series", "observations", "values", 120), 0, "nulls"),
            (("series", "verification", "values", 40), 0, "nulls"),
            (
                ("series", "observations", "gaps", 0, "end"),
                "2025-08-10T07:00:00Z",
                "gap",
            ),
            (
                ("series", "verification", "valid_times", 0),
                "2025-08-12T00:00:00Z",
                "valid times",
            ),
            (("series", "forecast", "forecast_id"), "different", "identity"),
            (
                ("manifest", "forecast", "horizon_end"),
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
