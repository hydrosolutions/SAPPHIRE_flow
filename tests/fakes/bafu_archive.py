"""Real-shape synthetic raw evidence for overview archive tests."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def write_raw_evidence(
    base: Path, code: str, issue: datetime, rows: list[dict[str, Any]]
) -> Path:
    names = dict.fromkeys(row["trace_name"] for row in rows)
    traces = []
    for name in names:
        points = sorted(
            (r for r in rows if r["trace_name"] == name), key=lambda r: r["point_index"]
        )
        traces.append(
            dict(
                name=name,
                x=[r["valid_time"].isoformat() for r in points],
                y=[r["value"] for r in points],
                yaxis="y",
                meta={"unit": "" if name == "25.-75. Percentile" else "m³/s"},
            )
        )
    payload = {
        "plot": {
            "data": traces,
            "layout": {
                "annotations": [
                    {"text": "Forecast as of", "x": issue.isoformat()},
                    {"text": "m³/s", "x": 0.0},
                ]
            },
        }
    }
    target = base / "raw" / f"{code}_q_forecast_{issue.strftime('%Y%m%dT%H%M%SZ')}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload))
    return target
