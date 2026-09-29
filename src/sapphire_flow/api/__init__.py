from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from fastapi import Depends, FastAPI, HTTPException
from fastapi.templating import Jinja2Templates

from sapphire_flow.api.cors import ScopedCorsMiddleware, parse_exact_origin
from sapphire_flow.api.deps import lifespan
from sapphire_flow.api.errors import http_exception_handler, unhandled_exception_handler
from sapphire_flow.api.publication_cache import PublicationCacheMiddleware
from sapphire_flow.api.security import (
    require_admin,
    require_principal,
    require_reviewer,
)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
# localhost fallback is for local dev; production sets PREFECT_UI_URL
# in compose (Plan 053 D4).
_PREFECT_UI_URL = os.environ.get("PREFECT_UI_URL", "http://localhost:4200")

# Plan 147 Slice C (blocker fix): the headless v1.0 API is served behind an
# authenticating proxy — FastAPI's built-in interactive docs are DISABLED so
# `/openapi.json` (full schema leak), `/docs`, `/docs/oauth2-redirect`, and
# `/redoc` are never mounted UNAUTHENTICATED. There is no interactive-docs need
# on the proxied surface; admin-gating them would still ship a schema route we
# do not want. The route-matrix test (tests/unit/api/test_security.py) fails if
# any of these is re-enabled.
app = FastAPI(
    title="SAPPHIRE Flow",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
app.add_middleware(PublicationCacheMiddleware)
# jinja2 stubs over-narrow env.globals' value type to the default-globals
# union; at runtime it is a plain str-keyed dict accepting any value.
cast("dict[str, object]", templates.env.globals)["prefect_ui_url"] = _PREFECT_UI_URL

# --- CORS ---
# Plan 147 Slice C: explicit-origin CORS is REQUIRED once auth is on
# (`security.md` § CORS/CSRF, G2) — a wildcard origin would let any site's
# JS ride a browser-held bearer token. Reject "*" outright rather than
# silently downgrading it.
_cors_origins = os.environ.get("SAPPHIRE_CORS_ORIGINS", "")
_human_origin = os.environ.get("SAPPHIRE_HUMAN_DASHBOARD_ORIGIN", "").strip()
if _cors_origins or _human_origin:
    if _cors_origins.strip() == "*":
        raise RuntimeError(
            "SAPPHIRE_CORS_ORIGINS='*' is rejected once auth is enforced "
            "(security.md § CORS/CSRF) — set an explicit comma-separated "
            "origin list."
        )
    app.add_middleware(
        ScopedCorsMiddleware,
        consumer_origins=[
            parse_exact_origin(origin.strip())
            for origin in _cors_origins.split(",")
            if origin.strip()
        ],
        human_dashboard_origin=parse_exact_origin(_human_origin)
        if _human_origin
        else None,
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
# Plan 401/402: `api_review` (`GET /api/v1/qc/rules[?station_id=]`,
# `GET /api/v1/stations/{id}/skill`) is REVIEW-gated — a reviewer or admin
# token only, still applying the principal's station scope where it serves
# station data.
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
import sapphire_flow.api.routes.api_rejected_forecasts as _api_rejected  # noqa: E402
import sapphire_flow.api.routes.api_review as _api_review  # noqa: E402
import sapphire_flow.api.routes.api_stations as _api_stn  # noqa: E402
import sapphire_flow.api.routes.forecast_lab as _forecast_lab  # noqa: E402
import sapphire_flow.api.routes.forecast_publication as _publication  # noqa: E402

app.include_router(_api_stn.router, dependencies=[Depends(require_principal)])
app.include_router(_api_fcst.router, dependencies=[Depends(require_principal)])
app.include_router(_api_alerts.router, dependencies=[Depends(require_principal)])
# Plan 198 T5 — Forecast Lab snapshot export, same auth surface as the
# other `/api/v1/...` consumer routes above.
app.include_router(_forecast_lab.router, dependencies=[Depends(require_principal)])
# Plan 402 — REVIEW-gated: reviewer or admin token only (Plan 401).
app.include_router(_api_review.router, dependencies=[Depends(require_reviewer)])
# Plan 404 T3 — its own route-level auth (`require_reviewer_or_human`,
# matrix class REVIEW_OR_HUMAN): a reviewer/admin service token OR a named
# human with a current station `review` grant. Registered with NO router-
# level `dependencies=`, deliberately outside Plan 402's `require_reviewer`
# router above — that gate admits service-token principals only.
app.include_router(_api_rejected.router)
# Review routes depend on a named OIDC human. Consumer publication routes
# retain the existing service-token authorization and station scope.
app.include_router(_publication.router)
