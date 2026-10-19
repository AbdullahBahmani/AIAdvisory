# syntax=docker/dockerfile:1
#
# Local demo image for the AI Opportunity Advisor.
#
# Scope and posture:
#   * Third-party dependencies are installed from the committed uv.lock
#     (uv sync --locked), so the image is reproducible and matches the
#     dependency lock that the test suite runs against.
#   * The application source is copied into /app and imported from there
#     (PYTHONPATH=/app); it is deliberately NOT installed a second time
#     into site-packages, so there is exactly one copy of the code.
#   * The container runs as an unprivileged, non-root user (uid 10001).
#   * No API key, reviewer token, database credential or any other secret is
#     baked into the image. Every secret is supplied at run time.
#   * `ADVISORY_DEPLOYMENT_MODE=production` still refuses to start; this
#     image is not, and cannot become, a production deployment.
#
# Build:  docker compose build
# Run:    docker compose up -d
ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.12.1

# ---------------------------------------------------------------------------
# Builder: resolve and install the locked dependency set only.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder
ARG UV_VERSION
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
COPY pyproject.toml uv.lock .python-version README.md ./
# --locked fails the build when pyproject.toml and uv.lock disagree, instead
# of silently resolving different versions than the tested lock file.
# --no-install-project keeps the application source out of site-packages.
RUN uv sync --locked --no-dev --extra postgres --no-install-project

# ---------------------------------------------------------------------------
# Runtime: application source + virtualenv, no build toolchain, non-root.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime
ARG APP_UID=10001
ARG APP_GID=10001
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/venv/bin:${PATH} \
    PYTHONPATH=/app \
    HOME=/home/appuser
RUN groupadd --gid ${APP_GID} appuser \
    && useradd --uid ${APP_UID} --gid ${APP_GID} --create-home \
        --home-dir /home/appuser --shell /usr/sbin/nologin appuser
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY app/ ./app/
COPY ui/ ./ui/
# scripts/admin.py and scripts/maintenance.py are the operator CLIs; the
# offline demo script stays out of the image (it needs the dev extra).
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY alembic.ini ./
# /data holds the local SQLite database and is the volume mount point.
RUN mkdir -p /data && chown -R appuser:appuser /app /data
USER appuser
EXPOSE 8000
# Liveness/readiness probe for the API role; the Streamlit role overrides it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3).status == 200 else 1)"]
# Alembic migrations are applied before the API accepts traffic; `exec` keeps
# uvicorn as PID 1 so SIGTERM is handled for a clean container shutdown.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
