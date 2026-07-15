from __future__ import annotations

import json
import os
import threading
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

MAX_PAYLOAD_BYTES = 64 * 1024


class Delivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=1, max_length=128)
    payload: dict[str, object]


class DeliveryReceipt(BaseModel):
    accepted: bool
    duplicate: bool
    delivery_count: int


app = FastAPI(title="Mock Zapier", version="1.0.0")
_lock = threading.Lock()
_deliveries: dict[str, Delivery] = {}
_attempts = 0


@app.get("/health/ready")
def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/hooks/catch", response_model=DeliveryReceipt)
async def catch_hook(
    request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> DeliveryReceipt:
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A bounded Idempotency-Key is required")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_PAYLOAD_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Payload is too large")
    try:
        payload = TypeAdapter(dict[str, object]).validate_python(json.loads(body))
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payload must be a JSON object") from exc
    global _attempts
    with _lock:
        _attempts += 1
        failures = int(os.environ.get("MOCK_ZAPIER_FAILURES_BEFORE_SUCCESS", "0"))
        configured_status = int(os.environ.get("MOCK_ZAPIER_RESPONSE_STATUS", "202"))
        if _attempts <= failures or configured_status >= 400:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE if _attempts <= failures else configured_status,
                "Configured mock delivery failure",
            )
        duplicate = idempotency_key in _deliveries
        _deliveries.setdefault(
            idempotency_key,
            Delivery(idempotency_key=idempotency_key, payload=payload),
        )
        return DeliveryReceipt(accepted=True, duplicate=duplicate, delivery_count=len(_deliveries))


@app.get("/deliveries", response_model=list[Delivery])
def deliveries() -> list[Delivery]:
    with _lock:
        return list(_deliveries.values())
