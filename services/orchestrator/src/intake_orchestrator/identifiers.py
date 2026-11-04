from uuid import NAMESPACE_URL, UUID, uuid5


def stable_id(kind: str, operation_key: str) -> UUID:
    """Deterministic UUID for replay-safe writes."""
    return uuid5(NAMESPACE_URL, f"document-intake:{kind}:{operation_key}")
