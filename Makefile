.PHONY: install lint reformat test-local test-integration \
	frontend-install frontend-types lint-frontend test-frontend build-frontend

install:
	uv venv
	uv pip install -U -e ".[dev,docs]"

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run ty check

reformat:
	uv run ruff format src tests
	uv run ruff check --fix src tests

test-local:
	uv run pytest -m "not integration"

test-integration:
	uv run pytest -m integration

# --- The management UI's frontend -------------------------------------------
#
# Separate targets rather than folded into `lint` and `test-local`: the Python
# half must stay runnable without a Node toolchain, and a developer touching
# `services/` should not be stopped by a missing pnpm.

frontend-install:
	cd frontend && pnpm install --frozen-lockfile

# Regenerate the typed client from the application's own OpenAPI document.
# Run this after changing a route or a response model -- `lint-frontend` is
# what then reports every call site the change invalidated.
frontend-types:
	cd frontend && node scripts/openapi.mjs && \
		pnpm openapi-typescript openapi.json -o src/api/schema.d.ts

lint-frontend:
	cd frontend && pnpm tsc --noEmit

test-frontend:
	cd frontend && pnpm vitest run

build-frontend:
	cd frontend && pnpm build
