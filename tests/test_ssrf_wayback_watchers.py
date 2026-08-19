from datetime import UTC, datetime

import pytest

from forecastlab.hashing import redact_secrets
from forecastlab.ssrf import UnsafeURLError, validate_url
from forecastlab.watchers import canonical_watch_value, extract_json_path, watch_changed
from forecastlab.wayback import mock_snapshots, nearest_eligible_snapshot, snapshot_eligible


def test_ssrf_blocks_loopback() -> None:
    with pytest.raises(UnsafeURLError):
        validate_url("http://127.0.0.1/secret")
    with pytest.raises(UnsafeURLError):
        validate_url("file:///etc/passwd")
    assert validate_url("https://fixtures.forecastlab.local/bls", allow_local_fixtures=True)


def test_wayback_cutoff() -> None:
    as_of = datetime(2024, 1, 1, tzinfo=UTC)
    snaps = mock_snapshots("https://example.org/report")
    nearest = nearest_eligible_snapshot(snaps, as_of)
    assert nearest is not None
    assert snapshot_eligible(nearest.timestamp, as_of)
    too_new = datetime(2025, 6, 1, tzinfo=UTC)
    assert snapshot_eligible(too_new, as_of) is False


def test_watch_hash_and_json_path() -> None:
    payload = {"unemployment": {"value": 4.1}}
    assert extract_json_path(payload, "$.unemployment.value") == 4.1
    left = canonical_watch_value(4.1)
    right = canonical_watch_value(4.2)
    assert watch_changed(left, right) is True
    assert watch_changed(left, left) is False


def test_secret_redaction() -> None:
    text = redact_secrets("Authorization: Bearer sk-test-secret-value-123456")
    assert "sk-test-secret-value-123456" not in text
    assert "REDACTED" in text
