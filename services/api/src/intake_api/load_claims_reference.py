"""Load the claims reference CSVs into the bootstrap tenant (local demo and tests).

Usage: python -m intake_api.load_claims_reference [directory]
The directory defaults to INTAKE_CLAIMS_REFERENCE_DIR or test-data/claims and must contain
policies.csv and policy_vehicles.csv.
"""

import hashlib
import os
import sys
from pathlib import Path

import structlog

from intake_api.application.claims_reference import (
    MAX_REFERENCE_FILE_BYTES,
    ReferenceDataError,
    load_claims_reference_data,
    parse_policies,
    parse_vehicles,
)
from intake_api.infrastructure.config import get_settings
from intake_api.infrastructure.database import SessionFactory
from intake_api.infrastructure.logging import configure_logging

logger = structlog.get_logger(__name__)


def _read_bounded(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(MAX_REFERENCE_FILE_BYTES + 1)
    if len(data) > MAX_REFERENCE_FILE_BYTES:
        raise ReferenceDataError(f"{path.name} exceeds the bounded size")
    return data


def main(argv: list[str]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    directory = Path(
        argv[1]
        if len(argv) > 1
        else os.environ.get("INTAKE_CLAIMS_REFERENCE_DIR", "test-data/claims")
    )
    policies_bytes = _read_bounded(directory / "policies.csv")
    vehicles_bytes = _read_bounded(directory / "policy_vehicles.csv")
    digest = hashlib.sha256(policies_bytes + b"\0" + vehicles_bytes).hexdigest()
    policies = parse_policies(policies_bytes.decode("utf-8"))
    vehicles = parse_vehicles(vehicles_bytes.decode("utf-8"))
    with SessionFactory() as session, session.begin():
        summary = load_claims_reference_data(
            session,
            tenant_id=settings.bootstrap_tenant_id,
            policies=policies,
            vehicles=vehicles,
            actor_id="claims-reference-loader",
            correlation_id=f"claims-reference-{digest[:16]}",
            source_sha256=digest,
        )
    logger.info(
        "claims_reference_data_loaded",
        tenant_id=str(settings.bootstrap_tenant_id),
        source_sha256=digest,
        policies_inserted=summary.policies_inserted,
        policies_updated=summary.policies_updated,
        policies_unchanged=summary.policies_unchanged,
        vehicles_inserted=summary.vehicles_inserted,
        vehicles_updated=summary.vehicles_updated,
        vehicles_unchanged=summary.vehicles_unchanged,
    )


if __name__ == "__main__":
    main(sys.argv)
