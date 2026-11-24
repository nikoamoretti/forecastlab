from __future__ import annotations

import inspect
import math
import shutil
from pathlib import Path

import pytest
from tests.helpers import autopilot_outcome_acceptance as helper


def test_independent_scores_match_frozen_fixture_and_avoid_production_scoring() -> None:
    source = Path(inspect.getsourcefile(helper)).read_text(encoding="utf-8")
    assert "forecastlab.evaluation" not in source
    assert "test_question_selection" not in source
    initial = helper.expected_initial_scores()
    corrected = helper.expected_corrected_scores()
    assert initial["brier_score"] == pytest.approx(0.16)
    assert initial["log_loss"] == pytest.approx(-math.log(0.6))
    assert corrected["brier_score"] == pytest.approx(0.36)
    assert corrected["log_loss"] == pytest.approx(-math.log(0.4))
    assert helper.FROZEN_PROBABILITY == 0.6
    assert helper.FROZEN_OUTCOME == 1
    assert helper.CORRECTED_OUTCOME == 0


def test_frozen_calendar_is_july_cpi_on_august_12() -> None:
    releases = helper.frozen_july_2026_cpi_releases()
    assert len(releases) == 1
    release = releases[0]
    assert release.family == "cpi"
    assert release.observation_period == "2026-07"
    assert release.release_at == helper.RELEASE_AT
    assert release.checked_at == helper.SEED_NOW
    assert release.quote == helper.FROZEN_CALENDAR_QUOTE
    assert "September" not in release.quote
    september_source = Path(helper.TESTS_DIR / "test_question_selection.py").read_text(encoding="utf-8")
    assert "2026-09-04" in september_source
    helper_source = Path(inspect.getsourcefile(helper)).read_text(encoding="utf-8")
    assert "from tests.test_question_selection import releases" not in helper_source


def test_seed_refuses_repository_and_existing_local_database(tmp_path: Path) -> None:
    with pytest.raises(helper.ForbiddenDatabaseTarget, match="outside the repository"):
        helper.assert_isolated_workspace(helper.REPO_ROOT)
    with pytest.raises(helper.ForbiddenDatabaseTarget, match="production/local"):
        helper.assert_isolated_database_url(f"sqlite:///{helper.PRODUCTION_SQLITE}", tmp_path)
    with pytest.raises(helper.ForbiddenDatabaseTarget, match="isolated SQLite"):
        helper.assert_isolated_database_url("postgresql+psycopg://localhost/forecastlab", tmp_path)
    existing = tmp_path / "acceptance.sqlite"
    existing.write_bytes(b"occupied")
    with pytest.raises(helper.ForbiddenDatabaseTarget, match="existing database"):
        helper.assert_isolated_database_url(f"sqlite:///{existing}", tmp_path)


def test_sanitized_environment_strips_live_and_credential_values(tmp_path: Path) -> None:
    workspace = tmp_path / "outside"
    database_url = f"sqlite:///{workspace / 'acceptance.sqlite'}"
    env = helper.sanitized_environ(
        workspace=workspace,
        database_url=database_url,
        api_origin="http://127.0.0.1:5999",
        web_origin="http://127.0.0.1:5998",
        inherited={
            "PATH": "/usr/bin",
            "HOME": str(tmp_path),
            "DATABASE_URL": "postgresql://prod.example/forecastlab",
            "FORECASTLAB_DATABASE_URL": f"sqlite:///{helper.PRODUCTION_SQLITE}",
            "OPENAI_API_KEY": "sk-live",
            "FORECASTLAB_MODEL_API_KEY": "sk-live",
            "BLOB_READ_WRITE_TOKEN": "blob",
            "VERCEL": "1",
            "FORECASTLAB_GITHUB_CLIENT_SECRET": "oauth",
        },
    )
    assert "DATABASE_URL" not in env
    assert "OPENAI_API_KEY" not in env
    assert "VERCEL" not in env
    assert env["FORECASTLAB_DATABASE_URL"] == database_url
    assert env["FORECASTLAB_MODEL_PROVIDER"] == "mock"
    assert env["FORECASTLAB_MODEL_API_KEY"] == ""
    assert env["FORECASTLAB_GITHUB_CLIENT_SECRET"] == ""


