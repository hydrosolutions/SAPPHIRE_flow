"""Explicit real-Chrome checks; run with the documented test-only uv overlay."""

from __future__ import annotations

import socket
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

import pytest
import uvicorn

from sapphire_flow.basin_operator.web import create_operator_app
from sapphire_flow.basin_operator.workspace import BasinWorkspace


@pytest.fixture
def operator_url(
    tmp_path: Path, synthetic_package: Path, inventory: bytes
) -> Iterator[str]:
    workspace = BasinWorkspace(tmp_path / "browser-review", ())
    workspace.upload(inventory, ".csv")
    workspace.confirm()
    workspace.attach_package(synthetic_package)
    ready = threading.Event()

    class Server(uvicorn.Server):
        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets)
            ready.set()

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        app = create_operator_app(workspace, port=port, package_dir=synthetic_package)
        server = Server(uvicorn.Config(app, access_log=False, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        thread.start()
        try:
            assert ready.wait(10), "isolated server did not start"
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


def test_visible_feedback(operator_url: str) -> None:
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1000, "height": 600})
        # External basemap availability is not part of local button behavior.
        page.route("https://**/*", lambda route: route.abort())
        requests: list[str] = []
        page.on("request", lambda request: requests.append(request.url))
        page.goto(operator_url)
        for reviewer in ("", "   "):
            page.locator("#reviewer").fill(reviewer)
            page.locator('[data-decision="approved"]').click()
            expect(page.locator("#reviewer")).to_be_focused()
            expect(page.locator("#reviewer-message")).to_be_in_viewport()
            expect(page.locator("#reviewer-message")).to_contain_text("reviewer")
        assert not any(url.endswith("/decision") for url in requests)
        expect(page.locator("#extract")).to_be_disabled()
        expect(
            page.get_by_text("Existing extraction loaded", exact=False)
        ).to_be_visible()
        page.locator("#select-none").click()
        page.locator('[data-decision="approved"]').click()
        expect(page.locator("#message")).to_contain_text("Select at least one")
        expect(page.locator("#message")).to_be_focused()
        expect(page.locator("#message")).to_be_in_viewport()
        page.locator("#select-all").click()
        page.locator("#reviewer").fill("Synthetic hydrologist")
        with page.expect_response("**/decision") as rejected:
            page.locator('[data-decision="approved"]').click()
        assert rejected.value.status == 400
        expect(page.locator("#message")).to_contain_text("acknowledgment")
        expect(page.locator("#message")).to_be_focused()
        expect(page.locator("#message")).to_be_in_viewport()
        page.locator("#ack").check()
        with page.expect_response("**/decision") as accepted:
            page.locator('[data-decision="approved"]').click()
        assert accepted.value.status == 200
        expect(page.locator("tbody").last).to_contain_text("approved")
        page.route("**/export", lambda route: route.abort())
        page.locator("#export").click()
        expect(page.locator("#message")).to_contain_text("Failed to fetch")
        expect(page.locator("#message")).to_be_focused()
        expect(page.locator("#message")).to_be_in_viewport()
        page.unroute("**/export")
        with page.expect_response("**/export") as exported:
            page.locator("#export").click()
        assert exported.value.status == 200
        expect(page.locator("#message")).to_contain_text("Handover saved:")
        expect(page.locator("#message")).to_be_focused()
        expect(page.locator("#message")).to_be_in_viewport()
        assert page.locator("#message").get_attribute("aria-live") == "polite"
        feedback = page.request.get(operator_url + "/feedback")
        assert feedback.ok
        assert "const csrf=" not in feedback.text()
        page.goto(operator_url + "/feedback")
        expect(
            page.locator("[data-decision], #extract, #export, #confirm")
        ).to_have_count(0)
        browser.close()
