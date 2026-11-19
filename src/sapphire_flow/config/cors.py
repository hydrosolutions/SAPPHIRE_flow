from __future__ import annotations

from urllib.parse import urlsplit

from sapphire_flow.exceptions import ConfigurationError


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
