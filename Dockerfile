FROM python:3.13.9-slim

LABEL org.opencontainers.image.title="ContextWiki" \
    org.opencontainers.image.description="Evidence-first MCP retrieval backend" \
    org.opencontainers.image.source="https://github.com/eunaverse/MCPContentSearch"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}" \
    HOME="/home/appuser"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && apt-get clean

RUN pip install --no-cache-dir uv==0.9.5
RUN useradd --create-home --home-dir /home/appuser --shell /bin/bash appuser
RUN mkdir -p /home/appuser/.mcp_content_search

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev

COPY api ./api
COPY core ./core
COPY environments ./environments
COPY fetching ./fetching
COPY indexing ./indexing
COPY search ./search
COPY storage ./storage
COPY scripts ./scripts
COPY sample_vault ./sample_vault
COPY app_runtime.py ./app_runtime.py
COPY main.py ./main.py

RUN chown -R appuser:appuser /app /home/appuser

USER appuser

CMD ["/app/.venv/bin/python", "main.py"]
