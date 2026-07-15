#!/usr/bin/env sh
set -eu

uv sync --frozen
sh scripts/release_static_checks.sh
uv run ruff format --check services apps/dashboard evaluation scripts
uv run ruff check services apps/dashboard evaluation scripts
uv run pyright
uv run pytest -m "not integration"
uv run python -m evaluation.run
pnpm install --frozen-lockfile
pnpm --dir apps/web format:check
pnpm --dir apps/web lint
pnpm --dir apps/web test
pnpm --dir apps/web build
docker compose --profile local --env-file .env config --quiet
