# ADR 0007: Local Demo Role Sessions

- Status: Accepted
- Date: 2026-07-25

## Context

The first-slice Angular and Streamlit applications required operators to copy distinct development
bearer tokens from `.env`. That was error-prone during demonstrations, encouraged credentials in
clipboards, and obscured why controls were disabled. Embedding those permanent tokens in browser
assets or returning them through an API would violate the credential boundary.

## Decision

When `INTAKE_DEMO_ROLE_LOGIN_ENABLED=true`, FastAPI exposes a local-development role-session
endpoint. A user selects a closed `UserRole`; FastAPI creates an opaque random session identifier,
stores only its SHA-256 digest with the role and expiry in process memory, and sets the identifier
as an HttpOnly, SameSite=Lax cookie scoped to `/api`. Logout revokes the digest and expires the
cookie. Role switching revokes the prior session, the active-session count is bounded, and
restarting FastAPI clears all sessions.

Angular uses this cookie through its same-origin `/api` proxy and never reads it. Streamlit retains
the cookie in its own server-side HTTP client session, not browser state. Existing bearer tokens
remain supported for CLI and automated clients.

The role-session router is absent unless explicitly enabled. Configuration validation rejects demo
role sessions and demo bearer credentials in production. Compose enables the role selector for the
local profile and binds browser-facing demo ports to `127.0.0.1`.

## Consequences

Local demonstrations gain one-click role switching without exposing configured tokens. This is not
production authentication: anyone with access to the loopback UI can assume a demo role, sessions
are not shared across API workers, and restart requires reconnection. Production must disable demo
role login and use the planned OIDC/JWT integration.

## Alternatives rejected

- Embedding tokens in Angular configuration: exposes permanent credentials to every browser.
- Returning configured tokens from FastAPI: turns the API into a secret-distribution endpoint.
- Persisting demo sessions in PostgreSQL: adds lifecycle and security complexity to disposable
  local-only authentication.
- Implementing OIDC in this UX increment: expands beyond first-slice local demonstration needs.
