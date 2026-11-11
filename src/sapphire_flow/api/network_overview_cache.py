from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING

from starlette.datastructures import MutableHeaders
from starlette.middleware.gzip import GZipMiddleware

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from sapphire_flow.api.network_overview_schemas import NetworkOverviewResponse
    from sapphire_flow.api.security import Principal
    from sapphire_flow.types.enums import ForecastDataUse

PRIVATE_OVERVIEW_CACHE = "private, no-cache, max-age=0, must-revalidate"


def network_overview_path(path: str) -> bool:
    return re.fullmatch(r"/api/v1/networks/[^/]+/overview/?", path) is not None


def overview_etag(
    body: NetworkOverviewResponse,
    principal: Principal,
    purposes: tuple[ForecastDataUse, ...],
) -> str:
    semantic = body.model_dump(mode="json", exclude={"generated_at"})
    for item in semantic["items"]:
        for parameter in ("water_level", "discharge"):
            # This describes the full200's latest query time, not data identity.
            del item[parameter]["latest"]["end"]
    context = {
        "token": str(principal.token_id),
        "role": principal.role.value,
        "tenant": str(principal.tenant_id),
        "stations": sorted(str(sid) for sid in principal.station_ids),
        "purposes": sorted(purpose.value for purpose in purposes),
        "body": semantic,
    }
    digest = hashlib.sha256(
        json.dumps(
            context, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    return f'W/"{digest}"'


def etag_matches(header: str | None, etag: str) -> bool:
    if header is None:
        return False
    if header.strip() == "*":
        return True
    tags = re.fullmatch(
        r'\s*(?:W/)?"[^"\x00-\x20]*"(?:\s*,\s*(?:W/)?"[^"\x00-\x20]*")*\s*', header
    )
    if tags is None:
        return False
    target = etag.removeprefix("W/")
    return any(
        value.strip().removeprefix("W/") == target for value in header.split(",")
    )


class NetworkOverviewCacheMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not network_overview_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        async def cache_policy(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if (
                    scope["method"] == "GET"
                    and message["status"] in (200, 304)
                    and "etag" in headers
                ):
                    headers["Cache-Control"] = PRIVATE_OVERVIEW_CACHE
                    vary = {
                        entry.strip()
                        for entry in headers.get("Vary", "").split(",")
                        if entry.strip()
                    }
                    headers["Vary"] = ", ".join(
                        sorted(vary | {"Authorization", "Origin", "Accept-Encoding"})
                    )
                else:
                    headers["Cache-Control"] = "no-store"
                    if "etag" in headers:
                        del headers["etag"]
            await send(message)

        await GZipMiddleware(self.app, minimum_size=1000, compresslevel=5)(
            scope, receive, cache_policy
        )
