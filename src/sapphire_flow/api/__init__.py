from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from fastapi import Depends, FastAPI, HTTPException
from fastapi.templating import Jinja2Templates

from sapphire_flow.api.deps import lifespan
from sapphire_flow.api.errors import http_exception_handler, unhandled_exception_handler
from sapphire_flow.api.security import require_admin, require_principal

_TEMPLATES_DIR = Path(__file__).parent / "templates"
# localhost fallback is for local dev; production sets PREFECT_UI_URL
# in compose (Plan 053 D4).
_PREFECT_UI_URL = os.environ.get("PREFECT_UI_URL", "http://localhost:4200")

app = FastAPI(
    title="SAPPHIRE Flow",
    lifespan=lifespan,
)
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# jinja2 stubs over-narrow env.globals' value type to the default-globals
# union; at runtime it is a plain str-keyed dict accepting any value.
cast("dict[str, object]", templates.env.globals)["prefect_ui_url"] = _PREFECT_UI_URL

# --- CORS ---
# Plan 147 Slice C: explicit-origin CORS is REQUIRED once auth is on
# (`security.md` § CORS/CSRF, G2) — a wildcard origin would let any site's
# JS ride a browser-held bearer token. Reject "*" outright rather than
# silently downgrading it.
_cors_origins = os.environ.get("SAPPHIRE_CORS_ORIGINS", "")
if _cors_origins:
    if _cors_origins.strip() == "*":
        raise RuntimeError(
            "SAPPHIRE_CORS_ORIGINS='*' is rejected once auth is enforced "
            "(security.md § CORS/CSRF) — set an explicit comma-separated "
            "origin list."
        )
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins.split(",")],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

# --- Error handlers ---
app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, unhandled_exception_handler)  # type: ignore[arg-type]

# --- Routers ---
# Plan 147 Slice C (R3 LOCKED, F7): the legacy HTML dashboard/browser
# routers — and the JSON exports that share a router object with them
# (`.json` legacy exports, the global model skill-chart) — are ADMIN-GATED
# in full rather than individually scope-filtered. The modern `/api/v1/...`
# JSON API (api_stations/api_forecasts/api_alerts) is the one surface a
# `consumer` token can reach, with per-endpoint station-scope filtering.
from sapphire_flow.api.routes.dashboard import router as dashboard_router  # noqa: E402
from sapphire_flow.api.routes.forecasts import router as forecasts_router  # noqa: E402
from sapphire_flow.api.routes.health import (  # noqa: E402
    dashboard_router as health_dashboard_router,
)
from sapphire_flow.api.routes.health import router as health_router  # noqa: E402
from sapphire_flow.api.routes.models import router as models_router  # noqa: E402
from sapphire_flow.api.routes.stations import router as stations_router  # noqa: E402
from sapphire_flow.api.routes.tables import router as tables_router  # noqa: E402

# health_router carries BOTH the public `GET /health` and the admin-only
# `GET /health/detail` — gated per-route inside routes/health.py, not here.
app.include_router(health_router)
app.include_router(health_dashboard_router, dependencies=[Depends(require_admin)])
app.include_router(dashboard_router, dependencies=[Depends(require_admin)])
app.include_router(tables_router, dependencies=[Depends(require_admin)])
app.include_router(stations_router, dependencies=[Depends(require_admin)])
app.include_router(forecasts_router, dependencies=[Depends(require_admin)])
app.include_router(models_router, dependencies=[Depends(require_admin)])

import sapphire_flow.api.routes.api_alerts as _api_alerts  # noqa: E402
import sapphire_flow.api.routes.api_forecasts as _api_fcst  # noqa: E402
import sapphire_flow.api.routes.api_stations as _api_stn  # noqa: E402

app.include_router(_api_stn.router, dependencies=[Depends(require_principal)])
app.include_router(_api_fcst.router, dependencies=[Depends(require_principal)])
app.include_router(_api_alerts.router, dependencies=[Depends(require_principal)])
