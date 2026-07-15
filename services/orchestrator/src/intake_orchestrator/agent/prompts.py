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
