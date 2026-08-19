from __future__ import annotations

import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from forecastlab_api.config import ROOT, settings
from forecastlab_api.db import engine

ALEMBIC_INI = ROOT / "alembic.ini"
BASELINE_REVISION = "20260818_0001"


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    url = database_url or settings.database_url
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["forecastlab_database_url"] = url
    return cfg


def apply_migrations(database_url: str | None = None) -> None:
    command.upgrade(alembic_config(database_url), "head")


def apply_schema(database_url: str | None = None) -> None:
    """Upgrade to Alembic head under a lock. Migration failures abort startup."""
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
    url = database_url or settings.database_url
    bind = engine if database_url is None else create_engine(url)
    created = database_url is not None
    try:
        inspector = inspect(bind)
        if inspector.has_table("questions") and not inspector.has_table("alembic_version"):
            command.stamp(alembic_config(url), BASELINE_REVISION)
        apply_migrations(url)
    finally:
        if created:
            bind.dispose()
