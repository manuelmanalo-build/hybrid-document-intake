# Zapier integration: Jira and QuickBooks

The platform never calls Jira or QuickBooks directly. Approved documents and queue assignments go
to the transactional outbox; the integration worker delivers each event to a Zapier catch hook with
retries, backoff, a dead-letter queue, and an `Idempotency-Key` header. Zaps do the rest. Sample
payloads for every event live in [`integrations/samples`](integrations/samples); paste one into a
Zap's test step. They are produced by the same builders the platform uses
(`intake_api.application.integration_payloads`) and are regenerated with
`uv run python -m scripts.generate_zapier_samples`.

## Events

| Event | When | Destination block |
|---|---|---|
| `DOCUMENT_APPROVED` (invoice) | An approver approves an invoice | `accounting_bill` (`quickbooks-bill-v1`) |
| `DOCUMENT_APPROVED` (claim) | An approver approves a claim intake | `claim` summary and `jira` (one issue) |
| `DOCUMENT_APPROVED` (brief) | An approver approves a work request | `work_request` and `jira` (epic plus one issue per deliverable) |
| `DOCUMENT_APPROVED` (CDP) | An approver approves an onboarding plan | `onboarding_plan` and `jira` (epic, one story per workstream, `mapping_rows`) |
| `DOCUMENT_ROUTED` | A claim, brief, or onboarding request enters a queue (rules or a reviewer re-route) | `alert` (title and text ready to post), `service_level`, `summary` |

Every approval payload also carries `document_type` and `event_key`. Invoice payloads are otherwise
unchanged, so existing invoice Zaps keep working.

## Configuration

| Variable | Purpose |
|---|---|
| `INTAKE_ZAPIER_HOOK_URL` | Default catch hook for every event |
| `INTAKE_ZAPIER_HOOK_URL_AP_INVOICE` | Optional: invoice approvals (QuickBooks Zap) |
| `INTAKE_ZAPIER_HOOK_URL_CLAIM_PACKET` | Optional: claim approvals (Jira claims project) |
| `INTAKE_ZAPIER_HOOK_URL_CAMPAIGN_BRIEF` | Optional: brief approvals (Jira campaign project) |
| `INTAKE_ZAPIER_HOOK_URL_CDP_ONBOARDING` | Optional: onboarding approvals (Jira data project) |
| `INTAKE_ZAPIER_HOOK_URL_ROUTED` | Optional: `DOCUMENT_ROUTED` queue alerts (Slack Zap) |
| `INTAKE_ZAPIER_AUTH_TOKEN` | Sent as `Authorization: Bearer …`; the Zap's first step stops when it does not match |
| `INTAKE_ZAPIER_SIGNING_SECRET` | HMAC-SHA256 of the body in `X-Webhook-Signature`, for consumers that can verify it |

Routed events go to the routed hook when set; otherwise each event goes to its type's hook, then to
the default. Treat every hook URL as a secret: catch-hook URLs accept anyone who has them.

## Zap recipes

App and action names below are as they appear in Zapier's editor at the time of writing; check them
when you build the Zap.

### Briefs → Jira (build first)

1. **Webhooks by Zapier → Catch Raw Hook** (raw, so the `Authorization` header is visible).
2. **Filter**: continue only if the header matches your token and `document_type` is
   `CAMPAIGN_BRIEF`.
3. **Jira Software Cloud → Find Issue** by the label in `jira.epic.labels` that starts with
   `intake-`. If found, stop: this event was already processed.
4. **Jira → Create Issue**: issue type `jira.epic.issue_type` (Epic), summary, description, labels,
   and due date from `jira.epic`.
5. **Looping by Zapier → Create Loop From Line Items** over `jira.issues`.
6. **Jira → Create Issue** inside the loop: summary, description, labels, priority, due date, and
   the epic from step 4 as parent.

Each loop iteration is a separate Zapier task: a three-deliverable brief uses about six tasks.

### CDP onboarding → Jira and a mapping sheet

Same as briefs, with stories instead of tasks. Zapier cannot nest loops, so write the mapping
specification with one bulk step: **Google Sheets → Create Multiple Spreadsheet Rows** from
`jira.mapping_rows`, or a second Zap. The payload contains field names and attribute classes only,
never sample values.

### Claims → Jira

`jira.epic` is null and `jira.issues` has one issue: summary `FNOL: insured · vehicle · loss date`,
priority `High` for bodily injury, SIU, and coverage queues, and labels for the queue and each flag.
No loop is needed. Large carriers usually integrate their core claims system through its own API;
this recipe fits MGAs, administrators, and the demo.

### Invoices → QuickBooks Online

1. Catch Raw Hook and the same token filter; continue only if `document_type` is `AP_INVOICE` and
   `accounting_bill.lines_match_total` is true (otherwise send it to an AP Slack channel instead).
2. **QuickBooks Online → Find Vendor** by `accounting_bill.vendor_name` (create it if missing, or
   stop and alert).
3. **QuickBooks Online → Create Bill**: vendor, `doc_number` as the bill number (QuickBooks then
   rejects a duplicate bill number for the same vendor), `txn_date`, `due_date`, memo, and the
   `lines` (map them to your expense account).

### Queue alerts → Slack

Catch hook on `INTAKE_ZAPIER_HOOK_URL_ROUTED` → **Paths by Zapier** on `queue` → **Slack → Send
Channel Message** with `alert.text`. For example: bodily injury and SIU to the claims desk, privacy
review to the privacy team, rush review to traffic.

## Guarantees and limits

- The outbox delivers each event at least once. A retry after a timeout can reach Zapier twice;
  the `intake-…` label search (Jira) and the bill number (QuickBooks) make the Zaps idempotent.
- Approval events are written in the same transaction as the approval; routed events in the same
  transaction as the review task or re-route. Neither is lost if a service restarts.
- The model never triggers an event. Routing comes from the deterministic rules or a reviewer, and
  approval from a person.
