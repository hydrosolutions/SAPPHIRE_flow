from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from sapphire_flow.api.deps import lifespan

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_PREFECT_UI_URL = os.environ.get("PREFECT_UI_URL", "http://localhost:4200")

app = FastAPI(title="SAPPHIRE Flow", lifespan=lifespan)
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.globals["prefect_ui_url"] = _PREFECT_UI_URL

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
