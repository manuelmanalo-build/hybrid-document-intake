from sqlalchemy import text

from intake_api.infrastructure.config import get_settings
from intake_api.infrastructure.database import build_engine


def main() -> None:
    engine = build_engine(get_settings())
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
