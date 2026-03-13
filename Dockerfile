FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --frozen --no-dev

COPY src/ src/


FROM python:3.11-slim

RUN groupadd -g 1000 app && useradd -u 1000 -g 1000 -m app

RUN apt-get update && apt-get install -y --no-install-recommends gosu curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "sapphire_flow"]
