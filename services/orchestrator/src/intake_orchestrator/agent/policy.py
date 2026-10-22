from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ValidationError

from intake_orchestrator.agent.errors import PolicyViolation
from intake_orchestrator.agent.tools import (
    CLAIM_TOOLS,
    EXTRACTION_TOOLS,
    INVOICE_TOOLS,
    TOOL_SCHEMAS,
)


@dataclass(frozen=True, slots=True)
class AgentLimits:
    max_tool_calls: int = 10
    max_extraction_attempts: int = 3
    max_supplier_lookups: int = 2
    max_purchase_order_lookups: int = 2
    max_policy_lookups: int = 2
    max_vehicle_lookups: int = 2


class AgentPolicyGuard:
    def __init__(self, limits: AgentLimits) -> None:
        self.limits = limits

    def validate(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        tenant_id: str,
        document_id: str,
        agent_run_id: str,
        tool_call_count: int,
        extraction_attempts: int,
        supplier_lookups: int,
        purchase_order_lookups: int,
        tools_used: list[str],
        extraction_candidate_id: str | None,
        document_type: str | None = None,
        policy_lookups: int = 0,
        vehicle_lookups: int = 0,
    ) -> BaseModel:
        schema = TOOL_SCHEMAS.get(tool_name)
        if schema is None:
            raise PolicyViolation("Requested tool is not allowlisted")
        if tool_call_count >= self.limits.max_tool_calls:
            raise PolicyViolation("Agent tool-call limit reached")
        try:
            parsed = schema.model_validate(arguments)
        except ValidationError as exc:
            raise PolicyViolation("Requested tool arguments do not match the schema") from exc

        values = parsed.model_dump(mode="json")
        if values["tenant_id"] != tenant_id or values["document_id"] != document_id:
            raise PolicyViolation("Tool tenant or document identity does not match the graph run")
        if values["agent_run_id"] != agent_run_id:
            raise PolicyViolation("Tool agent-run identity does not match the active run")
        self._reject_unauthorized_content(values)

        # Document-type scoping is enforced here, not in the prompt (ADR 0008): invoice tools
        # need an AP_INVOICE classification and claim tools need a CLAIM_PACKET classification.
        if tool_name in INVOICE_TOOLS and document_type != "AP_INVOICE":
            raise PolicyViolation("Invoice tools require an AP_INVOICE classification")
        if tool_name in CLAIM_TOOLS and document_type != "CLAIM_PACKET":
            raise PolicyViolation("Claim tools require a CLAIM_PACKET classification")
        if tool_name == "segment_claim_packet" and "segment_claim_packet" in tools_used:
            raise PolicyViolation("A claim packet is segmented once per agent run")
        if tool_name.startswith("extract_claim_from_") and "segment_claim_packet" not in tools_used:
            raise PolicyViolation("Claim extraction requires page segmentation first")
        if tool_name == "lookup_policy" and policy_lookups >= self.limits.max_policy_lookups:
            raise PolicyViolation("Policy-lookup limit reached")
        if (
            tool_name == "lookup_policy_vehicle"
            and vehicle_lookups >= self.limits.max_vehicle_lookups
        ):
            raise PolicyViolation("Policy-vehicle-lookup limit reached")
        if tool_name in EXTRACTION_TOOLS:
            if extraction_attempts >= self.limits.max_extraction_attempts:
                raise PolicyViolation("Extraction-attempt limit reached")
            if int(values["attempt_number"]) != extraction_attempts + 1:
                raise PolicyViolation("Extraction attempt number is not the next permitted attempt")
        if tool_name == "lookup_supplier" and supplier_lookups >= self.limits.max_supplier_lookups:
            raise PolicyViolation("Supplier-lookup limit reached")
        if (
            tool_name == "lookup_purchase_order"
            and purchase_order_lookups >= self.limits.max_purchase_order_lookups
        ):
            raise PolicyViolation("Purchase-order-lookup limit reached")
        if (
            tool_name == "retry_extraction"
            and extraction_attempts >= self.limits.max_extraction_attempts
        ):
            raise PolicyViolation("No extraction attempts remain")
        if tool_name == "submit_recommendation":
            prerequisites = {"inspect_document", "classify_document"}
            if not prerequisites.issubset(tools_used):
                raise PolicyViolation("Recommendation prerequisites are incomplete")
            requested_candidate = values.get("extraction_candidate_id")
            if values.get("document_type") in {"AP_INVOICE", "CLAIM_PACKET"} and (
                "validate_candidate" not in tools_used or extraction_candidate_id is None
            ):
                raise PolicyViolation("Recommendation prerequisites are incomplete for the type")
            if document_type is not None and values.get("document_type") != document_type:
                raise PolicyViolation("Recommendation document type does not match classification")
            if requested_candidate != extraction_candidate_id:
                raise PolicyViolation("Recommendation candidate does not match agent state")
            if values.get("tools_used") != tools_used:
                raise PolicyViolation("Recommendation tool summary does not match agent state")
        return parsed

    @classmethod
    def _reject_unauthorized_content(cls, value: object) -> None:
        forbidden_fragments = (
            "http://",
            "https://",
            "file://",
            "../",
            "\\..\\",
            "select ",
            "insert ",
            "update ",
            "delete ",
            "drop ",
            "zapier",
            "approve_document",
        )
        for item in cls._walk(value):
            lowered = item.casefold()
            if any(fragment in lowered for fragment in forbidden_fragments):
                raise PolicyViolation("Tool arguments encode an unauthorized operation")

    @classmethod
    def _walk(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            output: list[str] = []
            for key, item in cast(dict[object, object], value).items():
                output.extend(cls._walk(key))
                output.extend(cls._walk(item))
            return output
        if isinstance(value, list):
            output = []
            for item in cast(list[object], value):
                output.extend(cls._walk(item))
            return output
        return []


def identity_matches(arguments: BaseModel, *, tenant_id: UUID, document_id: UUID) -> bool:
    values: dict[str, Any] = arguments.model_dump()
    return values.get("tenant_id") == tenant_id and values.get("document_id") == document_id
