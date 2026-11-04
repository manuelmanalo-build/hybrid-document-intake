"""Load the agency brief reference CSVs into the bootstrap tenant (local demo and tests).

Usage: python -m intake_api.load_brief_reference [directory]  (default test-data/briefs)
"""

import hashlib
import os
import sys
from pathlib import Path

import structlog

from intake_api.application.briefs_reference import (
    load_brief_reference_data,
    parse_lead_times,
    parse_sow_brands,
    parse_sows,
)
from intake_api.application.claims_reference import MAX_REFERENCE_FILE_BYTES, ReferenceDataError
from intake_api.infrastructure.config import get_settings
from intake_api.infrastructure.database import SessionFactory
from intake_api.infrastructure.logging import configure_logging

logger = structlog.get_logger(__name__)
FILES = ("statements_of_work.csv", "sow_brands.csv", "lead_times.csv")


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
        argv[1]
        if len(argv) > 1
        else os.environ.get("INTAKE_BRIEF_REFERENCE_DIR", "test-data/briefs")
    )
    raw = [_read(directory / name) for name in FILES]
    digest = hashlib.sha256(b"\0".join(raw)).hexdigest()
    sows, brands, leads = (
        parse_sows(raw[0].decode()),
        parse_sow_brands(raw[1].decode()),
        parse_lead_times(raw[2].decode()),
    )
    with SessionFactory() as session, session.begin():
        summary = load_brief_reference_data(
            session,
            tenant_id=settings.bootstrap_tenant_id,
            sows=sows,
            brands=brands,
            lead_times=leads,
            actor_id="brief-reference-loader",
            correlation_id=f"brief-reference-{digest[:16]}",
            source_sha256=digest,
        )
    logger.info(
        "brief_reference_data_loaded",
        tenant_id=str(settings.bootstrap_tenant_id),
        source_sha256=digest,
        sows_inserted=summary.sows_inserted,
        sows_updated=summary.sows_updated,
        brands_inserted=summary.brands_inserted,
        lead_times_upserted=summary.lead_times_upserted,
    )


if __name__ == "__main__":
    main(sys.argv)
