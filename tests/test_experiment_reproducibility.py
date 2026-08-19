from __future__ import annotations

import csv
import json
from io import BytesIO, StringIO

from forecastlab.hashing import sha256_text
from forecastlab.profiles import PROFILES_DIR, profile_hash
from forecastlab.prompts import PROMPTS_DIR, PromptBundle
from forecastlab.schemas import ForecastProfile
from forecastlab_api.experiments import DEFAULT_EXPERIMENT_PROFILES, dataset_hash_for_rows


def _csv(rows: list[dict[str, object]]) -> bytes:
    header = [
        "question",
        "forecast_date",
        "resolution_date",
        "outcome",
        "resolution_source",
        "category",
        "provenance",
        "is_synthetic",
        "exact_yes",
        "exact_no",
        "resolution_deadline",
        "authoritative_source",
        "fallback_sources",
        "geography",
        "units",
        "ambiguity_notes",
        "cancellation_conditions",
        "resolver_risk_notes",
    ]
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in header})
    return buffer.getvalue().encode("utf-8")


def _row(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "question": "Will frozen contract series exceed 1.0 before 2026-01-01?",
        "forecast_date": "2024-08-01",
        "resolution_date": "2026-01-01",
        "outcome": 1,
        "resolution_source": "https://fixtures.forecastlab.local/contract",
        "category": "test",
        "provenance": "repro-test",
        "is_synthetic": "true",
        "exact_yes": "Yes if the fixture series exceeds 1.0",
        "exact_no": "No if the fixture series stays at or below 1.0",
        "resolution_deadline": "2026-01-01",
        "authoritative_source": "https://fixtures.forecastlab.local/contract",
        "fallback_sources": "[]",
        "geography": "",
        "units": "",
        "ambiguity_notes": "Fixture series only.",
        "cancellation_conditions": "Cancelled if withdrawn.",
        "resolver_risk_notes": "Use the fixture page.",
    }
    payload.update(overrides)
    return payload


def _import(client, rows: list[dict[str, object]], name: str) -> dict:
    response = client.post(
        "/api/benchmarks/import",
        files={"file": (f"{name}.csv", BytesIO(_csv(rows)), "text/csv")},
        data={"name": name},
    )
    return response.json() if response.status_code == 200 else {"status_code": response.status_code, "detail": response.json()}


def test_default_scientific_profiles() -> None:
    assert DEFAULT_EXPERIMENT_PROFILES == ("single_agent_equal_budget_v1", "three_track_equal_budget_v1")


