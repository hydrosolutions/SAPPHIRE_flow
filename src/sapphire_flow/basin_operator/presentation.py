"""Read-only map renderer shared by the local page and feedback export."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from shapely.geometry import mapping

from .inventory import inventory_json
from .review import decision_json

if TYPE_CHECKING:
    from .review import ReviewBatch, ReviewDecision

_ENV = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def map_data(
    batch: ReviewBatch, decisions: tuple[ReviewDecision, ...]
) -> dict[str, Any]:
    stations = {s["code"]: s for s in inventory_json(batch.stations)}
    by_code = {
        d["station_code"]: d
        for d in decision_json(decisions)
        if d["fingerprint"] == batch.fingerprint
    }
    return {
        "package_id": batch.package.manifest.package_id,
        "fingerprint": batch.fingerprint,
        "stations": [
            {
                **stations[b.station_code],
                "outlet_lat": b.outlet_lat,
                "outlet_lon": b.outlet_lon,
                "area_km2": b.area_km2,
                "snap_distance_m": b.outlet_snap_distance_m,
                "geometry": mapping(b.geometry) if b.geometry is not None else None,
                "warnings": batch.warnings[b.station_code],
                "blockers": batch.blockers[b.station_code],
                "decision": by_code.get(
                    b.station_code, {"status": "pending", "reason": "Awaiting review"}
                ),
            }
            for b in batch.package.basins
        ],
    }


def render_page(
    *,
    data: dict[str, Any] | None,
    inventory: list[dict[str, Any]],
    csrf: str = "",
    extraction: str = "",
    rivers: dict[str, Any] | None = None,
    confirmed: bool = False,
) -> str:
    return _ENV.get_template("review.html").render(
        data=data,
        inventory=inventory,
        csrf=csrf,
        extraction=extraction,
        rivers=rivers,
        confirmed=confirmed,
    )
