"""Shared SQL capture for history boundary tests; no query substitutions."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import sqlalchemy as sa


@contextmanager
def capture_statements(bind: sa.Engine | sa.Connection) -> Iterator[list[str]]:
    statements: list[str] = []

    def capture(
        conn: sa.Connection,
        cursor: Any,
        statement: str,
        params: Any,
        context: Any,
        many: bool,
    ) -> None:
        statements.append(statement)

    sa.event.listen(bind, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        sa.event.remove(bind, "before_cursor_execute", capture)


def observation_statements(statements: list[str]) -> list[str]:
    return [statement for statement in statements if "FROM observations" in statement]
