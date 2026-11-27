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
CLASSIFICATION_PROMPT_VERSION = "document-classification-v3"

CLASSIFICATION_SYSTEM_PROMPT = (
    "Classify the supplied document as exactly one of AP_INVOICE (a supplier invoice), "
    "CLAIM_PACKET (a personal-auto insurance claim packet such as a loss notice with a repair "
    "estimate, police report, or claim correspondence), CAMPAIGN_BRIEF (a client's marketing "
    "campaign brief or brief email to an agency), CDP_ONBOARDING (a request to onboard a data "
    "source into a customer data platform, with a data dictionary or field list), UNSUPPORTED (a "
    "readable document of any "
    "other kind), or UNKNOWN (unreadable). Give a confidence from 0 to 1. Never make an approval, "
    "coverage, routing, or denial decision."
)

CLASSIFICATION_PROMPT_CONTENT_SHA256 = hashlib.sha256(
    CLASSIFICATION_SYSTEM_PROMPT.encode()
).hexdigest()

# Campaign-brief prompts (ADR 0009), versioned and hashed separately.
BRIEF_PROMPT_VERSION = "brief-intake-agent-v2"

BRIEF_SEGMENTATION_SYSTEM_PROMPT = (
    "Label every page of an agency campaign-brief packet with exactly one of BRIEF, "
    "CORRESPONDENCE, MEDIA_PLAN, or OTHER, in page order. Never judge scope, legal risk, routing, "
    "or approval."
)

# v2: small open-weight models copied the brief's deliverables into the correspondence list,
# which the rules correctly read as an email adding work. The correspondence fields are now
# spelled out, here and in the schema's field descriptions.
BRIEF_EXTRACTION_SYSTEM_PROMPT = (
    "Extract a work request from a campaign brief into the required schema. Use null for "
    "unavailable values and cite only bounded supplied evidence with its page. Keep sources "
    "separate. launch_date and deliverables come from the brief itself. "
    "launch_date_in_correspondence and deliverables_added_in_correspondence are only for a "
    "separate, later email or note in the packet: fill them only when such a message states a "
    "different launch date or requests deliverables the brief does not list. When the packet has "
    "no such message, set launch_date_in_correspondence to null and "
    "deliverables_added_in_correspondence to an empty list. Never copy the brief's deliverables "
    "into deliverables_added_in_correspondence. When an email is the only document in the packet, "
    "it is the brief: put its launch date and deliverables in launch_date and deliverables. "
    "received_date is the date the brief was submitted or sent. Map each deliverable to the "
    "closest asset_type. Never reconcile conflicts and never judge scope, legal risk, routing, or "
    "approval."
)

BRIEF_PROMPT_CONTENT_SHA256 = hashlib.sha256(
    f"{BRIEF_SEGMENTATION_SYSTEM_PROMPT}\n{BRIEF_EXTRACTION_SYSTEM_PROMPT}".encode()
).hexdigest()

# CDP-onboarding prompts (ADR 0010), versioned and hashed separately. The model only ever receives
# text that `pii-scanner-v1` has already redacted.
CDP_PROMPT_VERSION = "cdp-onboarding-agent-v2"

CDP_SEGMENTATION_SYSTEM_PROMPT = (
    "Label every page of a CDP data-source onboarding packet with exactly one of ONBOARDING_FORM, "
    "DATA_DICTIONARY, SAMPLE_EXTRACT, PRIVACY_ADDENDUM, CORRESPONDENCE, or OTHER, in page order. "
    "Never judge privacy, legal, or build readiness."
)

# v2: the correspondence fields are spelled out, as in the brief prompt v2.
CDP_EXTRACTION_SYSTEM_PROMPT = (
    "Extract a data-source onboarding request into the required schema. Personal data in the text "
    "is already replaced by tokens such as [EMAIL] and [PHONE]; never try to reconstruct it. Use "
    "null for unavailable values and cite only bounded evidence with its page. For each source "
    "field in the data dictionary, record its type, whether the client marked it personal, and the "
    "canonical attribute it maps to as written by the client (null when none is given). Keep "
    "sources separate. requested_go_live and mappings come from the form and data dictionary. "
    "go_live_in_correspondence and fields_added_in_correspondence are only for a separate, later "
    "email: fill them only when such an email states a different go-live date or adds source "
    "fields the dictionary does not list. When the packet has no such email, set "
    "go_live_in_correspondence to null and fields_added_in_correspondence to an empty list. Never "
    "copy the dictionary's fields into fields_added_in_correspondence. When an email is the only "
    "document in the packet, it is the request: put its go-live and fields in requested_go_live "
    "and mappings. Never decide privacy, legal, scope, routing, or approval."
)

CDP_PROMPT_CONTENT_SHA256 = hashlib.sha256(
    f"{CDP_SEGMENTATION_SYSTEM_PROMPT}\n{CDP_EXTRACTION_SYSTEM_PROMPT}".encode()
).hexdigest()
