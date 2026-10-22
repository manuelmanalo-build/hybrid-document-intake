from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

# Claim-packet tools (ADR 0008). Kept in a separate import block so the invoice surface is easy to
# audit on its own.
from intake_orchestrator.agent.claim_contracts import (
    ExtractClaimInput,
    ExtractClaimOutput,
    LookupPolicyInput,
    LookupPolicyOutput,
    LookupPolicyVehicleInput,
    LookupPolicyVehicleOutput,
    SegmentClaimPacketInput,
    SegmentClaimPacketOutput,
)
from intake_orchestrator.agent.contracts import (
    ClassifyDocumentInput,
    ClassifyDocumentOutput,
    ExtractInvoiceInput,
    ExtractInvoiceOutput,
    InspectDocumentInput,
    InspectDocumentOutput,
    LookupPurchaseOrderInput,
    LookupPurchaseOrderOutput,
    LookupSupplierInput,
    LookupSupplierOutput,
    RetryExtractionInput,
    RetryExtractionOutput,
    SubmitRecommendationInput,
    SubmitRecommendationOutput,
    ValidateCandidateInput,
    ValidateCandidateOutput,
)


class ToolBackend(Protocol):
    async def execute(
        self, *, tool_name: str, arguments: BaseModel, operation_key: str
    ) -> dict[str, object]: ...


TOOL_SCHEMAS: dict[str, type[BaseModel]] = {
    "inspect_document": InspectDocumentInput,
    "classify_document": ClassifyDocumentInput,
    "extract_invoice_from_text": ExtractInvoiceInput,
    "extract_invoice_from_visual_content": ExtractInvoiceInput,
    "lookup_supplier": LookupSupplierInput,
    "lookup_purchase_order": LookupPurchaseOrderInput,
    "validate_candidate": ValidateCandidateInput,
    "retry_extraction": RetryExtractionInput,
    "submit_recommendation": SubmitRecommendationInput,
    "segment_claim_packet": SegmentClaimPacketInput,
    "extract_claim_from_text": ExtractClaimInput,
    "extract_claim_from_visual_content": ExtractClaimInput,
    "lookup_policy": LookupPolicyInput,
    "lookup_policy_vehicle": LookupPolicyVehicleInput,
}

TOOL_OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "inspect_document": InspectDocumentOutput,
    "classify_document": ClassifyDocumentOutput,
    "extract_invoice_from_text": ExtractInvoiceOutput,
    "extract_invoice_from_visual_content": ExtractInvoiceOutput,
    "lookup_supplier": LookupSupplierOutput,
    "lookup_purchase_order": LookupPurchaseOrderOutput,
    "validate_candidate": ValidateCandidateOutput,
    "retry_extraction": RetryExtractionOutput,
    "submit_recommendation": SubmitRecommendationOutput,
    "segment_claim_packet": SegmentClaimPacketOutput,
    "extract_claim_from_text": ExtractClaimOutput,
    "extract_claim_from_visual_content": ExtractClaimOutput,
    "lookup_policy": LookupPolicyOutput,
    "lookup_policy_vehicle": LookupPolicyVehicleOutput,
}

INVOICE_TOOLS = frozenset(
    {
        "extract_invoice_from_text",
        "extract_invoice_from_visual_content",
        "lookup_supplier",
        "lookup_purchase_order",
    }
)
CLAIM_TOOLS = frozenset(
    {
        "segment_claim_packet",
        "extract_claim_from_text",
        "extract_claim_from_visual_content",
        "lookup_policy",
        "lookup_policy_vehicle",
    }
)
EXTRACTION_TOOLS = frozenset(
    {
        "extract_invoice_from_text",
        "extract_invoice_from_visual_content",
        "extract_claim_from_text",
        "extract_claim_from_visual_content",
    }
)

TOOL_DESCRIPTIONS = {
    "inspect_document": "Inspect tenant-scoped PDF metadata and bounded content characteristics.",
    "classify_document": (
        "Classify the inspected document as AP_INVOICE, CLAIM_PACKET, UNSUPPORTED, or UNKNOWN."
    ),
    "extract_invoice_from_text": "Extract an AP invoice candidate from bounded PDF text.",
    "extract_invoice_from_visual_content": "Extract an AP invoice candidate from visual pages.",
    "lookup_supplier": "Look up a tenant-scoped supplier reference.",
    "lookup_purchase_order": "Look up a tenant-scoped purchase-order reference.",
    "validate_candidate": (
        "Run the deterministic rules for the candidate's document type (invoice BR rules or "
        "claim CR rules) for advisory agent feedback."
    ),
    "retry_extraction": "Authorize a bounded subsequent extraction attempt.",
    "submit_recommendation": "Submit the final non-authoritative recommendation.",
    "segment_claim_packet": (
        "For a CLAIM_PACKET only: label every page as LOSS_NOTICE, REPAIR_ESTIMATE, "
        "POLICE_REPORT, CORRESPONDENCE, or OTHER. Required before claim extraction."
    ),
    "extract_claim_from_text": (
        "For a CLAIM_PACKET only: extract a claim candidate from bounded PDF text."
    ),
    "extract_claim_from_visual_content": (
        "For a CLAIM_PACKET only: extract a claim candidate from page images (scanned packets)."
    ),
    "lookup_policy": "For a CLAIM_PACKET only: look up a tenant-scoped policy by number.",
    "lookup_policy_vehicle": (
        "For a CLAIM_PACKET only: check whether a VIN is on a policy (EXACT, NEAR, or NONE)."
    ),
}


def build_tools(backend: ToolBackend) -> list[BaseTool]:
    return [_build_tool(name, schema, backend) for name, schema in TOOL_SCHEMAS.items()]


def stable_operation_key(
    *, agent_run_id: str, call_index: int, tool_name: str, arguments: dict[str, object]
) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    material = f"{agent_run_id}:{call_index}:{tool_name}:{canonical}".encode()
    return hashlib.sha256(material).hexdigest()


def _build_tool(name: str, schema: type[BaseModel], backend: ToolBackend) -> BaseTool:
    async def invoke(config: RunnableConfig, **kwargs: object) -> dict[str, object]:
        parsed = schema.model_validate(kwargs)
        configurable = cast(dict[str, object], config.get("configurable", {}))
        operation_key = configurable.get("operation_key")
        if not isinstance(operation_key, str) or len(operation_key) != 64:
            raise ValueError("A stable operation key is required for tool execution")
        result = await backend.execute(
            tool_name=name,
            arguments=parsed,
            operation_key=operation_key,
        )
        return validate_tool_output(name, result)

    coroutine = cast(Callable[..., Awaitable[Any]], invoke)
    return StructuredTool.from_function(
        coroutine=coroutine,
        name=name,
        description=TOOL_DESCRIPTIONS[name],
        args_schema=schema,
    )


def tool_map(tools: list[BaseTool]) -> dict[str, BaseTool]:
    return {tool.name: tool for tool in tools}


def validate_tool_output(tool_name: str, result: object) -> dict[str, object]:
    schema = TOOL_OUTPUT_SCHEMAS.get(tool_name)
    if schema is None:
        raise ValueError("Tool output schema is not registered")
    validated = schema.model_validate(result)
    return cast(dict[str, object], validated.model_dump(mode="json"))
