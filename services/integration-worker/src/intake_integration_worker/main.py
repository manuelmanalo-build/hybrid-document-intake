import asyncio

from intake_api.infrastructure.config import get_settings
from intake_api.infrastructure.logging import configure_logging
from intake_integration_worker.worker import (
    HttpxZapierSender,
    IntegrationWorker,
    OutboxRepository,
    run_forever,
)
from intake_orchestrator.repository import build_async_engine, build_async_session_factory


async def async_main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = build_async_engine(settings)
    sender = HttpxZapierSender(settings)
    try:
        worker = IntegrationWorker(
            repository=OutboxRepository(build_async_session_factory(engine)),
            sender=sender,
            settings=settings,
        )
        await run_forever(worker, settings.webhook_poll_seconds)
    finally:
        await sender.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
