# Talus engine: FastAPI on :8000, reachable only on the compose network.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tzdata sqlite3 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/venv
COPY engine/pyproject.toml engine/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY engine/talus ./talus
RUN uv sync --frozen --no-dev
ENV PATH=/venv/bin:$PATH TALUS_DATA_DIR=/data
EXPOSE 8000
CMD ["talus", "serve", "--host", "0.0.0.0", "--port", "8000"]
