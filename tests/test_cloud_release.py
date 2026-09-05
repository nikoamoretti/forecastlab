import os

import pytest
from scripts.release_cloud import configure_release_environment


def test_release_ignores_redacted_provider_settings(monkeypatch):
    monkeypatch.setattr(os, "environ", {})
    url = configure_release_environment({
        "DATABASE_URL_UNPOOLED": "postgresql://test:password@localhost/release",
        "BLOB_READ_WRITE_TOKEN": "test-blob-token",
        "FORECASTLAB_MODEL_API_KEY": "[SENSITIVE]",
        "FORECASTLAB_ALLOW_LOCAL_FIXTURES": "[SENSITIVE]",
        "FORECASTLAB_MAX_COST_USD": "[SENSITIVE]",
    })
    assert url.drivername == "postgresql+psycopg"
    assert os.environ["FORECASTLAB_ALLOW_LOCAL_FIXTURES"] == "false"
    assert os.environ["FORECASTLAB_EMBEDDED_WORKER"] == "false"
    assert "FORECASTLAB_MODEL_API_KEY" not in os.environ
    assert "FORECASTLAB_MAX_COST_USD" not in os.environ


@pytest.mark.parametrize("missing", ["DATABASE_URL_UNPOOLED", "BLOB_READ_WRITE_TOKEN"])
def test_release_requires_readable_backup_credentials(monkeypatch, missing):
    monkeypatch.setattr(os, "environ", {})
    values = {"DATABASE_URL_UNPOOLED": "postgresql://test:password@localhost/release", "BLOB_READ_WRITE_TOKEN": "test"}
    values[missing] = "[SENSITIVE]"
    with pytest.raises(RuntimeError, match="release_credential_unavailable:" + missing):
        configure_release_environment(values)
    assert not os.environ


@pytest.mark.parametrize("name", [".env", "apps/api/.env.production", "data/local/credentials.json", "data/forecastlab.db", "tests/test_api.py"])
def test_api_release_excludes_private_and_test_files(name):
    from scripts.vercel_release import included
    assert not included(name, web=False)


def test_web_release_isolates_repository_root_and_secrets():
    from scripts.vercel_release import included
    assert included("apps/web/app/page.tsx", web=True)
    assert not included("apps/api/forecastlab_api/main.py", web=True)
    assert not included("apps/web/.env.local", web=True)
    assert not included("apps/web/e2e/autopilot.spec.ts", web=True)


def test_rest_environment_filters_out_preview_values(monkeypatch):
    from scripts.vercel_release import VercelProject
    project = VercelProject("project", "token", "team")
    def request(path, **_):
        if path.endswith("/env"):
            return {"envs": [
                {"id": "prod", "key": "DATABASE_URL_UNPOOLED", "target": ["production"]},
                {"id": "preview", "key": "DATABASE_URL_UNPOOLED", "target": ["preview"]},
            ]}
        assert path.endswith("/env/prod")
        return {"value": "production", "decrypted": True}
    monkeypatch.setattr(project, "request", request)
    assert project.environment() == {"DATABASE_URL_UNPOOLED": "production"}
    project.client.close()
