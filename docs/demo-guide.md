# Demo Guide

## Preparation

1. Copy `.env.example` to `.env` and replace all demo tokens/passwords. Leave
   `INTAKE_DEMO_ROLE_LOGIN_ENABLED=true` for one-click local role selection.
2. Leave `AGENT_PROVIDER=stub`, `LANGSMITH_TRACING=false`, and
   `EVALUATION_LANGSMITH_ENABLED=false` for a deterministic offline demo.
3. Run `docker compose --profile local --env-file .env up --build --wait`.
4. Verify Angular `http://localhost:4200`, API `/health/ready`, Streamlit
   `http://localhost:8501`, and mock Zapier `http://localhost:8090/health/ready`.

## Story

1. In Angular, select the Submitter role, connect, and upload
   `test-data/evaluation/valid-invoice.pdf`. Point out upload progress and retained idempotency key.
2. Show document stage and that the protected PDF arrives through FastAPI, not a storage path.
3. In Streamlit, select Auditor and show graph/agent/tool activity, extraction quality, rules,
   confidence, and the interrupted count.
4. In Angular, switch to Reviewer, claim the task, inspect bounded evidence, correct one field with
   a reason, and request correction if demonstrating the loop.
5. Switch to Approver, claim the refreshed/open task, and approve. Emphasize that the agent
   recommendation is labeled advisory and cannot expose an approve action.
6. Show the completed graph, approved record/outbox delivery, and mock Zapier `/deliveries` count.
   Re-sending the same idempotency key returns `duplicate: true` without a second business effect.
7. Open the audit timeline and reconstruct intake, agent, authoritative validation, review, resume,
   approval, outbox, and delivery.

## Failure demonstrations

- Set `MOCK_ZAPIER_FAILURES_BEFORE_SUCCESS=2` and restart mock/worker to show retry visibility.
- Set `MOCK_ZAPIER_RESPONSE_STATUS=500` to reach dead letter, correct it, then replay through the
  Streamlit operations command.
- Use `malformed.pdf` to show upload rejection and sanitized Problem Details.
- Use `low-confidence-invoice.pdf`, `inactive-supplier.pdf`, or `purchase-order-overage.pdf` to
  explain deterministic rules and mandatory human authority.

Never use real invoices, credentials, supplier information, or a production Zapier URL in a demo.
