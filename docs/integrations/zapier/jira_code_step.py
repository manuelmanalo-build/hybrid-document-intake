# Code by Zapier (Run Python) step for the Jira Zaps: verify the token, accept one document
# type, and flatten the payload's `jira` block for Looping by Zapier ("Create Loop From Text").
#
# Input Data (map each value as a pill, not typed text):
#   body           ← 1. Raw Body                       (Catch Raw Hook)
#   auth           ← 1. Headers Http Authorization     (Catch Raw Hook)
#   token          ← the shared token (INTAKE_ZAPIER_AUTH_TOKEN), without "Bearer"
#   expected_type  ← CAMPAIGN_BRIEF, CDP_ONBOARDING, or CLAIM_PACKET (typed text)
#
# Tested by evaluation/tests/test_zapier_code_step.py against the sample payloads.
import hmac
import json

DELIM = "~~~"  # never appears in the data; descriptions contain commas and newlines


def joined(values):
    return DELIM.join("" if value is None else str(value) for value in values)


result = {"ok": "false", "reason": ""}
auth = (input_data.get("auth") or "").strip()
expected = "Bearer " + (input_data.get("token") or "").strip()
expected_type = (input_data.get("expected_type") or "CAMPAIGN_BRIEF").strip()
try:
    payload = json.loads(input_data.get("body") or "{}")
except ValueError:
    payload = {}

if expected == "Bearer " or not hmac.compare_digest(auth, expected):
    result["reason"] = "Missing or wrong token"
elif payload.get("event_type") != "DOCUMENT_APPROVED" or payload.get("document_type") != expected_type:
    result["reason"] = "Not an approved " + expected_type
else:
    jira = payload.get("jira") or {}
    epic = jira.get("epic") or {}
    issues = jira.get("issues") or []
    labels = epic.get("labels") or (issues[0].get("labels", []) if issues else [])
    result.update(
        {
            "ok": "true",
            "event_label": next((label for label in labels if label.startswith("intake-")), ""),
            "has_epic": "true" if epic else "false",
            "epic_summary": epic.get("summary", ""),
            "epic_description": epic.get("description", ""),
            "epic_labels": ",".join(epic.get("labels", [])),
            "epic_due_date": epic.get("due_date") or "",
            "issue_count": len(issues),
            "issue_types": joined(issue.get("issue_type") for issue in issues),
            "issue_summaries": joined(issue.get("summary") for issue in issues),
            "issue_descriptions": joined(issue.get("description") for issue in issues),
            "issue_labels": joined(",".join(issue.get("labels", [])) for issue in issues),
            "issue_due_dates": joined(issue.get("due_date") for issue in issues),
            "issue_priorities": joined(issue.get("priority") for issue in issues),
        }
    )

output = result
