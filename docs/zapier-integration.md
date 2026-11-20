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

### Briefs → Jira (built and verified end to end)

| # | Step | Configuration |
|---|---|---|
| 1 | **Webhooks by Zapier → Catch Raw Hook** | Raw, so the `Authorization` header is available. Copy the URL into `INTAKE_ZAPIER_HOOK_URL_CAMPAIGN_BRIEF`. |
| 2 | **Code by Zapier → Run Python** | Paste [`integrations/zapier/jira_code_step.py`](integrations/zapier/jira_code_step.py). Input Data: `body` ← **1. Raw Body**, `auth` ← **1. Headers Http Authorization**, `token` ← the shared token, `expected_type` ← `CAMPAIGN_BRIEF`. Map `body` and `auth` as **pills** from the picker; typed field names are passed as literal text. |
| 3 | **Filter by Zapier** | Continue only if `ok` (Text) exactly matches `true`. |
| 4 | **Storage by Zapier → Get Value** | Key ← `event_label`. Set **"success when nothing is found"** to **Yes**; otherwise every new event stops here. |
| 5 | **Filter by Zapier** | Continue only if **Zap Search Was Found Status** (Boolean) is false. This is the duplicate guard. |
| 6 | **Jira Software Cloud → Create Issue** | Issue type **Epic**; summary, description, labels, due date ← the `epic_*` fields. |
| 7 | **Storage by Zapier → Set Value** | Key ← `event_label`; value ← the epic's **Key** from step 6. It runs before the loop, so a retry after a partial failure cannot create a second epic. |
| 8 | **Looping by Zapier → Create Loop From Text** | Values to loop: `summary` ← `issue_summaries`, `description` ← `issue_descriptions`, `labels` ← `issue_labels`, `due_date` ← `issue_due_dates`, `priority` ← `issue_priorities`. Custom delimiter `~~~`; trim whitespace; maximum 50 iterations. |
| 9 | **Jira Software Cloud → Create Issue** (runs once per loop item) | Issue type **Task**; summary, description, labels, due date, priority ← the **Loop Values** fields (not the Code step's). **Parent** ← the **Key** from step 6, entered through the field's **Custom** value option. Choosing an epic from the dropdown fixes the parent to that one epic, and later runs fail with "parent epic not located". |

Jira's **Find Issue** search offers only summary, key, and fields (no JQL), so it cannot look issues
up by label; Storage by Zapier holds the processed event labels instead. If a run fails after the
epic exists, use **Replay** in Zap History: it resumes from the failed step with the original data,
and the duplicate guard does not interfere.

Set `INTAKE_ZAPIER_HOOK_URL_ROUTED` (to the mock service locally, or to the alerts Zap) so routed
events do not reach this Zap. The Code step would reject them, but each would still use a task.
Each loop iteration is a separate Zapier task: a three-deliverable brief uses about ten tasks.

**Hardening:** keep the token out of the Input Data by storing it in Zapier's secret store and
reading it with `StoreClient` inside the Code step.

### CDP onboarding → Jira and a mapping sheet

The brief Zap with three changes: `expected_type` ← `CDP_ONBOARDING`, step 9 creates **Stories**
(the loop also offers `issue_types`), and the mapping specification is written after the loop with
**Google Sheets → Create Multiple Spreadsheet Rows** from `jira.mapping_rows` (or from a second
Zap, because Zapier cannot nest loops). The payload contains field names and attribute classes
only, never sample values.

### Claims → Jira

Steps 1–5 with `expected_type` ← `CLAIM_PACKET`. The Code step returns `has_epic` = `false` and one
issue, so skip the epic and the loop: one **Create Issue** from the first (only) values of the
`issue_*` fields, then **Set Value** with the new issue's key. Summary `FNOL: insured · vehicle ·
loss date`; priority `High` for bodily injury, SIU, and coverage queues; labels for the queue and
each flag. Large carriers usually integrate their core claims system through its own API; this
recipe fits MGAs, administrators, and the demo.

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