def test_ordinary_playwright_suite_excludes_dedicated_acceptance() -> None:
    ordinary = (helper.REPO_WEB_DIR / "playwright.config.ts").read_text(encoding="utf-8")
    dedicated = (helper.REPO_WEB_DIR / "playwright.outcome-acceptance.config.ts").read_text(encoding="utf-8")
    assert 'testDir: "./e2e"' in ordinary
    assert 'testIgnore: "autopilot-outcome-acceptance.spec.ts"' in ordinary
    assert "e2e-acceptance" not in ordinary
    assert 'testDir: "./e2e"' in dedicated
    assert 'testMatch: "autopilot-outcome-acceptance.spec.ts"' in dedicated
    assert "cannot start without AUTOPILOT_ACCEPTANCE_RECEIPT" in dedicated
    assert "test.skip" not in dedicated
    assert (helper.REPO_WEB_DIR / "e2e" / "autopilot-outcome-acceptance.spec.ts").is_file()
    assert not (helper.REPO_WEB_DIR / "e2e-acceptance").exists()
    spec = (helper.REPO_WEB_DIR / "e2e" / "autopilot-outcome-acceptance.spec.ts").read_text(encoding="utf-8")
    assert "toHaveValue(receipt.source_url)" in spec
    assert "www.bls.gov/news.release/cpi.nr0.htm" not in spec
    runner = (helper.TESTS_DIR / "run_autopilot_outcome_acceptance.py").read_text(encoding="utf-8")
    assert "forecastlab-outcome-acceptance-receipt.json" not in runner
    assert '.next").is_dir()' not in runner
    assert "materialize_isolated_web" in runner
    assert "install_isolated_web_dependencies" in runner
    assert "--webpack" not in runner


def test_materialize_isolated_web_copies_source_without_shared_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "web-workspace"
    workspace.mkdir()
    dest = helper.materialize_isolated_web(workspace)
    assert dest == workspace / "web"
    assert dest.resolve() != helper.REPO_WEB_DIR.resolve()
    assert not (dest / ".next").exists()
    assert not (dest / "node_modules").exists()
    assert (dest / "package-lock.json").is_file()
    assert not list(dest.glob(".env*"))
    assert (dest / "app").is_dir()
    source = Path(inspect.getsourcefile(helper)).read_text(encoding="utf-8")
    assert "--webpack" not in source
    assert "symlink_to" not in source
    assert '["npm", "ci"]' in source
    assert '["npx", "next", "build"]' in source
    with pytest.raises(helper.OutcomeAcceptanceError, match="refusing_to_use_repository_web_app"):
        helper.build_isolated_web(helper.REPO_WEB_DIR, env={}, log_path=workspace / "build.log")

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        cwd = Path(kwargs["cwd"])
        if command[:2] == ["npm", "ci"]:
            (cwd / "node_modules").mkdir()
        result = type("Result", (), {"returncode": 0, "stdout": "added 10 packages\nfound 1 vulnerability", "stderr": ""})
        return result()

    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    helper.install_isolated_web_dependencies(dest, env={}, log_path=workspace / "npm-ci.log")
    assert calls[0][:2] == ["npm", "ci"]
    assert any(command[:2] == ["npm", "audit"] for command in calls)
    assert (dest / "node_modules").is_dir()
    assert not (dest / "node_modules").is_symlink()
    shutil.rmtree(dest / "node_modules")
    dest.joinpath("node_modules").symlink_to(helper.REPO_WEB_DIR / "node_modules", target_is_directory=True)
    with pytest.raises(helper.OutcomeAcceptanceError, match="isolated_web_node_modules_symlink"):
        helper.build_isolated_web(dest, env={}, log_path=workspace / "build.log")


