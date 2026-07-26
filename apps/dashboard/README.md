# Streamlit operations dashboard

Run with `uv run streamlit run apps/dashboard/app.py`. The ten pages obtain tenant-scoped documents,
graph and agent runs, tools, extraction quality, validation failures, review activity, Zapier
delivery, audit history, and aggregate metrics only through FastAPI. The one operational action,
dead-letter replay, is an operations-admin FastAPI command. This application contains no PostgreSQL
connection or application-table write path.

The local Compose profile offers Operations Admin and Auditor role selection. The resulting
HttpOnly FastAPI cookie is retained only by Streamlit's server-side HTTP client. A bearer-token
field remains as a fallback when local demo role login is disabled.
