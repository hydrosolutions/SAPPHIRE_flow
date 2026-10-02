from __future__ import annotations

import json
import os
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003

IDENTITY_KEYS = ("VERSION", "SAPPHIRE_RELEASE_VERSION", "SAPPHIRE_SOURCE_REVISION")
_BINDING_RE = re.compile(rb"^([ \t]*)([A-Za-z_][A-Za-z0-9_]*)([ \t]*=[ \t]*)")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_CANONICAL_RELEASE_RE = re.compile(r"^[0-9]+[.][0-9]+[.][0-9]+(?:[.][0-9]+)?$")


@dataclass(frozen=True, slots=True)
class EnvBinding:
    key: str
    value: bytes
    line_index: int
    quoted: bool
    multiline: bool
    start: int
    end: int
    value_start: int
    value_end: int


class DotenvIdentityError(ValueError):
    pass


def _find_closing_quote(content: bytes, start: int, quote: int) -> int | None:
    escaped = False
    for pos in range(start + 1, len(content)):
        byte = content[pos]
        if quote == ord('"') and escaped:
            escaped = False
            continue
        if quote == ord('"') and byte == ord("\\"):
            escaped = True
            continue
        if byte == quote:
            return pos
    return None


def _line_end(content: bytes, start: int) -> int:
    newline = content.find(b"\n", start)
    return len(content) if newline == -1 else newline + 1


def _contains_unsupported_escape(value: bytes) -> bool:
    return b"\\" in value


def parse_env_bindings(content: bytes) -> dict[str, EnvBinding]:
    bindings: dict[str, EnvBinding] = {}
    offset = 0
    line_index = 0
    while offset < len(content):
        line_end = _line_end(content, offset)
        line = content[offset:line_end].rstrip(b"\r\n")
        stripped = line.lstrip()
        leading = len(line) - len(stripped)
        if not stripped or stripped.startswith(b"#"):
            offset = line_end
            line_index += 1
            continue
        match = _BINDING_RE.match(line)
        if match is None:
            raise DotenvIdentityError(
                f".env unsupported noncomment record on line {line_index + 1}"
            )
        raw_key = match.group(2)
        key = raw_key.decode("utf-8", "strict")
        value_start = offset + match.end(3)
        first_value = content[value_start : offset + len(line)]
        quoted = first_value.startswith((b"'", b'"'))
        multiline = False
        binding_end = line_end
        value_end = offset + len(line)
        if first_value.startswith(b"'"):
            raise DotenvIdentityError(
                f".env unsupported quoted value on line {line_index + 1}"
            )
        if quoted:
            quote = first_value[0]
            if _contains_unsupported_escape(first_value):
                raise DotenvIdentityError(
                    f".env unsupported escape in quoted value on line {line_index + 1}"
                )
            close = _find_closing_quote(content, value_start, quote)
            if close is None:
                raise DotenvIdentityError(
                    ".env has unterminated quoted value starting on line "
                    f"{line_index + 1}"
                )
            value_end = close + 1
            binding_end = _line_end(content, close + 1)
            multiline = b"\n" in content[value_start:value_end]
        if key in IDENTITY_KEYS and key in bindings:
            raise DotenvIdentityError(f".env has duplicate key {key}")
        if key not in bindings:
            bindings[key] = EnvBinding(
                key=key,
                value=content[value_start:value_end],
                line_index=line_index,
                quoted=quoted,
                multiline=multiline,
                start=offset + leading,
                end=binding_end,
                value_start=value_start,
                value_end=value_end,
            )
        line_index += content[offset:binding_end].count(b"\n")
        offset = binding_end
    return bindings


def public_identity_snapshot(content: bytes) -> dict[str, dict[str, str | bool]]:
    bindings = parse_env_bindings(content)
    result: dict[str, dict[str, str | bool]] = {}
    for key in IDENTITY_KEYS:
        binding = bindings.get(key)
        if binding is None:
            result[key] = {"present": False}
            continue
        if binding.quoted or binding.multiline:
            raise DotenvIdentityError(
                f".env release identity key {key} must be unquoted"
            )
        try:
            value = binding.value.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise DotenvIdentityError(
                f".env release identity key {key} is not UTF-8"
            ) from exc
        result[key] = {"present": True, "value": value}
    return result


def validate_identity_snapshot(
    snapshot: dict[str, dict[str, str | bool]],
) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in IDENTITY_KEYS:
        item = snapshot.get(key)
        if item is None or item.get("present") is not True:
            raise DotenvIdentityError(
                f".env missing required release identity key {key}"
            )
        value = item.get("value")
        if not isinstance(value, str) or not value:
            raise DotenvIdentityError(f".env release identity key {key} is empty")
        if value.strip() != value or "\n" in value or "\r" in value:
            raise DotenvIdentityError(f".env release identity key {key} is malformed")
        values[key] = value
    if not _SHA_RE.fullmatch(values["SAPPHIRE_SOURCE_REVISION"]):
        raise DotenvIdentityError(
            ".env SAPPHIRE_SOURCE_REVISION must be a full 40-hex SHA"
        )
    if (
        _CANONICAL_RELEASE_RE.fullmatch(values["SAPPHIRE_RELEASE_VERSION"])
        and values["VERSION"] != values["SAPPHIRE_RELEASE_VERSION"]
    ):
        raise DotenvIdentityError(".env VERSION must match SAPPHIRE_RELEASE_VERSION")
    return values


def read_identity(
    path: Path, *, require_present: bool
) -> dict[str, str] | dict[str, dict[str, str | bool]]:
    content = path.read_bytes() if path.exists() else b""
    snapshot = public_identity_snapshot(content)
    if require_present:
        return validate_identity_snapshot(snapshot)
    return snapshot


def _replace_identity_bytes(original: bytes, values: dict[str, str]) -> bytes:
    bindings = parse_env_bindings(original)
    chunks: list[bytes] = []
    cursor = 0
    seen: set[str] = set()
    for binding in sorted(bindings.values(), key=lambda item: item.value_start):
        if binding.key not in values:
            continue
        if binding.quoted or binding.multiline:
            raise DotenvIdentityError(
                f".env release identity key {binding.key} must be unquoted"
            )
        chunks.append(original[cursor : binding.value_start])
        chunks.append(values[binding.key].encode())
        cursor = binding.value_end
        seen.add(binding.key)
    chunks.append(original[cursor:])
    updated = b"".join(chunks)
    newline = b"\n"
    if original:
        last_crlf = original.rfind(b"\r\n")
        last_lf = original.rfind(b"\n")
        if last_crlf != -1 and last_crlf == last_lf - 1:
            newline = b"\r\n"
    if original and not updated.endswith((b"\n", b"\r\n")):
        updated += newline
    for key in IDENTITY_KEYS:
        if key not in seen:
            updated += f"{key}={values[key]}".encode() + newline
    return updated


def write_identity_atomic(path: Path, values: dict[str, str]) -> None:
    validate_identity_snapshot(
        {key: {"present": True, "value": value} for key, value in values.items()}
    )
    original = path.read_bytes() if path.exists() else b""
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    updated = _replace_identity_bytes(original, values)
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    fd = -1
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        if fd != -1:
            os.close(fd)
        with suppress(FileNotFoundError):
            tmp_path.unlink()
        raise


def write_json_once(path: Path, payload: object) -> None:
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text() != content:
            raise DotenvIdentityError(f"existing rollback evidence differs: {path}")
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
