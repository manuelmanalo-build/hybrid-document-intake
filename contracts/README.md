# Contracts

Contracts are versioned trust-boundary artifacts. The API and application database remain
authoritative; graph state carries only identifiers and bounded status data.

- `document-created-response.v1.schema.json` defines a successful intake response, including the
  durable graph-run request identity.

Agent, extraction, review-resume, operations projection, outbox, and callback payload contracts use
Pydantic as their executable source of truth and are published in FastAPI's generated OpenAPI
document at `/openapi.json`. The Angular and Streamlit clients communicate only through those HTTP
boundaries; Zapier configuration and durable graph/storage internals are not response fields.
