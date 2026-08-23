from __future__ import annotations

from sqlalchemy import select

from forecastlab_api.experiments import CURRENT_BUILTIN_KEY, CURRENT_BUILTIN_VERSION, current_builtin_dataset
from forecastlab_api.models import BenchmarkDataset
from forecastlab_api.seed import seed_synthetic_benchmarks


def test_repeated_seeding_is_idempotent(client) -> None:
    from forecastlab_api.db import SessionLocal

    with SessionLocal() as session:
        first = seed_synthetic_benchmarks(session)
        second = seed_synthetic_benchmarks(session)
        session.commit()
        assert first.id == second.id
        assert first.builtin_key == CURRENT_BUILTIN_KEY
        assert first.builtin_version == CURRENT_BUILTIN_VERSION
        rows = session.scalars(
            select(BenchmarkDataset).where(
                BenchmarkDataset.builtin_key == CURRENT_BUILTIN_KEY,
                BenchmarkDataset.builtin_version == CURRENT_BUILTIN_VERSION,
            )
        ).all()
        assert len(rows) == 1


def test_compatibility_endpoint_selects_current_builtin(client) -> None:
    import uuid

    from forecastlab_api.db import SessionLocal

    with SessionLocal() as session:
        session.add(
            BenchmarkDataset(
                id=str(uuid.uuid4()),
                name="older_synthetic",
                description="decoy",
                dataset_hash="decoy-hash",
                provenance="test",
                is_synthetic=True,
                question_count=0,
            )
        )
        session.commit()
        current = current_builtin_dataset(session)
        assert current is not None
        assert current.builtin_key == CURRENT_BUILTIN_KEY
        assert current.builtin_version == CURRENT_BUILTIN_VERSION
    response = client.post("/api/benchmarks/run")
    assert response.status_code == 200
    with SessionLocal() as session:
        from forecastlab_api.models import BenchmarkExperiment

        experiment = session.get(BenchmarkExperiment, response.json()["experiment_id"])
        assert experiment is not None
        assert experiment.dataset_id == current_builtin_dataset(session).id


def test_fixture_content_change_archives_old_version(client, monkeypatch) -> None:
    from forecastlab_api import seed as seed_mod
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.seed import seed_synthetic_benchmarks

    def other_rows():
        return [
            {
                "question": "Will a changed fixture appear?",
                "forecast_date": "2024-01-01",
                "resolution_date": "2026-01-01",
                "outcome": "1",
                "resolution_source": "fixture",
                "category": "test",
                "is_synthetic": "true",
            }
        ]

    with SessionLocal() as session:
        original = seed_synthetic_benchmarks(session)
        original_id = original.id
        session.commit()
    monkeypatch.setattr(seed_mod, "_fixture_rows", other_rows)
    with SessionLocal() as session:
        changed = seed_synthetic_benchmarks(session)
        session.commit()
        old = session.get(BenchmarkDataset, original_id)
        assert old is not None
        assert old.archived_at is not None
        assert changed.id != original_id
        assert changed.builtin_key == CURRENT_BUILTIN_KEY
        assert changed.builtin_version == CURRENT_BUILTIN_VERSION
        current_rows = session.scalars(
            select(BenchmarkDataset).where(
                BenchmarkDataset.builtin_key == CURRENT_BUILTIN_KEY,
                BenchmarkDataset.builtin_version == CURRENT_BUILTIN_VERSION,
                BenchmarkDataset.archived_at.is_(None),
            )
        ).all()
        assert len(current_rows) == 1


def test_lab_summary_labels_exist() -> None:
    from pathlib import Path

    text = Path("apps/web/app/lab/page.tsx").read_text(encoding="utf-8")
    assert "All-valid metrics" in text
    assert "Full-run-only metrics" in text
    assert "Outcome mix" in text
    assert "Run V1 10-question comparison" in text
    assert "Evidence coverage" in text
    assert "without claiming either profile is superior" in text
