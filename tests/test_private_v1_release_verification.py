from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from scripts.verify_private_v1_release import (
    SENSITIVE_ENV_KEYS,
    VerificationError,
    _preflight,
    _safe_failure_summary,
    _scan_generated_json,
    _scan_tracked_source,
    _substantive_checkout_result,
    _tracked_files,
    normalize_release_result,
    normalized_result_hash,
    substantive_differences,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(autouse=True)
def _clean_release_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        *SENSITIVE_ENV_KEYS,
        "FORECASTLAB_MODEL_PROVIDER",
        "FORECASTLAB_SEARCH_PROVIDER",
        "FORECASTLAB_DATABASE_URL",
        "FORECASTLAB_DATA_DIR",
        "FORECASTLAB_CREDENTIALS_PATH",
    ):
        monkeypatch.delenv(key, raising=False)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def _minimal_repository(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    tracked = {
        "uv.lock": "locked\n",
        "pyproject.toml": "[project]\nname='fixture'\n",
        "apps/web/package-lock.json": "{}\n",
        "configs/forecast_profiles/graph_forecaster_v1.yaml": "id: graph_forecaster_v1\n",
        "configs/forecast_profiles/graph_live_smoke_v1.yaml": "id: graph_live_smoke_v1\n",
        "fixtures/release/private_v1_verification_v1.json": (
            '{"fixture_label":"synthetic_release_verification_only"}\n'
        ),
        "prompts/scenario_synthesis.txt": "PROMPT_ID: scenario_synthesis:v1\n",
        "alembic.ini": "[alembic]\n",
        "alembic/env.py": "# fixture\n",
        "alembic/versions/0001.py": "revision='0001'\n",
    }
    for relative, content in tracked.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(source, "init")
    _git(source, "config", "user.name", "ForecastLab Test")
    _git(source, "config", "user.email", "forecastlab-test@example.invalid")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "fixture")
    return source, _git(source, "rev-parse", "HEAD")


def test_release_preflight_accepts_only_clean_exact_source(tmp_path: Path) -> None:
    source, source_sha = _minimal_repository(tmp_path)
    result = _preflight(source, source_sha, tmp_path / "output")
    assert result["commit_sha"] == source_sha
    assert result["prompt_files"] == ["prompts/scenario_synthesis.txt"]

    (source / "uv.lock").write_text("changed\n", encoding="utf-8")
    with pytest.raises(VerificationError, match="tracked_source_worktree_is_dirty"):
        _preflight(source, source_sha, tmp_path / "output")


def test_release_preflight_refuses_sha_lock_and_repository_output(tmp_path: Path) -> None:
    source, source_sha = _minimal_repository(tmp_path)
    with pytest.raises(VerificationError, match="source_sha_mismatch"):
        _preflight(source, "0" * 40, tmp_path / "output")
    with pytest.raises(VerificationError, match="output_directory_must_be_outside"):
        _preflight(source, source_sha, source / "artifacts")

    _git(source, "rm", "uv.lock")
    _git(source, "commit", "-m", "remove lock")
    new_sha = _git(source, "rev-parse", "HEAD")
    with pytest.raises(VerificationError, match="required_tracked_files_missing"):
        _preflight(source, new_sha, tmp_path / "output")


def test_release_preflight_refuses_live_credentials_and_database_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, source_sha = _minimal_repository(tmp_path)
    monkeypatch.setenv("FORECASTLAB_MODEL_PROVIDER", "openai")
    with pytest.raises(VerificationError, match="live_provider_configuration_refused"):
        _preflight(source, source_sha, tmp_path / "output")
    monkeypatch.setenv("FORECASTLAB_MODEL_PROVIDER", "mock")
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    with pytest.raises(VerificationError, match="credential_environment_present"):
        _preflight(source, source_sha, tmp_path / "output")
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("FORECASTLAB_DATABASE_URL", "sqlite:///live.db")
    with pytest.raises(VerificationError, match="external_database_configuration_refused"):
        _preflight(source, source_sha, tmp_path / "output")


def test_release_normalization_is_deterministic_and_preserves_substantive_differences() -> None:
    left = {
        "id": "11111111-1111-4111-8111-111111111111",
        "created_at": "2026-08-26T12:00:00+00:00",
        "probability": 0.43,
        "nodes": ["base_rate", "driver"],
    }
    right = {
        "id": "22222222-2222-4222-8222-222222222222",
        "created_at": "2026-08-26T12:01:00+00:00",
        "probability": 0.43,
        "nodes": ["base_rate", "driver"],
    }
    assert normalize_release_result(left) == normalize_release_result(right)
    assert normalized_result_hash(left) == normalized_result_hash(right)
    changed = {**right, "probability": 0.44}
    assert substantive_differences(
        normalize_release_result(left),
        normalize_release_result(changed),
    ) == ["$.probability"]

    sqlite_timestamp = {**left, "created_at": "2026-08-26 12:00:00.123456"}
    assert normalize_release_result(sqlite_timestamp)["created_at"] == "<timestamp>"


def test_release_checkout_identity_is_not_a_substantive_product_result() -> None:
    first = {"checkout_index": 1, "probability": 0.43, "status": "completed"}
    second = {"checkout_index": 2, "probability": 0.43, "status": "completed"}
    assert _substantive_checkout_result(first) == _substantive_checkout_result(second)


