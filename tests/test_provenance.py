from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from forecastlab.fetch import fetch_document
from forecastlab.hashing import import_hash, redact_secrets
from forecastlab.schemas import Driver, TrackForecastOutput
from forecastlab.wayback import mock_snapshots, nearest_eligible_snapshot, snapshot_eligible


def test_source_after_cutoff_is_rejected() -> None:
    as_of = datetime(2023, 1, 1, tzinfo=UTC)
    doc = fetch_document(
        "https://fixtures.forecastlab.local/bls-employment-situation",
        as_of=as_of,
        allow_local_fixtures=True,
    )
    assert doc.rejected is True
    assert doc.as_of_eligible is False
    assert doc.rejection_reason == "published_after_as_of"


def test_source_before_cutoff_is_kept() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=UTC)
    doc = fetch_document(
        "https://fixtures.forecastlab.local/bls-employment-situation",
        as_of=as_of,
        allow_local_fixtures=True,
    )
    assert doc.rejected is False
    assert doc.as_of_eligible is True


def test_wayback_snapshot_after_cutoff_ineligible() -> None:
    as_of = datetime(2024, 1, 1, tzinfo=UTC)
    snaps = mock_snapshots("https://example.org/report")
    nearest = nearest_eligible_snapshot(snaps, as_of)
    assert nearest is not None
    assert snapshot_eligible(nearest.timestamp, as_of)
    assert not snapshot_eligible(datetime(2025, 6, 1, tzinfo=UTC), as_of)


def test_duplicate_benchmark_hash() -> None:
    fields = {
        "question": "Will X happen?",
        "forecast_date": "2024-01-01",
        "resolution_date": "2025-01-01",
        "outcome": "1",
        "resolution_source": "fixture",
    }
    assert import_hash(fields) == import_hash(fields)
    other = dict(fields)
    other["outcome"] = "0"
    assert import_hash(fields) != import_hash(other)


def test_structured_output_drops_factual_drivers_without_evidence() -> None:
    parsed = TrackForecastOutput.model_validate(
        {
            "probability": 0.4,
            "key_drivers": [
                {"factor": "unsupported", "direction": "up", "importance": 0.5, "evidence_ids": [], "inference": False},
                {"factor": "ok", "direction": "down", "importance": 0.4, "evidence_ids": ["ev-1"], "inference": False},
            ],
            "reasoning_summary": "ok",
        }
    )
    assert len(parsed.key_drivers) == 1
    assert parsed.key_drivers[0].factor == "ok"
    Driver.model_validate({"factor": "infer", "direction": "unclear", "importance": 0.2, "inference": True})


def test_api_key_redaction() -> None:
    text = redact_secrets("posted api_key=sk-test-secret-value-123456 to logs")
    assert "sk-test-secret-value-123456" not in text
    assert "REDACTED" in text


def test_synthetic_benchmark_file_exists() -> None:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "benchmarks" / "synthetic_binary.csv"
    assert path.exists()
    assert "is_synthetic" in path.read_text(encoding="utf-8")
