"""Reference CD-001..CD-015 rules and cdp-routing-v1 for ground truth.

Kept independent of the production rules so they can be tested against it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.brief_fixtures.expected import business_days
from scripts.cdp_fixtures.data import (
    AGREEMENTS,
    ATTRIBUTES,
    COMPATIBLE,
    CONNECTORS,
    Agreement,
    Request,
)

PATTERNS = {
    "EMAIL": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "PHONE": re.compile(r"(?<!\d)(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)"),
    "SSN": re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
}


def scan(text: str) -> dict[str, int]:
    return {
        kind: len(pattern.findall(text))
        for kind, pattern in PATTERNS.items()
        if pattern.findall(text)
    }


@dataclass(frozen=True)
class Expectation:
    agreement: str | None
    flags: tuple[str, ...]
    queue: str
    reason: str


def resolve_agreement(request: Request) -> Agreement | None:
    mine = [agreement for agreement in AGREEMENTS if agreement.client == request.client]
    if request.agreement_stated:
        stated = [a for a in mine if a.number == request.agreement_stated]
        if stated:
            return stated[0]
    if request.go_live is not None:
        covering = [a for a in mine if a.start <= request.go_live <= a.end]
        if covering:
            return covering[0]
    return max(mine, key=lambda a: a.end) if mine else None


def expect(
    request: Request, sample_findings: dict[str, int], seen: set[tuple[str, str, str]]
) -> Expectation:
    agreement = resolve_agreement(request)
    flags: list[str] = []
    mappings = [*request.mappings, *request.email_added]
    if agreement is None:
        flags.append("AGREEMENT_NOT_FOUND")
    elif agreement.status != "ACTIVE" or (
        request.go_live is not None and not agreement.start <= request.go_live <= agreement.end
    ):
        flags.append("AGREEMENT_NOT_ACTIVE")
    elif request.region not in agreement.regions:
        flags.append("REGION_NOT_PERMITTED")
    if not (
        request.source_name and request.cadence and request.region and request.go_live and mappings
    ):
        flags.append("REQUEST_INCOMPLETE")
    connector = CONNECTORS.get(request.connector)
    if connector is None or (request.cadence and request.cadence not in connector.cadences):
        flags.append("CONNECTOR_UNSUPPORTED")
    if not any(
        m.proposed_attribute and ATTRIBUTES[m.proposed_attribute].identity_key for m in mappings
    ):
        flags.append("NO_IDENTITY_KEY")
    if any(
        m.proposed_attribute is None
        or m.proposed_attribute not in ATTRIBUTES
        or ATTRIBUTES[m.proposed_attribute].data_type not in COMPATIBLE[m.source_type]
        for m in mappings
    ):
        flags.append("MAPPING_INVALID")
    personal = [
        m
        for m in mappings
        if m.proposed_attribute and ATTRIBUTES[m.proposed_attribute].personal_class != "NONE"
    ]
    if any(not m.client_marked_personal for m in personal) or (
        request.declares_no_personal and personal
    ):
        flags.append("UNDECLARED_PERSONAL_DATA")
    if sample_findings:
        flags.append("UNMASKED_IDENTIFIERS_IN_PACKET")
    if personal and (
        request.lawful_basis is None
        or (agreement is not None and request.lawful_basis not in agreement.lawful_bases)
        or (request.lawful_basis == "CONSENT" and not request.consent_mechanism)
    ):
        flags.append("LAWFUL_BASIS_MISSING")
    sensitive = [
        m
        for m in mappings
        if m.proposed_attribute and ATTRIBUTES[m.proposed_attribute].personal_class == "SENSITIVE"
    ]
    if (sensitive or request.declares_sensitive) and (agreement is None or not agreement.sensitive):
        flags.append("SENSITIVE_DATA_NOT_PERMITTED")
    if agreement is not None and request.retention_days > agreement.max_retention_days:
        flags.append("RETENTION_EXCEEDS_AGREEMENT")
    launches = [day for day in (request.go_live, request.email_go_live) if day]
    if (
        connector is not None
        and launches
        and business_days(request.received, min(launches)) < connector.min_business_days
    ):
        flags.append("TIMELINE_AT_RISK")
    if (request.email_go_live and request.email_go_live != request.go_live) or request.email_added:
        flags.append("CORRESPONDENCE_CONFLICT")
    key = (agreement.number if agreement else "", request.connector, request.source_name.casefold())
    if agreement is not None and key in seen:
        flags.append("DUPLICATE_REQUEST")
    if agreement is not None:
        seen.add(key)

    table = (
        ({"DUPLICATE_REQUEST"}, "DUPLICATE_REVIEW", "Same agreement, connector, and source"),
        (
            {"AGREEMENT_NOT_FOUND", "AGREEMENT_NOT_ACTIVE", "REGION_NOT_PERMITTED"},
            "AGREEMENT_REVIEW",
            "No valid data agreement",
        ),
        (
            {
                "UNDECLARED_PERSONAL_DATA",
                "UNMASKED_IDENTIFIERS_IN_PACKET",
                "SAMPLE_NOT_SCANNABLE",
                "LAWFUL_BASIS_MISSING",
                "SENSITIVE_DATA_NOT_PERMITTED",
                "RETENTION_EXCEEDS_AGREEMENT",
            },
            "PRIVACY_REVIEW",
            "Personal-data findings",
        ),
        ({"REQUEST_INCOMPLETE"}, "NEEDS_CLIENT_INFO", "Request is missing required information"),
        (
            {"CONNECTOR_UNSUPPORTED", "NO_IDENTITY_KEY", "MAPPING_INVALID"},
            "MAPPING_REVIEW",
            "Mapping or connector needs an engineer",
        ),
        ({"TIMELINE_AT_RISK"}, "RUSH_REVIEW", "Build lead time at risk"),
        ({"CORRESPONDENCE_CONFLICT"}, "ONBOARDING_REVIEW", "Request needs reconciling"),
    )
    for triggers, queue, reason in table:
        if triggers & set(flags):
            return Expectation(agreement.number if agreement else None, tuple(flags), queue, reason)
    return Expectation(
        agreement.number if agreement else None,
        tuple(flags),
        "READY_TO_BUILD",
        "Complete, mapped, and cleared",
    )
