# TacVector: one container serving the FastAPI API and the built React frontend.
#
#   docker build -t tacvector .
#   docker run --rm -p 8080:8080 -e DEMO_PASSWORD=secret \
#       -v "$PWD/backend:/data" tacvector            # folder holding tacvector.db
#
# The SQLite database is built from licensed GRID data, so it is NEVER copied into the image
# (see .dockerignore). Mount it at runtime; DB_PATH points at it (default /data/tacvector.db).

# ---- 1. Build the React frontend ------------------------------------------------------------
FROM node:24-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. Python runtime ------------------------------------------------------------------------
FROM python:3.14-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DB_PATH=/data/tacvector.db \
    FRONTEND_DIST=/app/frontend/dist

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/*.py ./
COPY --from=frontend /src/frontend/dist /app/frontend/dist

# Run unprivileged. The mounted database must be readable by this user; mount it writable on the
# first run so startup can add indexes / team_rounds (a read-only mount works once they exist).
RUN useradd --system --uid 10001 --no-create-home tacvector && mkdir -p /data && chown tacvector /data
USER tacvector

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status != 200)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
