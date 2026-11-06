"""Deterministic personal-data scanner and redactor (`pii-scanner-v1`, ADR 0010).

Finds direct identifiers in bounded text and replaces them with typed tokens. It never returns the
matched values, only their types and counts, so findings can be stored and audited safely. Patterns
are US-centric in v1; national IDs and IBANs are deferred.
"""

from __future__ import annotations

import re
from collections.abc import Callable

PII_SCANNER_VERSION = "pii-scanner-v1"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def _luhn(candidate: str) -> bool:
    digits = [int(character) for character in candidate if character.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


# Order matters: cards before phones so a card number is never half-matched as a phone.
_DETECTORS: tuple[tuple[str, re.Pattern[str], Callable[[str], bool]], ...] = (
    ("EMAIL", _EMAIL, lambda _value: True),
    ("CARD", _CARD, _luhn),
    ("SSN", _SSN, lambda _value: True),
    ("PHONE", _PHONE, lambda _value: True),
)


def scan_identifiers(text: str) -> dict[str, int]:
    """Counts of direct identifiers by type; values are never returned."""
    counts: dict[str, int] = {}
    redact_identifiers(text, counts)
    return counts


def redact_identifiers(text: str, counts: dict[str, int] | None = None) -> str:
    """Replace each direct identifier with `[TYPE]`, optionally counting replacements."""
    result = text
    for kind, pattern, accept in _DETECTORS:

        def replace(
            match: re.Match[str], kind: str = kind, accept: Callable[[str], bool] = accept
        ) -> str:
            if not accept(match.group(0)):
                return match.group(0)
            if counts is not None:
                counts[kind] = counts.get(kind, 0) + 1
            return f"[{kind}]"

        result = pattern.sub(replace, result)
    return result
