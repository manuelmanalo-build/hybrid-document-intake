# Operations Runbook

## Start and verify

```bash
cp .env.example .env
docker compose --profile local --env-file .env config --quiet
docker compose --profile local --env-file .env up --build --wait
docker compose --profile local --env-file .env ps
```

Health is dependency-driven: PostgreSQL uses `pg_isready`; migrations/bootstrap/checkpoint setup are
one-shot completion dependencies; FastAPI and Streamlit expose readiness endpoints; Angular and
mock Zapier are HTTP-probed; orchestrator/integration containers verify live database access. No
service readiness uses a fixed sleep.

The local profile binds HTTP ports to `127.0.0.1` and enables development role sessions. Select a
role in Angular or Streamlit; no token copy/paste is required. Sessions are process-local, expire
after the configured TTL, and are intentionally cleared by a FastAPI restart. Bearer tokens remain
available for CLI diagnostics. Production must set `INTAKE_DEMO_ROLE_LOGIN_ENABLED=false` and use
the deployment identity provider.

## Common incidents

| Signal | Action |
|---|---|
| API not ready | Check PostgreSQL, migration job, credentials, and storage volume permissions |
| Run request retry/exhausted | Inspect sanitized error and graph version, fix the dependency, then restart the orchestrator; no manual graph-run replay command is exposed in this slice |
| Interrupted case old | Confirm review task ownership and checkpoint thread exists; do not start a new thread |
| Provider unavailable/malformed | Case should be `AGENT_INCOMPLETE` in review; verify provider config without exposing credentials |
| Outbox retry/dead letter | Check server-side hook/auth, delivery ledger status, then replay as operations admin |
| Callback rejected | Verify callback token plus event ID/idempotency key; never log supplied token |
| Demo session lost | Reconnect the role; API restart and TTL expiry intentionally revoke it |

## Dead-letter replay

Replay only after correcting the cause. The command preserves global attempt numbering and adds a
fresh bounded retry window, preventing delivery-ledger ID collisions:

```bash
curl -X POST -H "Authorization: Bearer $INTAKE_DEMO_OPERATIONS_ADMIN_TOKEN" \
  http://localhost:8000/api/v1/integrations/outbox/OUTBOX_UUID/replay
```

## Backup, restore, and shutdown

Back up PostgreSQL application and checkpoint table sets at one recovery point plus the immutable object
volume. During restore, stop orchestrator/integration consumers, restore database and objects,
validate hashes and run/thread mappings, then start consumers. Shut down locally with
`docker compose --profile local down`; add `--volumes` only when intentionally destroying demo data.

## Verification commands

Use `scripts/check.sh`, `scripts/run_postgres_integration.sh`, and
`python -m evaluation.run`. Default verification requires no provider, Zapier, or LangSmith network.
