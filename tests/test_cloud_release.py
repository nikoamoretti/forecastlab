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
