from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from mock_zapier import main
from mock_zapier.main import app


@pytest.fixture(autouse=True)
def isolated_deliveries() -> Iterator[None]:
    """The mock keeps deliveries in process memory; other suites post to it too."""
    main._deliveries.clear()  # pyright: ignore[reportPrivateUsage]
    yield
    main._deliveries.clear()  # pyright: ignore[reportPrivateUsage]


def test_mock_zapier_deduplicates_by_idempotency_key() -> None:
    with TestClient(app) as client:
        first = client.post(
            "/hooks/catch",
            headers={"Idempotency-Key": "approved-record:unit"},
            json={"event_type": "DOCUMENT_APPROVED"},
        )
        replay = client.post(
            "/hooks/catch",
            headers={"Idempotency-Key": "approved-record:unit"},
            json={"event_type": "DOCUMENT_APPROVED"},
        )

    assert first.status_code == 200
    assert first.json()["duplicate"] is False
    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True
    assert replay.json()["delivery_count"] == 1
