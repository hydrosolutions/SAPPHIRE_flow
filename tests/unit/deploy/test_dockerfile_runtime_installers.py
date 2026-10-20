import shlex
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]


class TestRuntimeInstallers:
    def test_removes_complete_global_installer_only_in_final_stage(self) -> None:
        dockerfile = (_REPO_ROOT / "Dockerfile").read_text()
        builder, runtime = dockerfile.rsplit("\nFROM ", 1)
        package = "/usr/local/lib/python3.14/site-packages/pip"
        assert package not in builder
        instructions = runtime.replace("\\\n", "").splitlines()
        removal = next(
            line for line in instructions if line.startswith(f"RUN rm -rf {package}")
        )
        args = shlex.split(removal)
        boundary = args.index("&&")
        assert set(args[3:boundary]) == {
            package,
            package + "-*.dist-info",
            "/usr/local/lib/python3.14/ensurepip",
            "/usr/local/bin/pip",
            "/usr/local/bin/pip3",
            "/usr/local/bin/pip3.14",
        }
        assert args[boundary + 1 : boundary + 4] == [
            "/usr/local/bin/python",
            "-I",
            "-c",
        ]
        compile(args[boundary + 4], "runtime-installer-assertions", "exec")
        assert runtime.index("apt-get upgrade") < runtime.index(f"RUN rm -rf {package}")

    def test_both_image_proofs_use_entrypoint_and_isolated_venv(self) -> None:
        workflow = yaml.safe_load((_REPO_ROOT / ".github/workflows/ci.yml").read_text())
        for job in ("build-image-and-scan", "aquacast-release-image-proof"):
            step = next(
                step
                for step in workflow["jobs"][job]["steps"]
                if "image release identity" in step.get("name", "")
            )
            command = step["run"]
            assert "tools/image_identity_probe.py" in command
            assert (
                "docker run --rm --network none -e DB_PASSWORD=ci-smoke-only" in command
            )
            assert "--entrypoint" not in command
            assert "include-system-site-packages = false" in command
            assert "sys.prefix == '/app/.venv'" in command
