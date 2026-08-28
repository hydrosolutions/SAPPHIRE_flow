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
_SCHEMA_PATH = _REPO_ROOT / "docs/spec/forecast-lab-snapshot-v2.schema.json"
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
            "docs/spec/forecast-lab-snapshot-v2.schema.json has drifted from "
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

    @staticmethod
    def _assert_all_timestamps_utc(document: Any) -> None:
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
                and (
                    "_at" in path
                    or path.endswith(
                        ("_start", "_end", "valid_time", "measurement_time")
                    )
                )
                and not _TIMESTAMP_RE.match(node)
            ):
                offenders.append(f"{path}={node!r}")

        walk(document, "$")
        assert offenders == []

    def test_every_timestamp_leaf_is_rfc3339_utc_with_z_suffix(self) -> None:
        # `valid_time` was absent from the path predicate until the review
        # caught it — and it is the commonest timestamp in the document, so
        # the walker was silent on most of what it claimed to cover.
        self._assert_all_timestamps_utc(_load_json(_EXAMPLE_PATH))

    def test_the_timestamp_walker_actually_rejects_a_non_utc_offset(self) -> None:
        # Negative control for the test above: without this, a walker whose
        # path predicate misses a field name would report "no offenders" on a
        # fixture that is in fact non-compliant, and pass forever.
        example = _load_json(_EXAMPLE_PATH)
        example["stations"][0]["observations"]["points"][0]["valid_time"] = (
            "2026-08-21T12:40:00+02:00"
        )
        with pytest.raises(AssertionError):
            self._assert_all_timestamps_utc(example)

    def test_no_numeric_leaf_is_nan_or_infinity(self) -> None:
        # json.loads already rejects literal NaN/Infinity tokens unless
        # parse_constant is overridden — assert the raw text contains none.
        # (Numeric-string leaves are caught by the JSON Schema validation
        # test above, which types every numeric field.)
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


class TestFixtureStationIdentityIsConsistent:
    """Plan 204 T3 — a real BAFU code must never wear another station's
    metadata (external finding, SAPPHIRE-flow-map agent, 2026-08-27: code
    `2091` was paired with Chancy's name/coordinates). Locks the known-good
    identity for every code the fixture uses, so this cannot silently
    regress on the next regeneration."""

    _KNOWN_STATION_IDENTITIES = {
        "2009": {
            "name": "Porte_du_Scex",
            "longitude": 6.89,
            "latitude": 46.35,
            "basin_area_km2": 5239.4,
        },
        "2091": {
            "name": "Rheinfelden-Messstation",
            "longitude": 7.8,
            "latitude": 47.56,
            "basin_area_km2": 34479.4,
        },
    }

    def test_fixture_uses_exactly_the_known_station_codes(self) -> None:
        # A silent skip on an unrecognised code would let the regeneration
        # swap in a different, unmapped real code (or restore 2091's
        # incorrect data under a code this test never learns about)
        # without failing anything below.
        example = _load_json(_EXAMPLE_PATH)
        fixture_codes = {station["station"]["code"] for station in example["stations"]}
        assert fixture_codes == set(self._KNOWN_STATION_IDENTITIES)

    def test_fixture_station_codes_and_names_are_mutually_consistent(self) -> None:
        example = _load_json(_EXAMPLE_PATH)
        for station in example["stations"]:
            code = station["station"]["code"]
            expected = self._KNOWN_STATION_IDENTITIES[code]
            assert station["station"]["name"] == expected["name"], (
                f"station {code} carries {station['station']['name']!r}, "
                f"expected {expected['name']!r}"
            )
            assert station["station"]["location"]["longitude"] == expected["longitude"]
            assert station["station"]["location"]["latitude"] == expected["latitude"]
            assert station["station"]["basin_area_km2"] == expected["basin_area_km2"], (
                f"station {code} carries basin_area_km2="
                f"{station['station']['basin_area_km2']!r}, "
                f"expected {expected['basin_area_km2']!r}"
            )
            assert station["station"]["location"]["latitude"] == expected["latitude"]