def test_release_generated_output_scan_never_exposes_secret_values() -> None:
    safe = {"provider": "mock", "model_api_key_set": False, "cost_usd": 0.0}
    assert _scan_generated_json("safe", safe) == []
    unsafe = {"nested": {"api_key": "redacted"}}
    matches = _scan_generated_json("unsafe", unsafe)
    assert len(matches) == 1
    assert "redacted" not in matches[0]


def test_release_failure_summary_redacts_secret_bearing_output(tmp_path: Path) -> None:
    output = "Error: provider returned " + "sk-" + ("x" * 24)
    summary = _safe_failure_summary(output, cwd=tmp_path)
    assert summary == "diagnostic_redacted_secret_pattern"
    assert "sk-" not in summary


def test_release_tracked_source_scan_only_accepts_hash_pinned_fixtures() -> None:
    source = Path(__file__).resolve().parents[1]
    result = _scan_tracked_source(source, _tracked_files(source))
    assert result["suspected_secret_count"] == 0
    assert result["known_synthetic_fixture_match_count"] == 11


def test_release_database_gate_migrates_fresh_and_legacy_with_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from forecastlab_api.config import settings
    from forecastlab_api.private_v1_release_verification import run_database_gate

    monkeypatch.setattr(settings, "data_dir", tmp_path / "migration-locks")
    result = run_database_gate(
        fresh_database=tmp_path / "fresh.db",
        legacy_database=tmp_path / "legacy.db",
    )
    assert result["fresh"]["integrity_check"] == "ok"
    assert result["legacy_upgrade"]["legacy_marker_row_preserved"] is True
    assert result["schema_parity"]["passed"] is True
    assert result["schema_parity"]["raw_schema_hashes_equal"] is False
    assert (
        result["schema_parity"]["documented_default_metadata_difference_count"]
        == 21
    )
    assert result["schema_parity"]["differences"] == []
    assert result["fresh"]["alembic_revision"] == "20260922_0033"


def test_release_positive_and_negative_journeys_cover_all_private_v1_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from forecastlab_api import private_v1_release_verification as release
    from forecastlab_api.config import settings
    from forecastlab_api.db import configure_sqlite_engine
    from forecastlab_api.migrate import apply_schema

    database = tmp_path / "journeys.db"
    database_url = f"sqlite:///{database}"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("FORECASTLAB_MODEL_PROVIDER", "mock")
    monkeypatch.setenv("FORECASTLAB_SEARCH_PROVIDER", "mock")
    monkeypatch.setattr(settings, "database_url", database_url)
    monkeypatch.setattr(settings, "data_dir", data_dir)
    apply_schema(database_url)
    engine = create_engine(
        database_url,
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    configure_sqlite_engine(engine)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(release, "SessionLocal", session_factory)
    try:
        result = release.run_release_journeys(source_sha="a" * 40)
    finally:
        engine.dispose()

    positive = result["positive_journey"]
    assert positive["status"] == "completed"
    assert positive["profile_id"] == "graph_forecaster_v1"
    assert positive["profile_version"] == 9
    assert positive["graph"]["node_count"] == 5
    assert len(positive["research_plan"]["selected_node_ids"]) == 3
    assert positive["evidence"]["claim_count"] == 3
    assert len(positive["node_runs"]) == 3
    assert positive["evidence_sufficiency"]["status"] == "passed"
    assert positive["material_node_coverage"]["status"] == "passed"
    assert positive["scenario_synthesis"]["status"] == "passed"
    assert {item["kind"] for item in positive["scenario_synthesis"]["scenarios"]} == {
        "base_case",
        "yes_case",
        "no_case",
    }
    assert positive["aggregation"]["method"] == "relationship_mass_conserving_log_odds_v1"
    assert 0.0 < positive["final_probability"] < 1.0
    assert positive["invariance"]["probability_unchanged"] is True
    assert positive["invariance"]["mass_conserved"] is True
    assert positive["invariance"]["omitted_node_probability_created"] is False
    assert positive["provider_audit"]["cost_usd"] == 0.0
    assert positive["provider_audit"]["all_ledgers_terminal"] is True
    assert positive["provider_audit"]["all_providers_mock"] is True

    negative = result["negative_journey"]
    assert negative["status"] == "failed"
    assert negative["error_stage"] == "evidence_sufficiency"
    assert negative["evidence_sufficiency"]["status"] == "failed"
    assert negative["aggregation"]["id"] is None
    assert negative["forecast_version_id"] is None
    assert negative["scenario_synthesis"] is None
    assert negative["final_probability"] is None
    assert len(negative["node_runs"]) == 3
    assert negative["provider_audit"]["cost_usd"] == 0.0
    assert result["database"] == {
        "integrity_check": "ok",
        "foreign_key_violation_count": 0,
        "active_job_count": 0,
        "nonterminal_ledger_count": 0,
        "nonterminal_attempt_count": 0,
    }


def test_release_fixture_is_explicitly_synthetic_and_contains_no_outcome() -> None:
    fixture_path = (
        Path(__file__).parents[1]
        / "fixtures"
        / "release"
        / "private_v1_verification_v1.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert payload["fixture_label"] == "synthetic_release_verification_only"
    assert "outcome" not in payload
    assert len(payload["nodes"]) == 5
    assert payload["selected_node_keys"] == [
        "base_rate",
        "current_driver",
        "adversarial",
    ]