def test_frozen_source_files(client, monkeypatch) -> None:
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import BenchmarkExperiment, BenchmarkProfileSnapshot, ForecastRun
    from forecastlab_api.worker import drain_jobs

    imported = _import(client, [_row(question="Will frozen source series happen?")], "frozen_sources")
    assert imported.get("created") == 1
    created = client.post(
        "/api/experiments",
        json={"dataset_id": imported["dataset_id"], "profile_ids": ["single_agent_equal_budget_v1"]},
    )
    assert created.status_code == 200
    experiment_id = created.json()["id"]

    profile_path = PROFILES_DIR / "single_agent_equal_budget_v1.yaml"
    prompt_path = PROMPTS_DIR / "forecast_single_agent.txt"
    original_profile = profile_path.read_text(encoding="utf-8")
    original_prompt = prompt_path.read_text(encoding="utf-8")
    with SessionLocal() as session:
        snapshot = session.query(BenchmarkProfileSnapshot).filter_by(experiment_id=experiment_id).one()
        stored_profile = ForecastProfile.model_validate(json.loads(snapshot.effective_profile_json))
        stored_source = ForecastProfile.model_validate(json.loads(snapshot.source_profile_json))
        stored_bundle = PromptBundle.model_validate(json.loads(snapshot.prompt_bundle_json))
        stored_prompt_text, _ = stored_bundle.get("forecast_single_agent")
        assert snapshot.profile_hash == profile_hash(stored_source)
        assert json.loads(snapshot.prompt_hashes_json) == stored_bundle.hashes()
        assert sha256_text(stored_prompt_text) == stored_bundle.hashes()["forecast_single_agent"]

    def boom(*_args, **_kwargs):
        raise AssertionError("mutable source loaded during benchmark execution")

    monkeypatch.setattr("forecastlab.engine.load_prompt", boom)
    monkeypatch.setattr("forecastlab.profiles.load_profile", boom)
    monkeypatch.setattr("forecastlab_api.pipeline.load_profile", boom)
    monkeypatch.setattr("forecastlab.execution.load_profile", boom)

    try:
        profile_path.write_text(original_profile.replace("max_tokens: 80000", "max_tokens: 1"), encoding="utf-8")
        prompt_path.write_text(original_prompt + "\nMUTATED_PROMPT_MARKER\n", encoding="utf-8")
        drain_jobs(max_steps=20)
    finally:
        profile_path.write_text(original_profile, encoding="utf-8")
        prompt_path.write_text(original_prompt, encoding="utf-8")

    summary = client.get(f"/api/experiments/{experiment_id}/summary").json()
    assert summary["progress"]["completed_tasks"] == 1
    assert summary["prompt_hashes"] == stored_bundle.hashes()
    assert summary["profile_hashes"]["single_agent_equal_budget_v1"] == snapshot.profile_hash
    with SessionLocal() as session:
        experiment = session.get(BenchmarkExperiment, experiment_id)
        assert experiment is not None
        refreshed = session.query(BenchmarkProfileSnapshot).filter_by(experiment_id=experiment_id).one()
        used = ForecastProfile.model_validate(json.loads(refreshed.effective_profile_json))
        used_bundle = PromptBundle.model_validate(json.loads(refreshed.prompt_bundle_json))
        assert used.max_tokens == stored_profile.max_tokens == 80000
        assert "MUTATED_PROMPT_MARKER" not in used_bundle.get("forecast_single_agent")[0]
        assert json.loads(experiment.prompt_hashes_json) == used_bundle.hashes()
        runs = session.query(ForecastRun).all()
        assert runs
        used_versions = json.loads(runs[0].prompt_versions_json or "{}")
        assert used_versions.get("forecast_single_agent") == used_bundle.versions()["forecast_single_agent"]
        context = json.loads(runs[0].execution_context_json)
        assert context["prompt_hashes"] == used_bundle.hashes()
        assert context["profile_id"] == "single_agent_equal_budget_v1"
        assert context["model_provider"] == "mock"


def test_frozen_settings(client, monkeypatch) -> None:
    from forecastlab.providers import factory
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import BenchmarkExperiment, ForecastRun
    from forecastlab_api.worker import drain_jobs

    imported = _import(client, [_row(question="Will frozen settings series happen?")], "frozen_settings")
    created = client.post(
        "/api/experiments",
        json={"dataset_id": imported["dataset_id"], "profile_ids": ["single_agent_equal_budget_v1"]},
    )
    experiment_id = created.json()["id"]
    with SessionLocal() as session:
        experiment = session.get(BenchmarkExperiment, experiment_id)
        assert experiment is not None
        frozen_provider = experiment.model_provider
        frozen_model = experiment.model_name
        frozen_base = experiment.model_base_url
        frozen_search = experiment.search_provider
        frozen_timeout = experiment.model_timeout_seconds
        frozen_policy = experiment.evidence_policy

    client.put(
        "/api/settings",
        json={
            "model_provider": "openai_compatible",
            "model_name": "mutated-model",
            "model_base_url": "https://mutated.example/v1",
            "search_provider": "tavily",
            "model_timeout_seconds": 5,
            "model_api_key": "sk-mutated-key-should-not-switch-providers",
            "search_api_key": "tvly-mutated-key",
        },
    )

    seen: list[dict] = []
    original = factory.build_model_provider

    def spy(**kwargs):
        seen.append(kwargs)
        return original(**kwargs)

    def boom(*_args, **_kwargs):
        raise AssertionError("Settings were re-resolved during benchmark execution")

    monkeypatch.setattr(factory, "build_model_provider", spy)
    monkeypatch.setattr("forecastlab_api.pipeline.build_model_provider", spy)
    monkeypatch.setattr("forecastlab.execution.resolve_execution_context", boom)
    monkeypatch.setattr("forecastlab_api.pipeline.resolve_execution_context", boom)
    monkeypatch.setattr("forecastlab_api.pipeline.resolve_for_question", boom)

    drain_jobs(max_steps=20)
    summary = client.get(f"/api/experiments/{experiment_id}/summary").json()
    assert summary["progress"]["completed_tasks"] == 1
    assert summary["model_provider"] == frozen_provider == "mock"
    assert summary["model_name"] == frozen_model
    assert summary["search_provider"] == frozen_search == "mock"
    assert seen
    assert seen[0]["timeout"] == frozen_timeout
    assert seen[0]["base_url"] == frozen_base
    assert seen[0]["model"] == frozen_model
    with SessionLocal() as session:
        run = session.query(ForecastRun).one()
        context = json.loads(run.execution_context_json)
        assert context["model_provider"] == "mock"
        assert context["search_provider"] == "mock"
        assert context["model_name"] != "mutated-model"
        assert context["model_base_url"] != "https://mutated.example/v1"
        assert context["evidence_policy"] == frozen_policy
        assert context["model_timeout_seconds"] == frozen_timeout
        assert "sk-mutated" not in json.dumps(context)


