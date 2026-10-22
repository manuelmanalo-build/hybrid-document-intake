from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Decimal

ALGORITHM_VERSION = "decision-confidence-v2"
WEIGHTS = {
    "classification_confidence": Decimal("0.15"),
    "required_field_completeness": Decimal("0.20"),
    "critical_field_confidence": Decimal("0.20"),
    "evidence_coverage": Decimal("0.15"),
    "supplier_match_quality": Decimal("0.10"),
    "purchase_order_match_quality": Decimal("0.10"),
    "deterministic_rule_score": Decimal("0.10"),
}

RISK_PENALTY_CAP = Decimal("0.60")
SCORE_FLOOR = Decimal("0")
SCORE_CAP = Decimal("1")
ROUNDING_QUANTUM = Decimal("0.0001")


CLAIM_ALGORITHM_VERSION = "decision-confidence-claims-v1"
CLAIM_WEIGHTS = {
    "classification_confidence": Decimal("0.15"),
    "required_field_completeness": Decimal("0.20"),
    "critical_field_confidence": Decimal("0.20"),
    "evidence_coverage": Decimal("0.15"),
    "policy_match_quality": Decimal("0.10"),
    "vehicle_match_quality": Decimal("0.10"),
    "deterministic_rule_score": Decimal("0.10"),
}


def decision_confidence(
    components: dict[str, Decimal | None],
    *,
    rule_based_risk: Decimal,
    weights: Mapping[str, Decimal] = WEIGHTS,
) -> Decimal:
    applicable = [
        (weights[name], value)
        for name, value in components.items()
        if name in weights and value is not None
    ]
    if not applicable:
        return Decimal("0.0000")
    denominator = sum((weight for weight, _ in applicable), Decimal("0"))
    weighted = sum((weight * _clamp(value) for weight, value in applicable), Decimal("0"))
    mean = weighted / denominator
    adjusted = mean * (Decimal("1") - RISK_PENALTY_CAP * _clamp(rule_based_risk))
    return _clamp(adjusted).quantize(ROUNDING_QUANTUM, rounding=ROUND_HALF_EVEN)


def _clamp(value: Decimal) -> Decimal:
    return min(SCORE_CAP, max(SCORE_FLOOR, value))
