from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from forecastlab_api.config import settings


class Base(DeclarativeBase):
    pass


connect_args = (
    {"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {}
)
engine = create_engine(settings.database_url, future=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def configure_sqlite_engine(bind) -> None:
    if bind.dialect.name != "sqlite":
        return

    @event.listens_for(bind, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


configure_sqlite_engine(engine)


def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


get_db = get_session
