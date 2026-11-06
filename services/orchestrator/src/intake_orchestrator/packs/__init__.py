"""Registered domain packs (ADR 0009). AP invoices are the built-in default document type."""

from collections.abc import Mapping

from intake_orchestrator.packs.base import DomainPack
from intake_orchestrator.packs.briefs import BRIEFS_PACK
from intake_orchestrator.packs.cdp import CDP_PACK
from intake_orchestrator.packs.claims import CLAIMS_PACK

PACKS: Mapping[str, DomainPack] = {
    pack.document_type: pack for pack in (CLAIMS_PACK, BRIEFS_PACK, CDP_PACK)
}


def pack_for_type(document_type: str | None) -> DomainPack | None:
    return PACKS.get(document_type) if document_type is not None else None


def pack_for_tool(tool_name: str) -> DomainPack | None:
    return next((pack for pack in PACKS.values() if tool_name in pack.agent.tools), None)


def pack_tools() -> frozenset[str]:
    return frozenset(tool for pack in PACKS.values() for tool in pack.agent.tools)


def pack_extraction_tools() -> frozenset[str]:
    return frozenset(tool for pack in PACKS.values() for tool in pack.agent.extraction_tools)
