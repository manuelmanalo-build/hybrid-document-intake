from __future__ import annotations

import asyncio

from intake_api.infrastructure.config import get_settings
from intake_api.infrastructure.logging import configure_logging
from intake_orchestrator.dispatcher import (
    GraphResumeDispatcher,
    GraphRunDispatcher,
    run_dispatchers_forever,
)
from intake_orchestrator.graph import ConfiguredResolutionAgent, open_graph_executor
from intake_orchestrator.repository import (
    GraphResumeRepository,
    GraphRunRepository,
    build_async_engine,
    build_async_session_factory,
)
from intake_orchestrator.workflow import PostgresWorkflowBackend


async def async_main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = build_async_engine(settings)
    session_factory = build_async_session_factory(engine)
    repository = GraphRunRepository(session_factory)
    resume_repository = GraphResumeRepository(session_factory)
    workflow = PostgresWorkflowBackend(
        session_factory, webhook_max_attempts=settings.webhook_max_attempts
    )
    resolution_agent = ConfiguredResolutionAgent(settings=settings, session_factory=session_factory)
    try:
        async with open_graph_executor(
            settings,
            initialize_checkpoint_schema=False,
            workflow=workflow,
            resolution_agent=resolution_agent,
        ) as executor:
            dispatcher = GraphRunDispatcher(
                repository=repository,
                executor=executor,
                settings=settings,
            )
            resume_dispatcher = GraphResumeDispatcher(
                repository=resume_repository,
                executor=executor,
                settings=settings,
            )
            await run_dispatchers_forever(
                dispatcher, resume_dispatcher, settings.graph_request_poll_seconds
            )
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
