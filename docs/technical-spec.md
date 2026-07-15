# Technical Specification

## Deployment components

```mermaid
flowchart LR
  U["Angular web"] -->|"HTTPS"| A["FastAPI API"]
  S["Streamlit dashboard"] -->|"FastAPI operations API"| A
  A --> P[("PostgreSQL application tables")]
  A --> O["Immutable object volume / storage port"]
  G["LangGraph orchestrator"] --> P
  G --> C[("AsyncPostgresSaver checkpoints")]
  G --> M["LangChain provider + policy guard"]
  I["Integration worker"] --> P
  I --> Z["Zapier or mock-zapier"]
```

Compose service names are `angular-web`, `fastapi-api`, `langgraph-orchestrator`,
`integration-worker`, `streamlit-dashboard`, `postgres`, and profile-scoped `mock-zapier`.

## Upload transaction

```mermaid
sequenceDiagram
  actor Submitter
  participant Angular
  participant API as FastAPI
  participant Store as Immutable storage
  participant DB as PostgreSQL
  Submitter->>Angular: Select PDF
  Angular->>API: multipart + Idempotency-Key
  API->>Store: Stream, bound, hash, stage
  API->>API: MIME + PDF magic validation
  API->>DB: Begin metadata/audit/run-request transaction
  API->>Store: Create immutable object
  API->>DB: Commit
  API-->>Angular: 201 document + graph_run_id
  Note over API,DB: No graph invocation in this transaction
```

Storage compensation removes only an unreferenced object if database commit fails. Original objects
are never updated or overwritten.

## Human interrupt and resume

```mermaid
sequenceDiagram
  participant Graph as LangGraph orchestrator
  participant DB as PostgreSQL
  participant API as FastAPI
  actor Reviewer
  Graph->>DB: Idempotently create review task + audit
  Graph->>DB: Persist checkpoint at interrupt
  Reviewer->>API: Claim, correct, decide
  API->>DB: Commit reviewed version + decision + resume request
  Note over API,DB: API does not invoke the graph
  Graph->>DB: Lease resume request and resolve persisted thread_id
  Graph->>Graph: Command(resume=validated payload)
  Graph->>DB: Revalidate and checkpoint same thread
```

## Approval and delivery

```mermaid
sequenceDiagram
  participant Graph as Parent graph
  participant DB as PostgreSQL
  participant Worker as Integration worker
  participant Zapier
  Graph->>DB: Verify authoritative APPROVE decision
  Graph->>DB: Atomic approved record + audit + outbox
  Worker->>DB: Lease due outbox row
  Worker->>Zapier: Signed/authenticated POST + idempotency key
  Zapier-->>Worker: 2xx / retryable / permanent response
  Worker->>DB: Delivery ledger + success, retry, or dead letter
```

Delivery is at least once at the HTTP boundary and exactly once at the mock/business-effect boundary
when the receiver honors the supplied idempotency key.

## Versioning and configuration

Graph, policy, prompt, extraction schema, rules, confidence, event payload, and recommendation formats
are versioned. Provider and model are server configuration. Production uses PostgreSQL checkpoints;
`InMemorySaver` is limited to narrow graph unit tests. All images and package versions are pinned.
