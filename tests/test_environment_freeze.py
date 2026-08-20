from __future__ import annotations

import json
from pathlib import Path

from forecastlab.environment import build_environment_identity, compare_environment, tracked_source_hash
from forecastlab.errors import ExperimentEnvironmentMismatch
from forecastlab.pricing import load_pricing, pricing_hash
from forecastlab_api.experiments import execute_benchmark_task
from forecastlab_api.models import BenchmarkProfileSnapshot


def test_ignored_files_do_not_change_source_hash(tmp_path: Path) -> None:
    (tmp_path / "packages" / "forecasting" / "forecastlab").mkdir(parents=True)
    (tmp_path / "apps" / "api" / "forecastlab_api").mkdir(parents=True)
    (tmp_path / "packages" / "forecasting" / "forecastlab" / "engine.py").write_text("x = 1\n", encoding="utf-8")
    first = tracked_source_hash(root=tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "local.db").write_bytes(b"sqlite")
    (tmp_path / "apps" / "api" / "forecastlab_api" / "notes.log").write_text("log", encoding="utf-8")
    second = tracked_source_hash(root=tmp_path)
    assert first == second


def test_compare_environment_ignores_pricing_hash() -> None:
    frozen = {"git_commit": "aaa", "tracked_source_hash": "s", "pricing_hash": "old"}
    current = {"git_commit": "aaa", "tracked_source_hash": "s", "pricing_hash": "new"}
    assert compare_environment(frozen, current) == []


def test_code_mutation_fails_closed(client, monkeypatch) -> None:
    from sqlalchemy import select

    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import BenchmarkTask

    state = {"mutated": False}

    def changing_hash(*, root=None):
        return "source-b" if state["mutated"] else "source-a"

    monkeypatch.setattr("forecastlab.environment.tracked_source_hash", changing_hash)
    datasets = client.get("/api/datasets").json()["datasets"]
    synth = next(item for item in datasets if item.get("is_builtin") or item["name"] == "synthetic_fixtures_v1")
    created = client.post("/api/experiments", json={"dataset_id": synth["id"], "profile_ids": ["single_agent_equal_budget_v1"]})
    assert created.status_code == 200
    state["mutated"] = True
    with SessionLocal() as session:
        task = session.scalars(select(BenchmarkTask).where(BenchmarkTask.experiment_id == created.json()["id"])).first()
        assert task is not None
        try:
            execute_benchmark_task(session, task)
            raised = False
        except ExperimentEnvironmentMismatch:
            raised = True
        assert raised


def test_dependency_mutation_fails_closed(client, monkeypatch) -> None:
    from sqlalchemy import select

    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import BenchmarkTask

    state = {"mutated": False}

    def changing_file(relative: str, *, root=None):
        if relative != "pyproject.toml":
            return "same"
        return "dep-b" if state["mutated"] else "dep-a"

    monkeypatch.setattr("forecastlab.environment.file_hash", changing_file)
    datasets = client.get("/api/datasets").json()["datasets"]
    synth = next(item for item in datasets if item.get("is_builtin") or item["name"] == "synthetic_fixtures_v1")
    created = client.post("/api/experiments", json={"dataset_id": synth["id"], "profile_ids": ["single_agent_equal_budget_v1"]})
    assert created.status_code == 200
    state["mutated"] = True
    with SessionLocal() as session:
        task = session.scalars(select(BenchmarkTask).where(BenchmarkTask.experiment_id == created.json()["id"])).first()
        assert task is not None
        try:
            execute_benchmark_task(session, task)
            raised = False
        except ExperimentEnvironmentMismatch:
            raised = True
        assert raised


def test_pricing_file_mutation_uses_frozen_catalog(client, monkeypatch) -> None:
    from sqlalchemy import select

    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import BenchmarkExperiment, BenchmarkTask
    from forecastlab_api.worker import drain_jobs

    original = load_pricing()
    datasets = client.get("/api/datasets").json()["datasets"]
    synth = next(item for item in datasets if item.get("is_builtin") or item["name"] == "synthetic_fixtures_v1")
    created = client.post("/api/experiments", json={"dataset_id": synth["id"], "profile_ids": ["single_agent_equal_budget_v1"]})
    assert created.status_code == 200
    mutated = json.loads(json.dumps(original))
    mutated.setdefault("search", {})
    mutated["search"]["tavily"] = {"estimated_per_request": 99.0}

    def fake_load(*, path=None, catalog=None):
        if catalog is not None:
            return catalog
        return mutated

    monkeypatch.setattr("forecastlab.pricing.load_pricing", fake_load)
    monkeypatch.setattr("forecastlab_api.experiments.load_pricing", fake_load)
    drain_jobs(max_steps=10)
    with SessionLocal() as session:
        experiment = session.get(BenchmarkExperiment, created.json()["id"])
        assert experiment is not None
        snapshot = session.scalars(
            select(BenchmarkProfileSnapshot).where(BenchmarkProfileSnapshot.experiment_id == experiment.id)
        ).one()
        stored = json.loads(snapshot.pricing_snapshot_json)
        assert stored.get("search", {}).get("tavily", {}).get("estimated_per_request") != 99.0
        assert pricing_hash(catalog=stored) != pricing_hash(catalog=mutated)
        tasks = session.scalars(select(BenchmarkTask).where(BenchmarkTask.experiment_id == experiment.id)).all()
        assert tasks
        assert all(task.status == "completed" for task in tasks)


def test_dirty_tree_blocks_real_experiment(client, monkeypatch) -> None:
    monkeypatch.setattr("forecastlab_api.experiments.working_tree_dirty", lambda **_k: True)
    imported = client.post(
        "/api/benchmarks/import",
        files={
            "file": (
                "real.csv",
                (
                    b"question,forecast_date,resolution_date,outcome,resolution_source,category,provenance,is_synthetic,"
                    b"exact_yes,exact_no,resolution_deadline,authoritative_source\n"
                    b"Will dirt block?,2024-01-01,2026-01-01,1,https://example.org,test,user,false,Yes,No,2026-01-01,https://example.org\n"
                ),
                "text/csv",
            )
        },
        data={"name": "real_dirty"},
    )
    assert imported.status_code == 200
    created = client.post(
        "/api/experiments",
        json={"dataset_id": imported.json()["dataset_id"], "profile_ids": ["single_agent_equal_budget_v1"]},
    )
    assert created.status_code == 400
    assert "dirty_working_tree" in str(created.json())


def test_identity_builder_includes_required_fields() -> None:
    identity = build_environment_identity(prompt_bundle_hash="p", profile_hashes={"a": "b"}, pricing_catalog={"x": 1})
    assert {"git_commit", "tracked_source_hash", "pyproject_hash", "package_lock_hash", "pricing_hash"} <= set(identity)
    assert identity["application_version"] == "0.3.1"
