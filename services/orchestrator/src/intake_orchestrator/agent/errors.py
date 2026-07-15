class TransientProviderTimeout(TimeoutError):
    """Model provider did not respond within the configured timeout."""


class TransientProviderRateLimit(ConnectionError):
    """Model provider asked the caller to retry later."""


class ProviderUnavailable(ConnectionError):
    """Model provider cannot serve the request after bounded retries."""


class MalformedModelResponse(ValueError):
    """Model response did not contain exactly one valid tool request."""


class AgentToolTimeout(TimeoutError):
    """An allow-listed tool exceeded its configured execution timeout."""


class AgentDeadlineExceeded(RuntimeError):
    """The persisted total agent execution deadline elapsed."""


class PolicyViolation(ValueError):
    """Requested tool operation violates the bounded-agent policy."""


class TransientDatabaseError(ConnectionError):
    """Application persistence is temporarily unavailable."""


class TransientStorageError(ConnectionError):
    """Immutable storage is temporarily unavailable."""
