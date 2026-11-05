"""Explicit local operator launch; never part of forecast or public API startup."""

from __future__ import annotations

import argparse
import fcntl
from pathlib import Path
from typing import Any

import structlog
import uvicorn
from pydantic import StrictStr, TypeAdapter

from sapphire_flow.basin_operator.web import create_operator_app
from sapphire_flow.basin_operator.workspace import BasinWorkspace
from sapphire_flow.logging import configure_cli_logging


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Local basin extraction and hydrologist review"
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--extractor-command",
        type=Path,
        help="JSON argv prefix for supported extractor in its own environment",
    )
    parser.add_argument(
        "--package-dir",
        type=Path,
        help="existing extraction to attach after confirmation",
    )
    parser.add_argument(
        "--rivers",
        type=Path,
        help="optional nearby river GeoJSON, never alternative basins",
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    command = (
        TypeAdapter(list[StrictStr]).validate_json(args.extractor_command.read_text())
        if args.extractor_command
        else []
    )
    rivers = None
    if args.rivers:
        if args.rivers.stat().st_size > 20 * 1024 * 1024:
            parser.error("river context exceeds 20 MiB")
        rivers = TypeAdapter(dict[str, Any]).validate_json(args.rivers.read_text())
        if rivers.get("type") != "FeatureCollection":
            parser.error("river context must be a GeoJSON FeatureCollection")
        if any(
            f.get("geometry", {}).get("type") not in ("LineString", "MultiLineString")
            for f in rivers.get("features", [])
        ):
            parser.error("river context may contain only river lines")
    args.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.workspace / ".operator.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("another operator is using this workspace")
        workspace = BasinWorkspace(args.workspace, tuple(command))
        configure_cli_logging()
        structlog.get_logger(__name__).info(
            "basin_operator.started", host="127.0.0.1", port=args.port
        )
        app = create_operator_app(
            workspace, port=args.port, package_dir=args.package_dir, rivers=rivers
        )
        try:
            uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
        finally:
            if workspace.process is not None and workspace.process.poll() is None:
                workspace.process.terminate()
                workspace.state["extraction"] = (
                    "interrupted; inspect extraction output before retry"
                )
                workspace.save()


if __name__ == "__main__":
    main()
