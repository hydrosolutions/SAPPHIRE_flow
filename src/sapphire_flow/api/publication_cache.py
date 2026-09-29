from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.datastructures import MutableHeaders

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _forecast_bearing_path(path: str) -> bool:
    return (
        path.startswith("/api/v1/review/forecasts")
        or path.startswith("/api/v1/forecasts/")
        or path == "/api/v1/forecast-publications"
        or (
            path.startswith("/api/v1/stations/")
            and (
                "/forecasts" in path
                or "/forecast-publications" in path
                or "/rejected-forecasts" in path
            )
        )
        or path == "/api/v1/forecast-lab/snapshot"
    )


class PublicationCacheMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _forecast_bearing_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        async def no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, no_store)
