#!/usr/bin/env python3
"""Plan 402 T4 — writes docs/spec/api-v1-map.openapi.json from
api/map_contract.py::build_map_openapi(). Run this after any change to the
map's routes or their response schemas, and bump MAP_CONTRACT_VERSION per
D14 (additive -> minor, anything else -> major, announced to the map
session before deploy) — tests/unit/api/test_map_contract.py's drift test
fails until the file is regenerated, and
tools/check_map_contract_version.py's CI gate fails a PR that changes the
file without a greater version.

Usage::

    uv run python tools/generate_map_contract.py
"""

from __future__ import annotations

import json
from pathlib import Path

from sapphire_flow.api.map_contract import build_map_openapi

_OUTPUT = Path(__file__).resolve().parent.parent / "docs/spec/api-v1-map.openapi.json"


def main() -> None:
    doc = build_map_openapi()
    _OUTPUT.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(f"wrote {_OUTPUT}")


if __name__ == "__main__":
    main()
