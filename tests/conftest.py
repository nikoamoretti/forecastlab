from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("FORECASTLAB_DATABASE_URL", db_url)
    monkeypatch.setenv("FORECASTLAB_EMBEDDED_WORKER", "true")
    monkeypatch.setenv("FORECASTLAB_ALLOW_LOCAL_FIXTURES", "true")

    from forecastlab_api import db as db_module
    from forecastlab_api import main as main_mod
    from forecastlab_api.config import settings
    from forecastlab_api.db import Base

    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "embedded_worker", True)
    monkeypatch.setattr(settings, "allow_local_fixtures", True)
    monkeypatch.setattr(settings, "credentials_path", tmp_path / "credentials.json")
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    engine = create_engine(db_url, future=True, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", SessionLocal)
    monkeypatch.setattr(main_mod, "SessionLocal", SessionLocal)
    from forecastlab_api import migrate as migrate_mod

    monkeypatch.setattr(migrate_mod, "engine", engine)

    def get_db():
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    main_mod.app.dependency_overrides[main_mod.get_db] = get_db
    with TestClient(main_mod.app) as test_client:
        yield test_client
    main_mod.app.dependency_overrides.clear()
