from __future__ import annotations

import asyncio

from intake_api.infrastructure.config import get_settings
from intake_api.infrastructure.logging import configure_logging
from intake_orchestrator.graph import initialize_checkpoint_schema


async def async_main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    await initialize_checkpoint_schema(settings)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
