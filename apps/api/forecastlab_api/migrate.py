from __future__ import annotations

import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import NullPool

from forecastlab_api.config import ROOT, settings

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
    url = database_url or settings.database_url
    if _already_at_head(url):
        return
    command.upgrade(alembic_config(url), "head")


def _already_at_head(url: str) -> bool:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    bind = create_engine(url, poolclass=NullPool)
    try:
        with bind.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
    finally:
        bind.dispose()
    head = ScriptDirectory.from_config(alembic_config(url)).get_current_head()
    return bool(current) and current == head


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
    bind = create_engine(url, poolclass=NullPool)
    try:
        inspector = inspect(bind)
        needs_stamp = inspector.has_table("questions") and not inspector.has_table("alembic_version")
    finally:
        bind.dispose()
    if needs_stamp:
        command.stamp(alembic_config(url), BASELINE_REVISION)
    apply_migrations(url)
