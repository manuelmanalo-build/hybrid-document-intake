# Local Infrastructure

The Compose topology provides pinned PostgreSQL, migration/bootstrap jobs, FastAPI, Angular,
Streamlit, the LangGraph orchestrator, and the Zapier integration worker. It requires an explicit
`.env`; placeholders must be replaced outside an isolated local demo.

Angular proxies `/api/` to FastAPI. Streamlit uses only `DASHBOARD_API_BASE_URL`. The orchestrator and
integration worker are separately deployable processes using PostgreSQL leases; the Zapier hook and
its optional authentication/signing secrets remain server-side. No empty placeholder services are
included.

Use `docker compose --profile local --env-file .env up --build --wait`. The local/test profiles add
the deterministic `mock-zapier` Catch Hook. Every long-running service has a health check; startup
dependencies use PostgreSQL/HTTP readiness or successful migration jobs rather than fixed sleeps.
