# syntax=docker/dockerfile:1.7

# ─── UI builder ───────────────────────────────────────────────────────
# Builds the Vite SPA. vite.config.ts sets outDir to
# ../src/agentic_primitives_gateway/static, so the bundle lands at
# /src/agentic_primitives_gateway/static inside this stage.
FROM node:22-alpine AS ui-builder

WORKDIR /ui

# Copy lockfile first so the `npm ci` layer is cached whenever only
# source files change. package-lock.json is required for `npm ci`.
#
# Do NOT pass `--omit=dev`: vite.config.ts imports `defineConfig` from
# `vitest/config` (the superset type that knows about the `test:` block),
# and vitest is a devDependency. `tsc -b` during the build needs it.
COPY ui/package.json ui/package-lock.json ./
RUN npm ci

COPY ui/ ./
RUN npm run build

# ─── Python builder ───────────────────────────────────────────────────
# Runs after the ui-builder so `static/` is present on disk when the
# wheel is built. Combined with the `artifacts` glob in pyproject.toml,
# the wheel ships the UI — so anyone who installs this wheel outside
# Docker also gets a working UI.
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a /uv /usr/local/bin/uv

ENV UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
# Layer the UI bundle into the source tree before installing so the
# built wheel's RECORD includes static/ (hatchling's `artifacts` glob
# only ships files that exist at build time; missing files are silently
# skipped rather than failing the build).
COPY --from=ui-builder /src/agentic_primitives_gateway/static/ \
     src/agentic_primitives_gateway/static/

RUN uv pip install --system --no-cache .[all]

# ─── Runtime ──────────────────────────────────────────────────────────
FROM python:3.14-slim

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

EXPOSE 8000

CMD ["uvicorn", "agentic_primitives_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
