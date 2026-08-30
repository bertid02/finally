# FinAlly — single image, single port. Node builds the frontend, Python serves
# everything: /api/* from FastAPI and the static export from /app/static.
#
# Layer order in both stages is "manifests, install, then source", so editing a
# component or a route rebuilds the last layer only and leaves the dependency
# layers in cache. A new Python dependency (litellm) means pyproject.toml and
# uv.lock change, which correctly invalidates the install layer and nothing else.

# ---------------------------------------------------------------------------
# Stage 1 — build the Next.js static export
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend

WORKDIR /build

# Manifests first: `npm ci` re-runs only when the lockfile actually changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# `output: 'export'` writes a complete static site to /build/out. There is no
# Node server in the final image. frontend/.env.production pins
# NEXT_PUBLIC_USE_MOCK_API=false, so the image always talks to the real API.
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — Python runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN pip install --no-cache-dir uv==0.5.31

WORKDIR /app

# Dependency manifests only. --no-install-project means the venv is built from
# the lockfile alone, so it survives every subsequent source edit; the app
# package is imported from the working directory rather than installed.
# --locked fails loudly if uv.lock is out of date with pyproject.toml, which is
# the failure you want when a dependency was added without relocking.
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --locked --no-dev --no-install-project

# Application source and the built frontend. Everything below this line is cheap.
COPY backend/app ./app
COPY --from=frontend /build/out ./static

# Non-root. /app/db is created and owned here so the named volume Docker mounts
# over it inherits that ownership on first use — otherwise the volume lands
# root-owned and SQLite cannot create its journal.
RUN useradd --create-home --uid 10001 finally \
    && mkdir -p /app/db \
    && chown -R finally:finally /app
USER finally

ENV PATH="/app/.venv/bin:${PATH}" \
    FINALLY_DB_PATH=/app/db/finally.db \
    FINALLY_STATIC_DIR=/app/static

EXPOSE 8000

# urllib rather than curl: python is already here, curl is not in -slim.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request as u, sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
