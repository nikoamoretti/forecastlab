from __future__ import annotations

import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from forecastlab_api.config import ROOT, settings
from forecastlab_api.db import engine
from forecastlab_api.models import Base

ALEMBIC_INI = ROOT / "alembic.ini"


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    if database_url:
        cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def apply_migrations(database_url: str | None = None) -> None:
    command.upgrade(alembic_config(database_url), "head")


def apply_schema(database_url: str | None = None) -> None:
    """Apply Alembic migrations. Existing local DBs without alembic_version are stamped after create_all."""
    lock_path = Path(settings.data_dir) / "migrate.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        _exclusive_lock(lock)
        _apply_schema_unlocked(database_url)


def _exclusive_lock(handle) -> None:
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except ImportError:
        time.sleep(0.2)


def _apply_schema_unlocked(database_url: str | None = None) -> None:
    from sqlalchemy import create_engine

    bind = engine if database_url is None else create_engine(database_url)
    inspector = inspect(bind)
    if inspector.has_table("alembic_version") and inspector.has_table("benchmark_experiments"):
        return
    try:
        apply_migrations(database_url)
        return
    except Exception:
        inspector = inspect(bind)
        if inspector.has_table("questions") and not inspector.has_table("alembic_version"):
            Base.metadata.create_all(bind)
            with bind.connect() as connection:
                if connection.dialect.name == "sqlite":
                    connection.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
                    connection.commit()
            command.stamp(alembic_config(database_url), "head")
            return
        Base.metadata.create_all(bind)
        try:
            command.stamp(alembic_config(database_url), "head")
        except Exception:
            pass
