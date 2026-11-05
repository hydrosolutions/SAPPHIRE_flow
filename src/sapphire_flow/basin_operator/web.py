"""Facilitator-only loopback UI, independent of the public SAPPHIRE API."""

# Decorated local route functions are registered by FastAPI.
# pyright: reportUnusedFunction=false
from __future__ import annotations

import base64
import binascii
import json
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from sapphire_flow.exceptions import BasinPackageRejectedError

from .handover import export_handover
from .inventory import Coordinate
from .presentation import map_data, render_page
from .review import (  # noqa: TC001 -- pydantic runtime
    ReviewStatus,
    WarningAcknowledgment,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .workspace import BasinWorkspace


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suffix: Literal[".csv", ".xlsx"]
    content: str = Field(max_length=7 * 1024 * 1024)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    codes: tuple[str, ...] = Field(max_length=1000)
    status: ReviewStatus
    reviewer: str = Field(min_length=1, max_length=200)
    reason: str = Field(default="", max_length=2000)
    acknowledgment: WarningAcknowledgment
    fingerprint: str


class AdjustmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=2000)


async def request_json(request: Request) -> Any:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 7 * 1024 * 1024:
            raise HTTPException(413, "request exceeds upload limit")
    return json.loads(body)


def create_operator_app(
    workspace: BasinWorkspace,
    *,
    port: int,
    package_dir: Path | None = None,
    rivers: dict[str, Any] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    csrf = secrets.token_urlsafe(32)
    authority = f"127.0.0.1:{port}"

    @app.middleware("http")
    async def local_only(request: Request, call_next: Any) -> Any:
        if request.headers.get("host") != authority:
            return JSONResponse(
                {"detail": "use the explicit loopback address"}, status_code=403
            )
        if request.method != "GET" and (
            request.headers.get("origin") not in (None, "http://" + authority)
            or not secrets.compare_digest(request.headers.get("x-basin-csrf", ""), csrf)
        ):
            return JSONResponse({"detail": "local CSRF check failed"}, status_code=403)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            }
        )
        return response

    async def invalid_request(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    for error in (ValueError, BasinPackageRejectedError, OSError, binascii.Error):
        app.add_exception_handler(error, invalid_request)

    def page(*, interactive: bool) -> str:
        workspace.refresh()
        batch = workspace.batch() if workspace.state["package_dir"] else None
        return render_page(
            data=map_data(batch, workspace.decisions) if batch else None,
            inventory=workspace.state["inventory"],
            csrf=csrf if interactive else "",
            extraction=workspace.state["extraction"],
            rivers=rivers,
            confirmed=workspace.state["confirmed"],
        )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return page(interactive=True)

    @app.get("/feedback", response_class=HTMLResponse)
    async def feedback() -> str:
        workspace.batch()
        return page(interactive=False)

    @app.post("/upload")
    async def upload(request: Request) -> dict[str, str]:
        row = UploadRequest.model_validate(await request_json(request))
        workspace.upload(base64.b64decode(row.content, validate=True), row.suffix)
        return {"status": "inventory_read"}

    @app.post("/confirm")
    async def confirm() -> dict[str, str]:
        workspace.confirm()
        if package_dir is not None:
            workspace.attach_package(package_dir)
        return {"status": "confirmed"}

    @app.post("/extract")
    async def extract() -> dict[str, str]:
        workspace.start_extraction()
        return {"status": "running"}

    @app.post("/adjust")
    async def adjust(request: Request) -> dict[str, str]:
        row = AdjustmentRequest.model_validate(await request_json(request))
        workspace.adjust(
            row.code,
            Coordinate(latitude=row.latitude, longitude=row.longitude),
            row.reason,
        )
        return {"status": "reextraction_required"}

    @app.post("/decision")
    async def decision(request: Request) -> dict[str, str]:
        row = DecisionRequest.model_validate(await request_json(request))
        workspace.review(**row.model_dump(), now=clock().isoformat())
        return {"status": "recorded"}

    @app.post("/export")
    async def export() -> dict[str, str]:
        batch = workspace.batch()
        target = workspace.directory / "handovers" / ("approved-" + uuid4().hex)
        export_handover(
            batch,
            workspace.decisions,
            target,
            package_id=target.name,
            now=clock().isoformat(),
            map_html=page(interactive=False),
            source_archive_sha256=workspace.state.get("source_archive_sha256"),
        )
        return {"handover": str(target)}

    return app
