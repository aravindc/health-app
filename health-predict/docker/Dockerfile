# Prediction API image (research use only — not a medical device).
#   docker build -f docker/Dockerfile -t health-predict .
#   docker run --rm -p 127.0.0.1:8000:8000 -v "$PWD/models:/app/models:ro" health-predict
# In health-app it runs as the health-predict service (root docker-compose.yml).

# Build stage: uv installs the locked serving dependencies (the base set plus
# the api extra, not the training pipeline's) into /app/.venv. uv and its
# download cache stay in this stage.
FROM docker.io/library/python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.8.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project --extra api

# Runtime: the same Python base (the venv links to its interpreter), the venv,
# and the code.
FROM docker.io/library/python:3.12-slim
ENV PYTHONUNBUFFERED=1 PATH=/app/.venv/bin:$PATH
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src ./src
COPY config ./config

# Models are mounted at runtime (models/ with registry.json); nothing personal is baked into the image.
RUN useradd --create-home app && mkdir -p models && chown app models   # code stays read-only for the app user
USER app
EXPOSE 8000
# The healthcheck is in health-app's docker-compose.yml, not here: Podman
# builds OCI-format images by default, which drop HEALTHCHECK.
CMD ["/app/.venv/bin/uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
