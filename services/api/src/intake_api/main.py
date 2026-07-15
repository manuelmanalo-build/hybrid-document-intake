from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from intake_api.api.correlation import CorrelationIdMiddleware
from intake_api.api.documents import router as documents_router
from intake_api.api.integrations import router as integrations_router
from intake_api.api.problem_handlers import install_problem_handlers
from intake_api.api.query import router as query_router
from intake_api.api.review_tasks import router as review_tasks_router
from intake_api.application.intake import build_intake_service
from intake_api.application.integrations import IntegrationAdminService
from intake_api.application.query import QueryService
from intake_api.application.review import ReviewService
from intake_api.infrastructure.config import Settings, get_settings
from intake_api.infrastructure.database import build_engine, build_session_factory
from intake_api.infrastructure.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    configure_logging(active_settings.log_level)
    engine = build_engine(active_settings)
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        active_settings.storage_root.mkdir(parents=True, exist_ok=True)
        yield
        engine.dispose()

    app = FastAPI(
        title="Document Intake API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = active_settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.intake_service = build_intake_service(active_settings, session_factory)
    app.state.review_service = ReviewService(active_settings, session_factory)
    app.state.integration_admin_service = IntegrationAdminService(active_settings, session_factory)
    app.state.query_service = QueryService(active_settings, session_factory)
    app.add_middleware(CorrelationIdMiddleware)
    install_problem_handlers(app)
    app.include_router(documents_router)
    app.include_router(review_tasks_router)
    app.include_router(integrations_router)
    app.include_router(query_router)

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "live"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ready"}

    return app


app = create_app()
