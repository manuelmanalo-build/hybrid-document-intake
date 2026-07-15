#!/usr/bin/env sh
set -eu

fail_if_match() {
  description=$1
  pattern=$2
  shift 2
  if rg -n -i --glob '!release_static_checks.sh' "$pattern" "$@"; then
    echo "release static check failed: $description" >&2
    exit 1
  fi
}

fail_if_match \
  "Camunda, Zeebe, or BPMN runtime references remain in executable/configuration paths" \
  'camunda|zeebe|bpmn' \
  pyproject.toml uv.lock docker-compose.yml services apps contracts infrastructure scripts .github

fail_if_match \
  "unfinished-work markers remain in product source" \
  'TODO|FIXME|XXX' \
  services apps/dashboard evaluation scripts

fail_if_match \
  "Streamlit imports persistence or application-table models" \
  'sqlalchemy|psycopg|asyncpg|intake_api\.domain|intake_api\.infrastructure\.database' \
  apps/dashboard

fail_if_match \
  "orchestrator source contains a direct webhook HTTP boundary" \
  'httpx|requests\.|zapier_hook_url|hooks\.zapier' \
  services/orchestrator/src

fail_if_match \
  "production Python source contains an unbounded no-argument read" \
  '\.read\(\)' \
  services/api/src services/orchestrator/src services/integration-worker/src apps/dashboard

echo "release static checks passed"
