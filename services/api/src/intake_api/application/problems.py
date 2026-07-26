from dataclasses import dataclass


@dataclass(slots=True)
class ProblemException(Exception):
    status: int
    title: str
    detail: str
    problem_type: str

    def __str__(self) -> str:
        return self.detail


class InvalidUpload(ProblemException):
    def __init__(self, detail: str, problem_type: str = "invalid-upload") -> None:
        super().__init__(400, "Invalid PDF upload", detail, problem_type)


class UploadTooLarge(ProblemException):
    def __init__(self, maximum_bytes: int) -> None:
        super().__init__(
            413,
            "Upload exceeds configured limit",
            f"The uploaded file exceeds the {maximum_bytes}-byte limit.",
            "upload-too-large",
        )


class IdempotencyConflict(ProblemException):
    def __init__(self) -> None:
        super().__init__(
            409,
            "Idempotency key conflict",
            "The Idempotency-Key was already used for a different upload request.",
            "idempotency-key-conflict",
        )


class AuthenticationRequired(ProblemException):
    def __init__(self) -> None:
        super().__init__(
            401,
            "Authentication required",
            "A valid credential or development session is required.",
            "authentication-required",
        )


class AuthenticationUnavailable(ProblemException):
    def __init__(self) -> None:
        super().__init__(
            503,
            "Authentication is not configured",
            "The server cannot accept document submissions until authentication is configured.",
            "authentication-unavailable",
        )


class AuthorizationDenied(ProblemException):
    def __init__(
        self, detail: str = "The actor is not authorized for this review operation."
    ) -> None:
        super().__init__(403, "Authorization denied", detail, "authorization-denied")


class ResourceNotFound(ProblemException):
    def __init__(self, detail: str = "The requested review task was not found.") -> None:
        super().__init__(404, "Resource not found", detail, "resource-not-found")


class ReviewConflict(ProblemException):
    def __init__(self, detail: str) -> None:
        super().__init__(409, "Review task conflict", detail, "review-task-conflict")
