from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.templating import Jinja2Templates

from sapphire_flow.api.deps import lifespan
from sapphire_flow.api.errors import http_exception_handler, unhandled_exception_handler

_TEMPLATES_DIR = Path(__file__).parent / "templates"
# localhost fallback is for local dev; production sets PREFECT_UI_URL
# in compose (Plan 053 D4).
_PREFECT_UI_URL = os.environ.get("PREFECT_UI_URL", "http://localhost:4200")

app = FastAPI(
    title="SAPPHIRE Flow",
    lifespan=lifespan,
)
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.globals["prefect_ui_url"] = _PREFECT_UI_URL

# --- CORS ---
_cors_origins = os.environ.get("SAPPHIRE_CORS_ORIGINS", "")
if _cors_origins:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins.split(",")],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

# --- Error handlers ---
app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, unhandled_exception_handler)  # type: ignore[arg-type]

# --- Routers (existing) ---
from sapphire_flow.api.routes.dashboard import router as dashboard_router  # noqa: E402
from sapphire_flow.api.routes.forecasts import router as forecasts_router  # noqa: E402
from sapphire_flow.api.routes.health import router as health_router  # noqa: E402
from sapphire_flow.api.routes.models import router as models_router  # noqa: E402
from sapphire_flow.api.routes.stations import router as stations_router  # noqa: E402
from sapphire_flow.api.routes.tables import router as tables_router  # noqa: E402

app.include_router(health_router)
app.include_router(dashboard_router)
app.include_router(tables_router)
app.include_router(stations_router)
app.include_router(forecasts_router)
app.include_router(models_router)

import sapphire_flow.api.routes.api_alerts as _api_alerts  # noqa: E402
import sapphire_flow.api.routes.api_forecasts as _api_fcst  # noqa: E402
import sapphire_flow.api.routes.api_stations as _api_stn  # noqa: E402

app.include_router(_api_stn.router)
app.include_router(_api_fcst.router)
app.include_router(_api_alerts.router)
