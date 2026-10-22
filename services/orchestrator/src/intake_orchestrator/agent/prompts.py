from __future__ import annotations

import hashlib

PROMPT_VERSION = "document-intake-agent-v1"

AGENT_SYSTEM_PROMPT = (
    "Choose exactly one allowlisted tool. Never approve, reject, create integrations, or return "
    "hidden reasoning. Human review is mandatory."
)

EXTRACTION_SYSTEM_PROMPT = (
    "Extract only AP-invoice fields into the required schema. Never make an approval, rejection, "
    "routing, or operational decision. Use null for unavailable values and cite only bounded "
    "supplied evidence."
)

PROMPT_CONTENT_SHA256 = hashlib.sha256(
    f"{AGENT_SYSTEM_PROMPT}\n{EXTRACTION_SYSTEM_PROMPT}".encode()
).hexdigest()

# Claim-packet prompts (ADR 0008) are versioned and hashed separately so that adding them does
# not change the recorded identity of the invoice prompt set.
CLAIM_PROMPT_VERSION = "claims-intake-agent-v1"

CLAIM_SEGMENTATION_SYSTEM_PROMPT = (
    "Label every page of a personal-auto claim packet with exactly one of LOSS_NOTICE, "
    "REPAIR_ESTIMATE, POLICE_REPORT, CORRESPONDENCE, or OTHER, in page order. Never make a "
    "coverage, routing, approval, or denial decision."
)

CLAIM_EXTRACTION_SYSTEM_PROMPT = (
    "Extract only claim-packet fields into the required schema. Use null for unavailable values "
    "and cite only bounded supplied evidence with its page. Keep sources separate: vin_reported "
    "comes from the loss notice, or from correspondence when there is no notice; vin_on_estimate "
    "comes from the repair estimate; injury_indicated_on_form is the loss-notice checkbox; "
    "injury_mentioned_in_correspondence is any injury described in an email or narrative. Never "
    "reconcile conflicting values. Never make a coverage, routing, approval, or denial decision."
)

CLAIM_PROMPT_CONTENT_SHA256 = hashlib.sha256(
    f"{CLAIM_SEGMENTATION_SYSTEM_PROMPT}\n{CLAIM_EXTRACTION_SYSTEM_PROMPT}".encode()
).hexdigest()

# Document classification runs for every upload, so it has its own version and content hash.
CLASSIFICATION_PROMPT_VERSION = "document-classification-v1"

CLASSIFICATION_SYSTEM_PROMPT = (
    "Classify the supplied document as exactly one of AP_INVOICE (a supplier invoice), "
    "CLAIM_PACKET (a personal-auto insurance claim packet such as a loss notice with a repair "
    "estimate, police report, or claim correspondence), UNSUPPORTED (a readable document of any "
    "other kind), or UNKNOWN (unreadable). Give a confidence from 0 to 1. Never make an approval, "
    "coverage, routing, or denial decision."
)

CLASSIFICATION_PROMPT_CONTENT_SHA256 = hashlib.sha256(
    CLASSIFICATION_SYSTEM_PROMPT.encode()
).hexdigest()
