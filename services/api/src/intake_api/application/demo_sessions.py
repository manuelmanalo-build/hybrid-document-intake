from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

from intake_api.domain.enums import UserRole

DEMO_SESSION_COOKIE = "intake_demo_session"


@dataclass(frozen=True, slots=True)
class DemoSession:
    role: UserRole
    expires_at: datetime


class DemoSessionStore:
    """Process-local demo sessions; deliberately unavailable outside local development."""

    def __init__(self, ttl_seconds: int, max_active: int) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max_active = max_active
        self._sessions: dict[str, DemoSession] = {}
        self._lock = Lock()

    def create(self, role: UserRole) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        with self._lock:
            self._remove_expired(now)
            if len(self._sessions) >= self._max_active:
                oldest = min(self._sessions, key=lambda key: self._sessions[key].expires_at)
                del self._sessions[oldest]
            self._sessions[_digest(token)] = DemoSession(role=role, expires_at=now + self._ttl)
        return token

    def resolve(self, token: str) -> UserRole | None:
        now = datetime.now(UTC)
        key = _digest(token)
        with self._lock:
            self._remove_expired(now)
            session = self._sessions.get(key)
            return session.role if session is not None else None

    def revoke(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(_digest(token), None)

    def _remove_expired(self, now: datetime) -> None:
        expired = [key for key, session in self._sessions.items() if session.expires_at <= now]
        for key in expired:
            del self._sessions[key]


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
