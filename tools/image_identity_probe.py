from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from typing import Literal, Protocol, cast


class ImageIdentityProbeError(RuntimeError):
    pass


class DockerRunner(Protocol):
    def run(self, args: list[str], *, stdin: str | None = None) -> str: ...


@dataclass(frozen=True, kw_only=True, slots=True)
class ProbedImage:
    variant: Literal["default", "aquacast"]
    mutable_tag: str
    image_id: str
    labels: dict[str, str]
    runtime_import_version: str


class SubprocessDockerRunner:
    def run(self, args: list[str], *, stdin: str | None = None) -> str:
        proc = subprocess.run(
            ["docker", *args],
            input=stdin,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            message = (
                proc.stderr.strip() or proc.stdout.strip() or "docker command failed"
            )
            raise ImageIdentityProbeError(message)
        return proc.stdout.strip()


def _json_object_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ImageIdentityProbeError("docker image inspect returned non-list JSON")
    items = cast("list[object]", value)
    result: list[dict[str, object]] = []
    for item_raw in items:
        if not isinstance(item_raw, dict):
            raise ImageIdentityProbeError(
                "docker image inspect returned invalid image item"
            )
        result.append(cast("dict[str, object]", item_raw))
    return result


def _str_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    mapping = cast("dict[object, object]", value)
    result: dict[str, str] = {}
    for key_raw, item_raw in mapping.items():
        if isinstance(key_raw, str) and isinstance(item_raw, str):
            result[key_raw] = item_raw
    return result


def _inspect_image(
    runner: DockerRunner,
    image_tag: str,
    expected_version: str,
    expected_revision: str,
) -> tuple[str, dict[str, str]]:
    raw = runner.run(["image", "inspect", image_tag])
    inspected = _json_object_list(json.loads(raw))
    if not inspected:
        raise ImageIdentityProbeError("docker image inspect returned no image data")
    data = inspected[0]
    image_id_raw = data.get("Id")
    if not isinstance(image_id_raw, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", image_id_raw
    ):
        raise ImageIdentityProbeError("docker image inspect returned invalid image ID")
    config_raw = data.get("Config")
    config = (
        cast("dict[str, object]", config_raw) if isinstance(config_raw, dict) else {}
    )
    labels = _str_mapping(config.get("Labels"))
    expected = {
        "org.opencontainers.image.version": expected_version,
        "org.opencontainers.image.revision": expected_revision,
    }
    for key, value in expected.items():
        if labels.get(key) != value:
            raise ImageIdentityProbeError(f"container label {key} mismatch")
    return image_id_raw, labels


def _shared_identity_payload() -> str:
    return r"""
from __future__ import annotations

import importlib.metadata as md
import importlib.resources as resources
import os
import sapphire_flow

expected = os.environ["EXPECTED_VERSION"]
runtime = sapphire_flow.__version__
metadata = md.version("sapphire-flow")
if runtime != expected or metadata != expected:
    raise SystemExit(
        "version mismatch: "
        f"runtime={runtime!r} metadata={metadata!r} expected={expected!r}"
    )
root = resources.files("sapphire_flow")
required = [
    root / "py.typed",
    root / "models" / "aquacast" / "configs" / "cmal_pool_pt.yaml",
    root / "models" / "aquacast" / "configs" / "cmal_small.yaml",
]
missing = [str(path) for path in required if not path.is_file()]
if missing:
    raise SystemExit("missing package resources: " + ", ".join(missing))
print(runtime)
""".lstrip()


def _default_payload() -> str:
    return (
        _shared_identity_payload()
        + r"""
import importlib.util
import sapphire_flow.config.deployment
for module_name in ("aquacast", "torch"):
    if importlib.util.find_spec(module_name) is not None:
        raise SystemExit(f"forbidden optional dependency present: {module_name}")
"""
    )


def _aquacast_payload() -> str:
    return (
        _shared_identity_payload()
        + r"""
import aquacast
import torch
from sapphire_flow.models.aquacast import AquacastShim, CmalPoolPT, CmalSmall
for model_cls in (CmalPoolPT, CmalSmall):
    if not issubclass(model_cls, AquacastShim):
        raise SystemExit(f"{model_cls.__name__} is not an AquacastShim")
    if not getattr(model_cls, "CONFIG_FILENAME", ""):
        raise SystemExit(f"{model_cls.__name__} has no bound config")
print(CmalPoolPT.__name__)
"""
    )


def probe_run_args(
    image_id: str, expected_version: str, expected_revision: str
) -> list[str]:
    return [
        "run",
        "--rm",
        "-i",
        "--user",
        "app",
        "--entrypoint",
        "/app/.venv/bin/python",
        "-e",
        f"EXPECTED_VERSION={expected_version}",
        "-e",
        f"EXPECTED_REVISION={expected_revision}",
        image_id,
        "-",
    ]


def probe_image(
    image_tag: str,
    expected_version: str,
    expected_revision: str,
    *,
    variant: Literal["default", "aquacast"],
    runner: DockerRunner | None = None,
) -> ProbedImage:
    docker = runner or SubprocessDockerRunner()
    image_id, labels = _inspect_image(
        docker, image_tag, expected_version, expected_revision
    )
    payload = _default_payload() if variant == "default" else _aquacast_payload()
    runtime = docker.run(
        probe_run_args(image_id, expected_version, expected_revision),
        stdin=payload,
    )
    first_line = runtime.splitlines()[0] if runtime else ""
    if first_line != expected_version:
        raise ImageIdentityProbeError("container runtime version mismatch")
    return ProbedImage(
        variant=variant,
        mutable_tag=image_tag,
        image_id=image_id,
        labels=labels,
        runtime_import_version=first_line,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=["default", "aquacast"], required=True)
    parser.add_argument("--image-tag", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-revision", required=True)
    args = parser.parse_args(argv)
    image = probe_image(
        args.image_tag,
        args.expected_version,
        args.expected_revision,
        variant=args.variant,
    )
    print(json.dumps({"variant": image.variant, "image_id": image.image_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
