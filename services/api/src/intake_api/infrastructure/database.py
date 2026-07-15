from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from intake_api.infrastructure.config import Settings, get_settings


def build_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url, pool_pre_ping=True)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)


_engine = build_engine(get_settings())
SessionFactory = build_session_factory(_engine)


def get_session() -> Iterator[Session]:
    with SessionFactory() as session:
        yield session
