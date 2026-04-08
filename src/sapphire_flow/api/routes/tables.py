from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from sapphire_flow.api.deps import get_connection

router = APIRouter(tags=["tables"])

PAGE_SIZE = 50

# Reflected metadata — populated on first request per engine
_reflected: sa.MetaData | None = None


def _get_reflected(conn: sa.Connection) -> sa.MetaData:
    global _reflected  # noqa: PLW0603
    if _reflected is None:
        import geoalchemy2  # noqa: F401  — registers geometry type with SQLAlchemy

        _reflected = sa.MetaData()
        _reflected.reflect(bind=conn)
    return _reflected


def _format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        s = json.dumps(value, default=str)
        return s[:120] + "..." if len(s) > 120 else s
    if isinstance(value, list):
        s = json.dumps(value, default=str)
        return s[:120] + "..." if len(s) > 120 else s
    return str(value)


def _build_select(table: sa.Table) -> list[sa.ColumnElement[Any]]:
    cols: list[sa.ColumnElement[Any]] = []
    for col in table.columns:
        type_str = str(col.type).upper()
        if "GEOMETRY" in type_str or "GEOGRAPHY" in type_str:
            cols.append(sa.func.ST_AsText(col).label(col.name))
        elif isinstance(col.type, sa.LargeBinary):
            cols.append(sa.func.length(col).label(col.name))
        else:
            cols.append(col)
    return cols


@router.get("/tables/", response_class=HTMLResponse)
def table_list(
    request: Request, conn: sa.Connection = Depends(get_connection)
) -> HTMLResponse:
    from sapphire_flow.api import templates

    reflected = _get_reflected(conn)
    tables_info = []
    for name in sorted(reflected.tables.keys()):
        table = reflected.tables[name]
        count = conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
        tables_info.append(
            {
                "name": name,
                "columns": len(table.columns),
                "rows": count,
            }
        )

    return templates.TemplateResponse(
        request,
        "tables/list.html",
        {"tables": tables_info, "active_nav": "tables"},
    )


@router.get("/tables/{table_name}/", response_class=HTMLResponse)
def table_detail(
    request: Request,
    table_name: str,
    page: int = Query(0, ge=0),
    conn: sa.Connection = Depends(get_connection),
) -> HTMLResponse:
    from sapphire_flow.api import templates

    if table_name not in _get_reflected(conn).tables:
        raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")

    table = _get_reflected(conn).tables[table_name]
    total = conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()

    cols = _build_select(table)
    offset = page * PAGE_SIZE
    rows_raw = (
        conn.execute(sa.select(*cols).limit(PAGE_SIZE).offset(offset)).mappings().all()
    )

    column_names = [c.name for c in table.columns]
    rows = [[_format_cell(row[c]) for c in column_names] for row in rows_raw]

    return templates.TemplateResponse(
        request,
        "tables/detail.html",
        {
            "table_name": table_name,
            "columns": column_names,
            "rows": rows,
            "total_rows": total,
            "page": page,
            "offset": offset,
            "row_count": len(rows),
            "has_next": offset + PAGE_SIZE < total,
            "active_nav": "tables",
        },
    )


@router.get("/tables/{table_name}/rows", response_class=HTMLResponse)
def table_rows_partial(
    request: Request,
    table_name: str,
    page: int = Query(0, ge=0),
    conn: sa.Connection = Depends(get_connection),
) -> HTMLResponse:
    from sapphire_flow.api import templates

    if table_name not in _get_reflected(conn).tables:
        raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")

    table = _get_reflected(conn).tables[table_name]
    total = conn.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()

    cols = _build_select(table)
    offset = page * PAGE_SIZE
    rows_raw = (
        conn.execute(sa.select(*cols).limit(PAGE_SIZE).offset(offset)).mappings().all()
    )

    column_names = [c.name for c in table.columns]
    rows = [[_format_cell(row[c]) for c in column_names] for row in rows_raw]

    return templates.TemplateResponse(
        request,
        "tables/_rows.html",
        {
            "table_name": table_name,
            "columns": column_names,
            "rows": rows,
            "total_rows": total,
            "page": page,
            "offset": offset,
            "row_count": len(rows),
            "has_next": offset + PAGE_SIZE < total,
        },
    )
