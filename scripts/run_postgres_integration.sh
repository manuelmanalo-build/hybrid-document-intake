#!/usr/bin/env sh
set -eu

: "${TEST_DATABASE_URL:?Set TEST_DATABASE_URL to an explicitly disposable PostgreSQL database}"
uv run pytest -m integration
