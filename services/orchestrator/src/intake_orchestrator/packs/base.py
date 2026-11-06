"""Domain-pack interface (ADR 0009).

A pack bundles everything that is specific to one document type: which agent tools it adds and how
the policy guard scopes them, how those tools run, its authoritative validation and routing, its
decision-confidence inputs, and what happens on rejection. The agent graph, policy guard, backends,
and parent workflow dispatch on the pack registered for the classified document type, so adding a
document type does not add branches to shared code. AP invoices remain the built-in default type.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from intake_api.domain.models import ExtractionCandidate, ReviewTask
from intake_api.infrastructure.config import Settings
from intake_orchestrator.agent.extraction import BoundedExtractionContent


@dataclass(frozen=True, slots=True)
class LookupLimit:
    """A per-run call limit: the agent-state counter key and the AgentLimits attribute."""

    tool: str
    state_key: str
    limit_attribute: str
    message: str


@dataclass(frozen=True, slots=True)
class PackAgentSpec:
    """Declarative agent surface of a pack, enforced by the policy guard and stub planner."""

    document_type: str
    segmentation_tool: str
    text_extraction_tool: str
    visual_extraction_tool: str
    lookups: tuple[LookupLimit, ...]
    human_action: str
    # Results the in-memory test backend returns for this pack's segmentation and lookup tools.
    deterministic_results: Mapping[str, Mapping[str, object]]
    # Arguments the deterministic stub model sends when a decision plan calls a lookup tool.
    stub_arguments: Mapping[str, Mapping[str, object]]
    # Optional deterministic tool that must run once before segmentation (for example a PII scan).
    preparation_tool: str | None = None

    @property
    def extraction_tools(self) -> frozenset[str]:
        return frozenset({self.text_extraction_tool, self.visual_extraction_tool})

    @property
    def tools(self) -> frozenset[str]:
        preparation = {self.preparation_tool} if self.preparation_tool else set[str]()
        return frozenset(
            {
                *preparation,
                self.segmentation_tool,
                *self.extraction_tools,
                *(item.tool for item in self.lookups),
            }
        )


@dataclass(frozen=True, slots=True)
class ToolEffectContext:
    session: AsyncSession
    tenant_id: UUID
    document_id: UUID
    agent_run_id: UUID
    operation_key: str


class PackBackendServices(Protocol):
    """Shared backend capabilities a pack's tool effects may use."""

    @property
    def provider(self) -> str: ...

    @property
    def graph_run_id(self) -> UUID: ...

    async def stored_document_path(
        self, session: AsyncSession, *, tenant_id: UUID, document_id: UUID
    ) -> Path: ...

    async def bounded_document_content(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        document_id: UUID,
        include_images: bool,
    ) -> BoundedExtractionContent: ...

    async def persist_candidate(
        self,
        session: AsyncSession,
        *,
        candidate: BaseModel,
        document_type: str,
        schema_version: str,
        prompt_version: str,
        attempt_number: int,
        result: Mapping[str, object],
        identity: tuple[UUID, UUID, UUID],
        operation_key: str,
    ) -> None: ...


class PackAgentEffects(Protocol):
    """Runs a pack's tools inside the backend's tool transaction."""

    async def execute(
        self,
        services: PackBackendServices,
        context: ToolEffectContext,
        tool_name: str,
        arguments: BaseModel,
    ) -> dict[str, object]: ...

    async def advisory_validation(
        self,
        services: PackBackendServices,
        context: ToolEffectContext,
        candidate: ExtractionCandidate,
    ) -> dict[str, object]: ...


class PackWorkflow(Protocol):
    """Authoritative parent-graph behavior for a pack."""

    confidence_weights: Mapping[str, Decimal]
    confidence_algorithm: str

    async def validate(
        self,
        session: AsyncSession,
        *,
        candidate: ExtractionCandidate,
        review_task: ReviewTask | None,
        phase: str,
        stored_phase: str,
        run_id: UUID,
        graph_run_id: UUID,
        tenant_id: UUID,
        document_id: UUID,
    ) -> list[str]: ...

    async def confidence_components(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        graph_run_id: UUID,
        candidate_id: UUID | None,
        validation_run_id: UUID,
    ) -> dict[str, Decimal | None]: ...

    async def on_rejection(
        self, session: AsyncSession, *, tenant_id: UUID, document_id: UUID
    ) -> None: ...

    async def outbox_payload(
        self, session: AsyncSession, *, tenant_id: UUID, business_data: Mapping[str, object]
    ) -> dict[str, object]:
        """Extra keys for the approved-document outbox event (added to the shared payload)."""
        ...


class EffectsFactory(Protocol):
    def __call__(
        self, *, settings: Settings | None, model: BaseChatModel | None, model_name: str
    ) -> PackAgentEffects: ...


@dataclass(frozen=True, slots=True)
class DomainPack:
    agent: PackAgentSpec
    build_effects: EffectsFactory
    workflow: PackWorkflow

    @property
    def document_type(self) -> str:
        return self.agent.document_type


Strategy = Literal["TEXT", "VISUAL"]
