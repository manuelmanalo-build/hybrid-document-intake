"""Load the CDP-onboarding reference CSVs into the bootstrap tenant (local demo and tests).

Usage: python -m intake_api.load_cdp_reference [directory]  (default test-data/cdp)
"""

import hashlib
import os
import sys
from pathlib import Path

import structlog

from intake_api.application.cdp_reference import (
    load_cdp_reference_data,
    parse_agreements,
    parse_attributes,
    parse_connectors,
)
from intake_api.application.claims_reference import MAX_REFERENCE_FILE_BYTES, ReferenceDataError
from intake_api.infrastructure.config import get_settings
from intake_api.infrastructure.database import SessionFactory
from intake_api.infrastructure.logging import configure_logging

logger = structlog.get_logger(__name__)
FILES = ("data_agreements.csv", "canonical_attributes.csv", "connectors.csv")


def _read(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(MAX_REFERENCE_FILE_BYTES + 1)
    if len(data) > MAX_REFERENCE_FILE_BYTES:
        raise ReferenceDataError(f"{path.name} exceeds the bounded size")
    return data


def main(argv: list[str]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    directory = Path(
        argv[1] if len(argv) > 1 else os.environ.get("INTAKE_CDP_REFERENCE_DIR", "test-data/cdp")
    )
    raw = [_read(directory / name) for name in FILES]
    digest = hashlib.sha256(b"\0".join(raw)).hexdigest()
    with SessionFactory() as session, session.begin():
        summary = load_cdp_reference_data(
            session,
            tenant_id=settings.bootstrap_tenant_id,
            agreements=parse_agreements(raw[0].decode()),
            attributes=parse_attributes(raw[1].decode()),
            connectors=parse_connectors(raw[2].decode()),
            actor_id="cdp-reference-loader",
            correlation_id=f"cdp-reference-{digest[:16]}",
            source_sha256=digest,
        )
    logger.info(
        "cdp_reference_data_loaded",
        tenant_id=str(settings.bootstrap_tenant_id),
        source_sha256=digest,
        agreements_upserted=summary.agreements_upserted,
        attributes_upserted=summary.attributes_upserted,
        connectors_upserted=summary.connectors_upserted,
    )


if __name__ == "__main__":
    main(sys.argv)
