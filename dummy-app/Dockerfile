# Stage 1: build the two React bundles (store and operator). Node stays in this stage only.
FROM node:24.22.1-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS web
WORKDIR /web
COPY web/package.json web/package-lock.json web/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# Fails the build if the types or the generated API types are stale.
RUN npm run typecheck && npm run check:api && npm run build

# Stage 2: the Python runtime that every service runs from.
FROM python:3.12.8-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY --chown=app:app services services
COPY --chown=app:app controllers controllers
COPY --chown=app:app scripts scripts
# Built bundles only. Root owns the files, so the app user cannot edit them in place.
# The Gateway serves dist/store and the controller dist/operator at /ui/next/.
COPY --from=web /web/dist services/ui/dist
USER app
CMD ["python", "-m", "services.run"]
