"""AGENT_PROVIDER=local: an open-weight model behind a self-hosted, OpenAI-compatible server.

A small in-process HTTP server stands in for Ollama, LM Studio, vLLM, or llama.cpp, so these tests
exercise the real client, request shape, and response parsing without a model or network access.
"""

import asyncio
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr, ValidationError

from intake_api.infrastructure.config import Settings
from intake_orchestrator.agent.brief_extraction import (
    LangChainStructuredBriefExtractor,
    StubStructuredBriefExtractor,
)
from intake_orchestrator.agent.extraction import BoundedExtractionContent
from intake_orchestrator.agent.models import (
    LOCAL_MAX_OUTPUT_TOKENS,
    LOCAL_PLACEHOLDER_API_KEY,
    create_chat_model,
    require_one_tool_call,
)


class FakeLocalServer:
    """Answers /v1/chat/completions like an OpenAI-compatible local server and records requests."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                del format, args

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, dict(self.headers.items()), body))
                message = outer.reply(body)
                data = json.dumps(
                    {
                        "id": "chatcmpl-local",
                        "object": "chat.completion",
                        "created": 0,
                        "model": body["model"],
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "tool_calls"
                                if "tool_calls" in message
                                else "stop",
                                "message": message,
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}/v1"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def reply(self, body: dict[str, Any]) -> dict[str, Any]:
        if "tools" in body:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "inspect_document",
                            "arguments": json.dumps({"document_id": "doc-1"}),
                        },
                    }
                ],
            }
        candidate = asyncio.run(
            StubStructuredBriefExtractor(model="local", prompt_version="p").extract(
                strategy="TEXT", bounded_content=BoundedExtractionContent(text="")
            )
        )
        return {"role": "assistant", "content": candidate.model_dump_json()}

    def close(self) -> None:
        self._server.shutdown()


@pytest.fixture
def server() -> Iterator[FakeLocalServer]:
    fake = FakeLocalServer()
    yield fake
    fake.close()


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"agent_provider": "local", "agent_model": "qwen3:14b"}
    values.update(overrides)
    return Settings.model_validate(values)


def test_local_provider_needs_no_cloud_key_and_defaults_to_ollama() -> None:
    settings = _settings()

    assert settings.agent_base_url == "http://localhost:11434/v1"
    assert settings.agent_api_key is None


def test_local_provider_still_requires_a_model_name_and_an_http_url() -> None:
    with pytest.raises(ValidationError, match="AGENT_MODEL must be explicitly configured"):
        Settings.model_validate({"agent_provider": "local"})
    with pytest.raises(ValidationError, match="AGENT_BASE_URL must be"):
        _settings(agent_base_url="localhost:11434")


def test_the_client_targets_the_local_server_and_never_reuses_a_cloud_key() -> None:
    model = create_chat_model(
        _settings(
            openai_api_key=SecretStr("sk-real-cloud-key"), agent_base_url="http://gpu:8000/v1"
        )
    )

    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base == "http://gpu:8000/v1"
    assert model.openai_api_key is not None
    assert model.openai_api_key.get_secret_value() == LOCAL_PLACEHOLDER_API_KEY
    assert model.temperature == 0


def test_a_configured_local_key_is_used() -> None:
    model = create_chat_model(_settings(agent_api_key=SecretStr("vllm-key")))

    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_key is not None
    assert model.openai_api_key.get_secret_value() == "vllm-key"


@pytest.mark.asyncio
async def test_structured_extraction_round_trips_through_a_local_server(
    server: FakeLocalServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    model = create_chat_model(_settings(agent_base_url=server.base_url))
    extractor = LangChainStructuredBriefExtractor(
        model=model, provider="local", model_name="qwen3:14b", prompt_version="p"
    )

    brief = await extractor.extract(
        strategy="TEXT", bounded_content=BoundedExtractionContent(text="[page 1]\nbrief")
    )

    assert brief.campaign_name.value == "Winter Warmers"
    assert brief.brand.provider == "local"
    assert len(brief.deliverables) == 3
    path, headers, body = server.requests[0]
    assert path == "/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {LOCAL_PLACEHOLDER_API_KEY}"
    assert body["model"] == "qwen3:14b"
    assert body["temperature"] == 0
    assert body["max_tokens"] == LOCAL_MAX_OUTPUT_TOKENS
    assert body["response_format"]["type"] == "json_schema"
    properties = body["response_format"]["json_schema"]["schema"]["properties"]
    assert "Never repeat" in properties["deliverables_added_in_correspondence"]["description"]


@pytest.mark.asyncio
async def test_agent_tool_calls_round_trip_through_a_local_server(server: FakeLocalServer) -> None:
    @tool
    def inspect_document(document_id: str) -> str:
        """Inspect a stored document."""
        return document_id

    model = create_chat_model(_settings(agent_base_url=server.base_url))
    message = await model.bind_tools([inspect_document]).ainvoke([HumanMessage(content="Begin.")])

    assert require_one_tool_call(message) == ("inspect_document", {"document_id": "doc-1"})  # type: ignore[arg-type]
    assert server.requests[0][2]["tools"][0]["function"]["name"] == "inspect_document"


@pytest.mark.asyncio
async def test_reasoning_effort_is_sent_only_when_configured(server: FakeLocalServer) -> None:
    @tool
    def inspect_document(document_id: str) -> str:
        """Inspect a stored document."""
        return document_id

    for effort in ("none", "", None):
        model = create_chat_model(
            _settings(agent_base_url=server.base_url, agent_reasoning_effort=effort)
        )
        await model.bind_tools([inspect_document]).ainvoke([HumanMessage(content="Begin.")])

    sent = [body.get("reasoning_effort") for _, _, body in server.requests]
    assert sent == ["none", None, None]
