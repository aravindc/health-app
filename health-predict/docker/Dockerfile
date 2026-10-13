# Prediction API image (research use only — not a medical device).
#   docker build -f docker/Dockerfile -t health-predict .
#   docker run --rm -p 127.0.0.1:8000:8000 -v "$PWD/models:/app/models:ro" health-predict
# In health-app it runs as the health-predict service (root docker-compose.yml).
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.8.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project --extra api

COPY src ./src
COPY config ./config

# Models are mounted at runtime (models/ with registry.json); nothing personal is baked into the image.
RUN useradd --create-home app && mkdir -p models && chown app models   # code stays read-only for the app user
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD \
  python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["/app/.venv/bin/uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
