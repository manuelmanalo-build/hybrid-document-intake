# Claims demo script

A five-minute walkthrough of the claims slice using the 20 synthetic packets in
`test-data/claims/packets`. Use a live provider (for example `AGENT_PROVIDER=anthropic` with
`ANTHROPIC_API_KEY` and `AGENT_MODEL` in `.env`): with the stub provider every packet extracts as
fixture C01, so all but the first route to duplicate review.

## Setup

1. `docker compose --profile local --env-file .env up --build --wait`. The local profile loads the
   claims policy and vehicle reference data automatically.
2. Open the web app and connect with the local demo role selector.

## Walkthrough

1. **Claims intake (Submitter).** Choose all 20 packets at once. Each file uploads with its own
   idempotency key and shows six steps: uploaded, classified, pages labeled, extracted, rules
   checked, ready for review. Upload one packet twice to show the identical-file flag.
2. **Claim queues (Reviewer or Approver).** The summary strip shows claims past, near, and within
   their service level; the queue buttons filter by queue. Every row says why the claim is there in
   plain language.
3. **C10, the hidden injury.** Open the Danielle Okafor claim in the bodily injury queue. The
   packet opens on the email page; the two injury fields are outlined because the loss notice says
   "no injury" while the email describes whiplash. "Why this queue" shows CR-009 and the
   third-party rule it outranked.
4. **Decide.** Claim the task. As an approver, approve the intake, or edit a field and submit the
   correction to show the claim being re-checked and re-routed.
5. **Re-route.** On another claim, choose a different queue and give a reason; the queue screen
   then shows "Re-routed by reviewer", and the audit trail records a `CLAIM_REROUTED` event.
6. **C09 and C14.** Show the VIN typo (VIN fields outlined, desk adjuster) and the email-only
   duplicate of C02 (duplicate review).

## Notes

- The packet viewer uses the browser's built-in PDF viewer with `#page=` navigation. Chrome, Edge,
  and Firefox honor page jumps; Safari may always open at page 1.
- Service-level targets come from `INTAKE_CLAIMS_SLA_*` settings and are measured from upload to the
  first time a reviewer claims the task.

## Campaign briefs (agency)

The same screens handle the 20 synthetic briefs in `test-data/briefs/packets` for the fictional
agency Meridian & Pike.

1. **Intake.** Upload the brief packets alongside or instead of the claim packets; each is classified
   and routed on its own.
2. **Work queues → Briefs.** The queues are ready for planning, needs client info, scope review,
   legal review, rush review, traffic review, and duplicate review.
3. **B12, the moved launch.** In rush review, open Cold Brew New Year. The packet opens on the
   follow-up email; the two launch-date fields are outlined because the brief says January 5 and the
   email says November 30, which leaves too little production time for the video.
4. **B05 and B06, change orders.** In scope review: a budget over the SOW's remaining fees, and an
   out-of-home request the SOW does not cover.
5. **B09, legal.** A pharma brief with label claims goes to legal review.
6. **Correct and re-check.** On a ready brief, correct the budget above the SOW's remaining fees and
   submit; the brief is re-checked and moves to scope review.
7. **B03, ask the client.** In needs client info, open Holiday Smart Gifting. The "Questions for the
   client" panel drafts one question because the KPI target has no number. Claim the task, edit the
   message, copy it into an email, and record it as sent.
8. **Approve and hand off.** Approve a ready brief; its outbox event carries one task per
   deliverable for the work-management tool (visible in the mock Zapier service locally).

