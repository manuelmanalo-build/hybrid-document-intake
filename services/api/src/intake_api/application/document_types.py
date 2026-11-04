"""Per-document-type API behavior (ADR 0009). AP invoices use the built-in defaults."""

from collections.abc import Callable, Mapping

from intake_api.application.claim_corrections import normalize_claim_correction

CorrectionNormalizer = Callable[[str, str | None], object]

CORRECTION_NORMALIZERS: Mapping[str, CorrectionNormalizer] = {
    "CLAIM_PACKET": normalize_claim_correction,
}