def test_shared_resolution_contract(client, monkeypatch) -> None:
    from forecastlab.engine import _ask_json
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import BenchmarkQuestion, Question
    from forecastlab_api.worker import drain_jobs

    imported = _import(client, [_row()], "shared_contract")
    created = client.post(
        "/api/experiments",
        json={
            "dataset_id": imported["dataset_id"],
            "profile_ids": ["single_agent_equal_budget_v1", "three_track_equal_budget_v1"],
        },
    )
    experiment_id = created.json()["id"]

    def boom(*_args, **_kwargs):
        raise AssertionError("operationalize model call")

    original_ask = _ask_json

    def guarded(model, budget, stage, prompt_name, *args, **kwargs):
        if prompt_name == "operationalize" or stage == "operationalize":
            raise AssertionError("operationalize model call")
        return original_ask(model, budget, stage, prompt_name, *args, **kwargs)

    monkeypatch.setattr("forecastlab_api.pipeline.operationalize_only", boom)
    monkeypatch.setattr("forecastlab.engine.operationalize_only", boom)
    monkeypatch.setattr("forecastlab.engine._ask_json", guarded)

    drain_jobs(max_steps=40)
    summary = client.get(f"/api/experiments/{experiment_id}/summary").json()
    assert summary["progress"]["completed_tasks"] == 2
    with SessionLocal() as session:
        item = session.query(BenchmarkQuestion).filter_by(dataset_id=imported["dataset_id"]).one()
        questions = session.query(Question).filter(Question.notes == f"benchmark:{item.id}:{experiment_id}").all()
        assert len(questions) == 1
        contract = questions[0].contract
        assert contract is not None
        assert contract.exact_yes == item.exact_yes == "Yes if the fixture series exceeds 1.0"
        assert contract.exact_no == item.exact_no
        assert contract.authoritative_source == item.authoritative_source
        assert questions[0].runs
        assert {run.profile_id for run in questions[0].runs} == {
            "single_agent_equal_budget_v1",
            "three_track_equal_budget_v1",
        }
        assert {run.question_id for run in questions[0].runs} == {questions[0].id}


def test_dataset_same_question_two_datasets(client) -> None:
    from forecastlab.hashing import import_hash
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.experiments import canonical_benchmark_row
    from forecastlab_api.models import BenchmarkQuestion

    shared = _row(question="Will the same question appear in two datasets?")
    first = _import(client, [shared], "dataset_version_a")
    second = _import(client, [shared, _row(question="Will a second versioned row appear?")], "dataset_version_b")
    assert first["dataset_id"] != second["dataset_id"]
    digest = import_hash(canonical_benchmark_row(shared, is_synthetic=True))
    with SessionLocal() as session:
        rows = session.query(BenchmarkQuestion).filter_by(import_hash=digest).all()
        assert len(rows) == 2
        assert {row.dataset_id for row in rows} == {first["dataset_id"], second["dataset_id"]}


