FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

# The image runs with non-sensitive synthetic data only; real-world/public
# deployment needs authentication, tenant isolation and privacy controls.
COPY pyproject.toml README.md ./
COPY app/ ./app/
COPY ui/ ./ui/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY alembic.ini ./
# No automatic provisioned admin token or provider secret is baked into the image.
RUN python -m pip install --no-cache-dir '.[postgres]'

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
