from pathlib import Path

import pytest

from intake_orchestrator.agent.tools import INVOICE_TOOLS, TOOL_SCHEMAS, validate_tool_output
from intake_orchestrator.packs import PACKS, pack_for_tool, pack_for_type, pack_tools

SHARED_MODULES = [
    "agent/backend.py",
    "agent/graph.py",
    "agent/models.py",
    "agent/policy.py",
    "workflow.py",
    "graph.py",
]
SOURCE = Path("services/orchestrator/src/intake_orchestrator")


def test_claims_and_briefs_are_registered_domain_packs() -> None:
    assert set(PACKS) == {"CLAIM_PACKET", "CAMPAIGN_BRIEF"}
    assert pack_for_tool("lookup_client_sow") is pack_for_type("CAMPAIGN_BRIEF")
    pack = pack_for_type("CLAIM_PACKET")
    assert pack is not None
    assert pack_for_type("AP_INVOICE") is None
    assert pack_for_tool("segment_claim_packet") is pack


def test_pack_tools_are_registered_disjoint_and_never_invoice_tools() -> None:
    tools = pack_tools()

    assert tools <= set(TOOL_SCHEMAS)
    assert not tools & INVOICE_TOOLS
    owners = [pack.document_type for pack in PACKS.values() for _ in pack.agent.tools]
    assert len(owners) == len(tools)


@pytest.mark.parametrize("document_type", sorted(PACKS))
def test_deterministic_results_satisfy_the_tool_output_contracts(document_type: str) -> None:
    spec = PACKS[document_type].agent
    for tool_name, result in spec.deterministic_results.items():
        assert tool_name in spec.tools
        validate_tool_output(tool_name, dict(result))


@pytest.mark.parametrize("module", SHARED_MODULES)
def test_shared_modules_contain_no_pack_specific_branches(module: str) -> None:
    text = (SOURCE / module).read_text()

    for pack in PACKS.values():
        assert pack.document_type not in text
        for tool_name in pack.agent.tools:
            assert tool_name not in text
