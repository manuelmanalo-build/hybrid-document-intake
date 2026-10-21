# ADR 0001: LangChain Model and Tool Framework

- Status: Accepted
- Date: 2026-07-14

## Context

The bounded resolution agent must support OpenAI and Gemini, validated structured responses, and a
small typed tool catalog without coupling business authority to one provider. Ordinary tests cannot
depend on network access or model behavior.

## Decision

Use LangChain provider integrations, prompt composition, structured output, and typed tools. Select
the provider/model from server-side configuration and expose a common application model port. Tool
inputs and outputs are Pydantic models with closed enums and `extra="forbid"`.

Bind only the approved catalog, then pass every requested call through a custom policy guard before
adapter execution. LangChain binding is discoverability, not authorization. The application owns
timeouts, counters, idempotency, audit, output validation, safe error mapping, and the final
recommendation contract. A deterministic scripted model implements the same port for default tests.

Persist provider/model IDs, prompt/schema/policy versions, available usage, reason codes, and concise
rationale. Do not request or store private chain-of-thought or raw provider payloads.

## Consequences

Provider changes are localized and model output remains untrusted. We accept an adapter layer and
provider capability tests. Provider-specific structured-output differences must be normalized and
malformed/unavailable responses degrade to `AGENT_INCOMPLETE`, never an authoritative decision.

## Alternatives rejected

- Direct provider SDKs: duplicate tool/structured-output orchestration and increase switching cost.
- A general-purpose autonomous agent: exposes more authority and less predictable control.
- Live models in unit tests: non-deterministic, slow, costly, and credential-dependent.

## Amendment (2026-10-21)

An Anthropic Claude adapter (`AGENT_PROVIDER=anthropic`, `langchain-anthropic`) was added behind the
same provider-neutral port. It sets an explicit output-token budget, disables client retries like
the other adapters, and maps rate-limit, timeout, connection, and overload errors to the existing
transient and unavailable categories. No graph, tool, or authority behavior changed.

