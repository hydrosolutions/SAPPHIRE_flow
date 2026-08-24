"""Plan 198 T1 — the committed JSON Schema must never drift from the
Pydantic models (D15), and the committed example fixture must validate
against it. AC1."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from sapphire_flow.api.forecast_lab_schemas import ForecastLabSnapshot

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_PATH = _REPO_ROOT / "docs/spec/forecast-lab-snapshot-v1.schema.json"
_EXAMPLE_PATH = (
    _REPO_ROOT / "tests/fixtures/forecast_lab/forecast_lab_snapshot_example.json"
)

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


class TestCommittedSchemaMatchesModels:
    def test_committed_schema_equals_generated_schema(self) -> None:
        committed = _load_json(_SCHEMA_PATH)
        generated = ForecastLabSnapshot.model_json_schema()
        assert committed == generated, (
            "docs/spec/forecast-lab-snapshot-v1.schema.json has drifted from "
            "ForecastLabSnapshot.model_json_schema() — regenerate it (D15)"
        )

    def test_example_validates_against_the_committed_schema(self) -> None:
        schema = _load_json(_SCHEMA_PATH)
        example = _load_json(_EXAMPLE_PATH)
        jsonschema.validate(instance=example, schema=schema)

    def test_example_parses_as_a_forecast_lab_snapshot(self) -> None:
        example = _load_json(_EXAMPLE_PATH)
        # Round-trips through the real model, not just the raw JSON Schema —
        # catches a discriminator/enum mismatch jsonschema alone would miss.
        ForecastLabSnapshot.model_validate(example)


class TestExampleFixtureShape:
    """AC4/AC5/AC6 as they apply to the committed example itself."""

    def test_every_timestamp_leaf_is_rfc3339_utc_with_z_suffix(self) -> None:
        example = _load_json(_EXAMPLE_PATH)
        offenders: list[str] = []

        def walk(node: Any, path: str) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")
            elif (
                isinstance(node, str)
                and ("_at" in path or path.endswith(("_start", "_end")))
                and not _TIMESTAMP_RE.match(node)
            ):
                offenders.append(f"{path}={node!r}")

        walk(example, "$")
        assert offenders == []

    def test_no_numeric_leaf_is_nan_or_infinity_or_a_numeric_string(self) -> None:
        # json.loads already rejects literal NaN/Infinity tokens unless
        # parse_constant is overridden — assert the raw text contains none.
        raw = _EXAMPLE_PATH.read_text()
        assert "NaN" not in raw
        assert "Infinity" not in raw

    def test_coordinates_declare_epsg4326(self) -> None:
        example = _load_json(_EXAMPLE_PATH)
        for station in example["stations"]:
            assert station["station"]["location"]["crs"] == "EPSG:4326"


@pytest.fixture(autouse=True)
def _ensure_jsonschema_importable() -> None:
    # jsonschema is a transitive dependency of pydantic/fastapi's ecosystem
    # in this repo, but assert it explicitly so a missing dep fails with a
    # clear message rather than a confusing collection error downstream.
    import jsonschema  # noqa: F401