def test_dataset_duplicate_inside_one_dataset(client) -> None:
    shared = _row(question="Will an in-dataset duplicate be counted?")
    imported = _import(client, [shared, shared], "dataset_internal_dup")
    assert imported["created"] == 1
    assert imported["duplicates"] == 1


def test_dataset_mixed_synthetic_and_real_rejected(client) -> None:
    response = client.post(
        "/api/benchmarks/import",
        files={
            "file": (
                "mix.csv",
                BytesIO(
                    _csv(
                        [
                            _row(is_synthetic="true"),
                            _row(question="Will a real row mix?", is_synthetic="false", exact_yes="Yes", exact_no="No"),
                        ]
                    )
                ),
                "text/csv",
            )
        },
    )
    assert response.status_code == 400
    assert "mixed_synthetic_and_real" in str(response.json())


def test_dataset_invalid_outcome_rejected(client) -> None:
    response = client.post(
        "/api/benchmarks/import",
        files={"file": ("bad_outcome.csv", BytesIO(_csv([_row(outcome=2)])), "text/csv")},
    )
    assert response.status_code == 400
    assert "invalid_outcome" in str(response.json())


def test_dataset_forecast_after_resolution_rejected(client) -> None:
    response = client.post(
        "/api/benchmarks/import",
        files={
            "file": (
                "bad_dates.csv",
                BytesIO(_csv([_row(forecast_date="2026-02-01", resolution_date="2026-01-01")])),
                "text/csv",
            )
        },
    )
    assert response.status_code == 400
    assert "forecast_date_after_resolution_date" in str(response.json())


def test_real_import_requires_exact_yes_and_no(client) -> None:
    row = _row(is_synthetic="false", exact_yes="", exact_no="")
    response = client.post(
        "/api/benchmarks/import",
        files={"file": ("real.csv", BytesIO(_csv([row])), "text/csv")},
        data={"name": "real_missing_contract"},
    )
    assert response.status_code == 400
    assert "exact_yes_required" in str(response.json())


def test_dataset_hash_ignores_row_order_and_changes_with_exact_yes() -> None:
    left = [_row(question="Will order A happen?"), _row(question="Will order B happen?")]
    right = list(reversed(left))
    assert dataset_hash_for_rows(left, is_synthetic=True) == dataset_hash_for_rows(right, is_synthetic=True)
    mutated = [dict(left[0], exact_yes="Yes if a different contract is stored"), left[1]]
    assert dataset_hash_for_rows(left, is_synthetic=True) != dataset_hash_for_rows(mutated, is_synthetic=True)


def test_create_experiment_uses_equal_budget_defaults(client) -> None:
    datasets = client.get("/api/datasets").json()["datasets"]
    synth = next(item for item in datasets if item["name"] == "synthetic_fixtures_v1")
    created = client.post("/api/experiments", json={"dataset_id": synth["id"]})
    assert created.status_code == 200
    assert created.json()["total_tasks"] == synth["question_count"] * 2
    from forecastlab_api.db import SessionLocal
    from forecastlab_api.models import BenchmarkExperiment, BenchmarkProfileSnapshot

    with SessionLocal() as session:
        experiment = session.get(BenchmarkExperiment, created.json()["id"])
        assert experiment is not None
        assert json.loads(experiment.profile_ids_json) == list(DEFAULT_EXPERIMENT_PROFILES)
        snapshots = session.query(BenchmarkProfileSnapshot).filter_by(experiment_id=experiment.id).all()
        assert {item.profile_id for item in snapshots} == set(DEFAULT_EXPERIMENT_PROFILES)
        by_id = {item.profile_id: ForecastProfile.model_validate(json.loads(item.effective_profile_json)) for item in snapshots}
        assert by_id["single_agent_equal_budget_v1"].tracks == ["single_agent"]
        assert by_id["three_track_equal_budget_v1"].tracks == ["base_rate", "current_evidence", "skeptic"]
        assert all("api_key" not in item.execution_context_json.lower() for item in snapshots)
        assert all("api_key" not in item.source_profile_json.lower() for item in snapshots)
