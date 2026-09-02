# The management UI's single-page application.
#
# Its own stage so the runtime image never carries a Node toolchain, and so a
# Python-only change does not invalidate it: the frontend layers are cached on
# `frontend/` alone.
FROM node:24-slim AS frontend
WORKDIR /frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
# THE MOUNT POINT IS BAKED IN AT BUILD TIME. Vite writes `base` into every
# asset URL, so this has to be the same value the deployment configures as
# `Settings.ui_root_path`. If the two drift, the page loads and then fetches
# its own assets from somewhere else: a white screen, and nothing in any log
# says why.
#
# A PORTAL PATH. This is an interface a person opens, not a REST backend
# another program calls -- it belongs beside the pass designer under
# `/portale/`, not in the `/api/<domain>/<service>/v<n>` namespace. Under the
# latter the bundle would fetch `/api/wallet/assets/...` and squat on a prefix
# two other services share.
ARG EDUTAP_PASS_BUILDER_UI_ROOT_PATH=/portale/edutap-pass-builder
ENV EDUTAP_PASS_BUILDER_UI_ROOT_PATH=${EDUTAP_PASS_BUILDER_UI_ROOT_PATH}
RUN pnpm build

FROM python:3.14-slim AS build
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml README.md ./
COPY src ./src
# Before the install, so the built interface ends up inside the installed
# package rather than beside it.
COPY --from=frontend /src/edutap/pass_builder/ui/static ./src/edutap/pass_builder/ui/static
RUN uv pip install --system --no-cache .

FROM python:3.14-slim
RUN useradd --create-home --uid 10001 app
COPY --from=build /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY assets /app/assets
COPY migrations /app/migrations
COPY alembic.ini /app/alembic.ini
WORKDIR /app
USER app
EXPOSE 8000
# The render API. The management UI is the same image with a different factory:
#   uvicorn edutap.pass_builder.ui.app:create_ui_app --factory
# One image and two services, because they share a database, a service layer
# and a master key -- see docs/superpowers/specs/2026-09-01-management-ui-design.md.
CMD ["uvicorn", "edutap.pass_builder.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