def test_seed_uses_official_pdf_and_leaves_jobs_terminal(tmp_path: Path) -> None:
    workspace = tmp_path / "acceptance-workspace"
    workspace.mkdir()
    receipt = helper.seed_outcome_acceptance(workspace)
    assert receipt["proposed_outcome"] == 1
    assert receipt["probability"] == 0.6
    assert receipt["source_sha256"] == helper.official_pdf_sha256()
    assert receipt["latest_prerelease_scored_questions"] == 0
    assert receipt["observation_period"] == "2026-07"
    assert receipt["release_at"] == helper.RELEASE_AT.isoformat()
    assert receipt["calendar_source"] == helper.FROZEN_CALENDAR_SOURCE
    assert Path(receipt["receipt_path"]).parent == workspace
    with helper.open_acceptance_session(workspace) as (_engine, SessionLocal):
        from sqlalchemy import select

        from forecastlab_api.autopilot_models import OutcomeProposal, QuestionAdjudication
        from forecastlab_api.models import Job

        with SessionLocal() as session:
            assert session.scalar(select(QuestionAdjudication.id)) is None
            proposal = session.get(OutcomeProposal, receipt["proposal_id"])
            assert proposal is not None
            jobs = list(session.scalars(select(Job)))
            assert jobs
            assert {job.status for job in jobs} <= {"completed", "failed"}
            assert all(job.status != "pending" and job.status != "running" for job in jobs)


def test_seed_restores_runtime_and_safe_get_after_success_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from forecastlab.http_client import safe_get as production_safe_get
    from forecastlab_api import autopilot_outcomes as outcomes
    from forecastlab_api import db as db_module
    from forecastlab_api import main as main_mod
    from forecastlab_api.config import settings

    real_prior = helper.snapshot_application_runtime()
    settings.env = "sentinel-env"
    settings.database_url = "sqlite:///sentinel-prior.db"
    settings.data_dir = tmp_path / "prior-data"
    settings.credentials_path = tmp_path / "prior-credentials.json"
    settings.allow_local_fixtures = False
    settings.embedded_worker = True
    settings.model_provider = "sentinel-model"
    settings.model_name = "sentinel-name"
    settings.model_api_key = "sentinel-model-key"
    settings.search_provider = "sentinel-search"
    settings.search_api_key = "sentinel-search-key"
    settings.github_client_id = "sentinel-github-id"
    settings.github_client_secret = "sentinel-github-secret"
    settings.blob_token = "sentinel-blob"
    settings.session_secret = "sentinel-session"
    settings.internal_secret = "sentinel-internal"
    sentinel = helper.snapshot_application_runtime()
    assert sentinel["safe_get"] is production_safe_get
    assert outcomes.safe_get is production_safe_get

    def assert_sentinel_runtime() -> None:
        current = helper.snapshot_application_runtime()
        assert helper.application_runtime_matches(current, sentinel)
        assert settings.env == "sentinel-env"
        assert settings.database_url == "sqlite:///sentinel-prior.db"
        assert settings.data_dir == tmp_path / "prior-data"
        assert settings.credentials_path == tmp_path / "prior-credentials.json"
        assert settings.allow_local_fixtures is False
        assert settings.embedded_worker is True
        assert settings.model_provider == "sentinel-model"
        assert settings.model_api_key == "sentinel-model-key"
        assert settings.search_provider == "sentinel-search"
        assert settings.github_client_secret == "sentinel-github-secret"
        assert settings.blob_token == "sentinel-blob"
        assert db_module.engine is sentinel["engine"]
        assert db_module.SessionLocal is sentinel["SessionLocal"]
        assert main_mod.SessionLocal is sentinel["main_SessionLocal"]
        assert outcomes.safe_get is production_safe_get
        assert outcomes.safe_get is sentinel["safe_get"]
        assert current["utcnows"] == sentinel["utcnows"]

    try:
        success_workspace = tmp_path / "seed-success"
        success_workspace.mkdir()
        helper.seed_outcome_acceptance(success_workspace)
        assert_sentinel_runtime()

        def fail_after_fake_http(*_args, **_kwargs):
            raise helper.OutcomeAcceptanceError("forced_seed_failure")

        monkeypatch.setattr(outcomes, "collect_outcome", fail_after_fake_http)
        failure_workspace = tmp_path / "seed-failure"
        failure_workspace.mkdir()
        with pytest.raises(helper.OutcomeAcceptanceError, match="forced_seed_failure"):
            helper.seed_outcome_acceptance(failure_workspace)
        assert_sentinel_runtime()
    finally:
        helper.restore_application_runtime(real_prior)
        assert helper.application_runtime_matches(helper.snapshot_application_runtime(), real_prior)
