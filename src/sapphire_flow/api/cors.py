from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from fastapi.middleware.cors import CORSMiddleware

from sapphire_flow.exceptions import ConfigurationError

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

_REVIEW_PREFIX = "/api/v1/review/forecasts"


def parse_exact_origin(value: str) -> str:
    parsed = urlsplit(value)
    is_local = parsed.hostname in {"localhost", "127.0.0.1"}
    if (
        parsed.scheme not in ({"http", "https"} if is_local else {"https"})
        or not parsed.netloc
        or parsed.username is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or "*" in value
    ):
        raise ConfigurationError("CORS origin must be one exact HTTPS origin")
    return value


class ScopedCorsMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        consumer_origins: list[str],
        human_dashboard_origin: str | None,
    ) -> None:
        self._consumer = CORSMiddleware(
            app,
            allow_origins=consumer_origins,
            allow_methods=["GET"],
            allow_headers=["Authorization", "Content-Type"],
        )
        self._review = (
            CORSMiddleware(
                app,
                allow_origins=[human_dashboard_origin],
                allow_methods=["GET", "POST"],
                allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
                allow_credentials=True,
            )
            if human_dashboard_origin is not None
            else None
        )
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path == _REVIEW_PREFIX or path.startswith(f"{_REVIEW_PREFIX}/"):
            target = self._review if self._review is not None else self._app
        else:
            target = self._consumer
        await target(scope, receive, send)
