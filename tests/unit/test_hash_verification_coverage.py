"""Regression test ensuring all deserialize_artifact() call sites in operational
flows are preceded by SHA-256 hash verification. Prevents future deserialization
paths from silently bypassing integrity checks."""

import ast
import textwrap
from pathlib import Path

# Directories where deserialize_artifact() appearances are NOT operational call
# sites requiring a hash guard:
#   protocols/ — Protocol method definitions
#   models/    — model class method implementations (the serialize/deserialize body)
#   adapters/  — adapter method implementations that delegate model deserialization
#   services/  — smoke_test_model() does a pure serialize→deserialize roundtrip;
#                bytes are never fetched from storage, so hash guard is N/A
#   store/     — fetch_artifact() implementations handle hash verification internally
_SKIP_DIRS = {"protocols", "models", "adapters", "services", "store"}

_SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "sapphire_flow"

_SHA_KEYWORDS = {"sha256", "hashlib"}


def _relative_to_src(path: Path) -> str:
    return str(path.relative_to(_SRC_ROOT.parent.parent))


def _containing_function_lines(tree: ast.Module, target_lineno: int) -> list[str]:
    """Return the source lines of the function body that contains target_lineno."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body_start = node.body[0].lineno
        body_end = node.end_lineno or node.lineno
        if body_start <= target_lineno <= body_end:
            return list(range(body_start, target_lineno))
    return []


def _has_sha_guard_before(
    source_lines: list[str], call_lineno: int, func_start_lineno: int
) -> bool:
    """Return True if any line in [func_start, call_lineno) has a sha256 keyword."""
    # source_lines is 0-indexed; linenos are 1-indexed
    for lineno in range(func_start_lineno - 1, call_lineno - 1):
        if lineno < len(source_lines):
            line = source_lines[lineno]
            if any(kw in line for kw in _SHA_KEYWORDS):
                return True
    return False


def _find_function_start(tree: ast.Module, target_lineno: int) -> int | None:
    """Return the first line of the function body containing target_lineno."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body_start = node.body[0].lineno
        body_end = node.end_lineno or node.lineno
        if body_start <= target_lineno <= body_end:
            return body_start
    return None


class TestHashVerificationCoverage:
    def test_all_flow_deserialize_calls_have_sha_guard(self) -> None:
        """Every deserialize_artifact() call in src/sapphire_flow/flows/ must be
        preceded by a hashlib.sha256 reference within the same function body."""
        unguarded: list[str] = []

        for py_file in sorted(_SRC_ROOT.rglob("*.py")):
            # Skip non-call-site directories
            rel_parts = py_file.relative_to(_SRC_ROOT).parts
            if rel_parts[0] in _SKIP_DIRS:
                continue

            source = py_file.read_text(encoding="utf-8")
            if "deserialize_artifact" not in source:
                continue

            source_lines = source.splitlines()
            tree = ast.parse(source, filename=str(py_file))

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # Match obj.deserialize_artifact(...)
                func = node.func
                is_deser = (
                    isinstance(func, ast.Attribute)
                    and func.attr == "deserialize_artifact"
                )
                if not is_deser:
                    continue

                call_lineno = node.lineno
                func_start = _find_function_start(tree, call_lineno)

                if func_start is None:
                    # Module-level call — flag it
                    unguarded.append(
                        f"{_relative_to_src(py_file)}:{call_lineno} "
                        f"(module-level, no enclosing function)"
                    )
                    continue

                if not _has_sha_guard_before(source_lines, call_lineno, func_start):
                    unguarded.append(
                        f"{_relative_to_src(py_file)}:{call_lineno} "
                        f"(no sha256/hashlib reference before call in function body)"
                    )

        assert not unguarded, (
            "Found deserialize_artifact() call sites without a preceding SHA-256 "
            "hash verification guard:\n"
            + textwrap.indent("\n".join(unguarded), "  ")
            + "\n\nEach call site in flows/ must have a hashlib.sha256(...) check "
            "before calling deserialize_artifact()."
        )
