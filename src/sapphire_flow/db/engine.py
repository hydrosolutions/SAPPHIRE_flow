from __future__ import annotations

import os

import sqlalchemy as sa


def create_engine_from_env() -> sa.Engine:
    url = os.environ["DATABASE_URL"]
    return sa.create_engine(url, pool_pre_ping=True)
