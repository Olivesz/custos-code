# Bench execution environment; the scenario runner is not implemented yet.
FROM ghcr.io/astral-sh/uv:0.12.17 AS uv
FROM python:3.12-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md LICENSE ./
RUN uv sync --locked --all-extras --no-install-project
COPY src/ src/
RUN uv sync --locked --all-extras --no-editable
COPY bench/README.md bench/README.md
COPY bench/scenarios/ bench/scenarios/

RUN useradd --create-home --uid 10001 receipts \
    && mkdir /workspace && chown receipts:receipts /workspace
USER receipts
WORKDIR /workspace
CMD ["receipts", "--help"]
