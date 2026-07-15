# Hybrid Agent Authority Model

## Core rule

The model chooses among approved advisory operations; deterministic code decides whether that
choice is permitted and humans decide approval or rejection. Model output is untrusted input until
Pydantic and policy validation succeeds.

| Actor | May do | Must not do |
|---|---|---|
| Parent graph | Order deterministic stages, invoke bounded subgraph, pause/resume, route validated human decisions | Invent business facts or treat a recommendation as approval |
| Agent model | Select an approved tool, supply arguments, order/repeat within limits, submit an advisory recommendation | Select arbitrary code/nodes, change authority/status/rules, cause external effects |
| Policy guard | Authorize tool, scope, arguments, counters, prerequisites, deadline, idempotency | Infer authorization from model prose |
| Tool adapter | Execute one typed bounded operation through a domain port | Expose arbitrary SQL, URL, filesystem, secrets, or admin APIs |
| Deterministic services | Validate schema/rules, calculate confidence, enforce transitions and idempotency | Delegate authoritative outcomes to a model |
| FastAPI | Authenticate, enforce tenant/role, write review commands and requests | Run or resume the graph inside request transactions |
| Human reviewer/approver | Claim work, correct fields, request correction, approve/reject within role | Bypass validation or cross tenant boundaries |
| Integration worker | Deliver committed outbox events with bounded retry | Derive approval from graph state or model output |

## Enforcement pipeline

1. The model response is parsed into either one typed tool request or the typed completion contract.
2. Unknown/multiple/parallel calls are rejected with safe reason codes.
3. The guard resolves the tool from an immutable registry and validates its Pydantic input.
4. It checks tenant and document against graph state, service identity capability, prerequisites,
   per-tool and global counters, deadline, and stable idempotency key.
5. It appends `TOOL_REQUESTED`; rejection appends `TOOL_REJECTED_BY_POLICY` and returns bounded
   feedback without executing the adapter.
6. The adapter runs with a per-tool timeout and least-privilege port. Its output is Pydantic-
   validated and reduced to a concise checkpoint summary.
7. Completion/failure and duration are audited. Detailed data is stored in application records and
   referenced by ID.

Neither prompt wording nor LangChain tool binding replaces this guard.

## Bounded autonomy

The state tracks total calls, extraction attempts, supplier lookups, PO lookups, started time, and
deadline. Initial limits are 10/3/2/2 respectively, with no parallel calls. The guard also enforces
tool prerequisites: for example, extraction requires inspection/classification evidence and retry
requires a prior failed or insufficient extraction.

Provider unavailable, model timeout, malformed structured output, unauthorized request, invalid
tool result, total timeout, or exhausted limit terminates the subgraph with `AGENT_INCOMPLETE` plus
reason codes. The parent continues independent validation and review creation.

## Human authority

Agent dispositions are advice only. `REJECT_UNSUPPORTED` means “present this rationale to a human,”
not “reject.” Only a validated application review command by an authorized actor can set
`REQUEST_CORRECTION`, `REJECT`, or `APPROVE`. A reviewer can correct fields; approval requires the
configured `APPROVER` capability. Every correction and decision is versioned and audited.

After resume, the parent applies corrections and runs schema/business rules again. The gateway reads
the stored, validated human decision and current record version. It does not read model prose.

## Data minimization

No private reasoning is requested or stored. Rationale is a short user-facing summary plus reason
codes. Evidence excerpts are normalized, length-bounded, and associated with page/field metadata.
Tool arguments and results omit credentials, raw documents, unrestricted paths, full provider
payloads, and sensitive reference records. Provider/model/version and available token usage may be
stored for cost and quality analysis.

## Threat responses

- Prompt injection in a document cannot enlarge the tool registry or policy permissions.
- Cross-tenant IDs fail both guard checks and tenant-aware repository predicates.
- Replayed nodes encounter existing idempotency records rather than duplicate writes.
- Forged browser decisions fail authentication, role, task ownership, tenant, payload, and version
  checks before a resume request exists.
- Checkpoint or trace access cannot substitute for the application audit ledger.
- A compromised model credential can call only the configured provider, not database, storage,
  graph administration, or integration endpoints.
