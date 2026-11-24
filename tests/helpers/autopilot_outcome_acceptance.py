"""Isolated Autopilot outcome-acceptance seed and post-browser database checks.

Expected Brier and log-loss numbers are computed here with the elementary
binary formulas. This module must not import production scoring functions.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

HELPERS_DIR = Path(__file__).resolve().parent
TESTS_DIR = HELPERS_DIR.parent
REPO_ROOT = TESTS_DIR.parent
SYNTHESIZED_OBSERVATIONS = (
    TESTS_DIR / "fixtures" / "autopilot" / "synthesized_july_2026_cpi_threshold_observations.json"
)
OFFICIAL_CPI_PDF = TESTS_DIR / "fixtures" / "official_releases" / "cpi_08122026.pdf"
PRODUCTION_SQLITE = (REPO_ROOT / "data" / "forecastlab.db").resolve()

SEED_NOW = datetime(2026, 8, 5, 20, tzinfo=UTC)
RELEASE_AT = datetime(2026, 8, 12, 12, 30, tzinfo=UTC)
REFRESH_AT = SEED_NOW + timedelta(days=2)
COLLECT_AT = datetime(2026, 8, 12, 13, 30, tzinfo=UTC)
FROZEN_PROBABILITY = 0.6
FROZEN_OUTCOME = 1
CORRECTED_OUTCOME = 0
OFFICIAL_DOL_URL = "https://www.dol.gov/newsroom/economicdata/cpi_08122026.pdf"
CORRECTION_EVIDENCE_URL = OFFICIAL_DOL_URL
FROZEN_CALENDAR_SOURCE = "frozen_july_2026_cpi_release"
FROZEN_CALENDAR_HASH = "frozen-july-2026-cpi-calendar-v1"
FROZEN_CALENDAR_QUOTE = (
    "SYNTHESIZED isolated-acceptance calendar: one July 2026 CPI first release "
    "at 8:30 a.m. ET on August 12, 2026. Not a live BLS schedule cache entry."
)
REPO_WEB_DIR = REPO_ROOT / "apps" / "web"
WEB_COPY_IGNORE_NAMES = {
    ".next",
    "node_modules",
    "test-results",
    "playwright-report",
    "blob-report",
    "e2e",
    "e2e-acceptance",
    ".turbo",
    ".vercel",
    ".git",
    "tsconfig.tsbuildinfo",
}

LIVE_ENV_KEYS = (
    "DATABASE_URL",
    "DATABASE_URL_UNPOOLED",
    "POSTGRES_URL",
    "POSTGRES_PRISMA_URL",
    "BLOB_READ_WRITE_TOKEN",
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "FRED_API_KEY",
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
    "CRON_SECRET",
    "VERCEL",
    "VERCEL_ENV",
    "VERCEL_OIDC_TOKEN",
    "VERCEL_URL",
    "FORECASTLAB_DATABASE_URL",
    "FORECASTLAB_DATABASE_DIRECT_URL",
    "FORECASTLAB_MODEL_API_KEY",
    "FORECASTLAB_SEARCH_API_KEY",
    "FORECASTLAB_FRED_API_KEY",
    "FORECASTLAB_GITHUB_CLIENT_ID",
    "FORECASTLAB_GITHUB_CLIENT_SECRET",
    "FORECASTLAB_SESSION_SECRET",
    "FORECASTLAB_INTERNAL_SECRET",
    "FORECASTLAB_BLOB_TOKEN",
    "FORECASTLAB_BLOB_STORE_ID",
    "FORECASTLAB_CREDENTIALS_PATH",
)

UTCNOW_PATCH_TARGETS = (
    "forecastlab.timeutil",
    "forecastlab.macro",
    "forecastlab_api.autopilot",
    "forecastlab_api.autopilot_store",
    "forecastlab_api.autopilot_outcomes",
    "forecastlab_api.models",
    "forecastlab_api.jobs",
    "forecastlab_api.personal_forecasts",
    "forecastlab_api.pipeline",
)


class ForbiddenDatabaseTarget(RuntimeError):
    pass


class OutcomeAcceptanceError(RuntimeError):
    pass


def independent_binary_scores(probability: float, outcome: int) -> tuple[float, float]:
    if outcome not in (0, 1):
        raise ValueError("outcome must be 0 or 1")
    brier = (probability - outcome) ** 2
    log_loss = -math.log(probability if outcome == 1 else 1.0 - probability)
    return brier, log_loss


def expected_initial_scores() -> dict[str, float]:
    brier, log_loss = independent_binary_scores(FROZEN_PROBABILITY, FROZEN_OUTCOME)
    return {"brier_score": brier, "log_loss": log_loss}


def expected_corrected_scores() -> dict[str, float]:
    brier, log_loss = independent_binary_scores(FROZEN_PROBABILITY, CORRECTED_OUTCOME)
    return {"brier_score": brier, "log_loss": log_loss}


def frozen_july_2026_cpi_releases():
    """One July 2026 / August 12 CPI event. Do not reuse the September test calendar."""
    from forecastlab.question_selection import ScheduledRelease

    return [
        ScheduledRelease(
            family="cpi",
            observation_period="2026-07",
            release_at=RELEASE_AT,
            source_url="https://www.bls.gov/schedule/2026/home.htm",
            source_hash=FROZEN_CALENDAR_HASH,
            checked_at=SEED_NOW,
            schedule_basis="isolated_acceptance_frozen_calendar_v1",
            quote=FROZEN_CALENDAR_QUOTE,
        )
    ]


def official_pdf_bytes() -> bytes:
    return OFFICIAL_CPI_PDF.read_bytes()


def official_pdf_sha256() -> str:
    return hashlib.sha256(official_pdf_bytes()).hexdigest()


def synthesized_observation_payload() -> dict[str, Any]:
    payload = json.loads(SYNTHESIZED_OBSERVATIONS.read_text(encoding="utf-8"))
    payload.pop("schema_version", None)
    payload.pop("synthetic", None)
    payload.pop("label", None)
    payload.pop("purpose", None)
    return payload


def local_sqlite_path(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    return Path(url.removeprefix("sqlite:///")).expanduser()


def assert_isolated_workspace(workspace: Path) -> Path:
    resolved = workspace.expanduser().resolve()
    root = REPO_ROOT.resolve()
    if resolved == root or root in resolved.parents:
        raise ForbiddenDatabaseTarget("acceptance workspace must be a private directory outside the repository")
    return resolved


def assert_isolated_database_url(database_url: str, workspace: Path, *, allow_existing: bool = False) -> Path:
    workspace = assert_isolated_workspace(workspace)
    if not database_url.startswith("sqlite:///"):
        raise ForbiddenDatabaseTarget("outcome acceptance uses a fresh isolated SQLite file")
    db_path = local_sqlite_path(database_url)
    if db_path is None:
        raise ForbiddenDatabaseTarget("outcome acceptance uses a fresh isolated SQLite file")
    resolved = db_path.resolve()
    if resolved == PRODUCTION_SQLITE:
        raise ForbiddenDatabaseTarget("refusing the production/local ForecastLab SQLite database")
    if "forecastlab.db" == resolved.name and resolved.parent.name == "data" and resolved.parent.parent == REPO_ROOT.resolve():
        raise ForbiddenDatabaseTarget("refusing the repository local database target")
    if workspace not in resolved.parents and resolved != workspace:
        raise ForbiddenDatabaseTarget("database file must stay inside the private acceptance workspace")
    if resolved.exists() and not allow_existing:
        raise ForbiddenDatabaseTarget("refusing an existing database file")
    return resolved


def sanitized_environ(
    *,
    workspace: Path,
    database_url: str,
    api_origin: str,
    web_origin: str,
    inherited: dict[str, str] | None = None,
) -> dict[str, str]:
    workspace = assert_isolated_workspace(workspace)
    assert_isolated_database_url(database_url, workspace, allow_existing=True)
    source = inherited if inherited is not None else os.environ
    keep_prefixes = (
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LC_",
        "TERM",
        "CI",
        "GITHUB_",
        "NODE",
        "npm_",
        "NVM_",
        "VOLTA_",
        "FNM_",
    )
    keep_exact = {
        "TZ",
        "DISPLAY",
        "XAUTHORITY",
        "UV",
        "UV_PYTHON",
        "VIRTUAL_ENV",
        "NODE_ENV",
        "npm_config_cache",
        "PLAYWRIGHT_BROWSERS_PATH",
        "FORECASTLAB_ROOT",
    }
    env = {
        key: value
        for key, value in source.items()
        if key not in LIVE_ENV_KEYS
        and (
            key in keep_exact
            or any(key == prefix or key.startswith(prefix) for prefix in keep_prefixes)
        )
        and "KEY" not in key
        and "TOKEN" not in key
        and "SECRET" not in key
        and "PASSWORD" not in key
        and "CREDENTIAL" not in key
    }
    data_dir = workspace / "data"
    env.update(
        {
            "FORECASTLAB_ROOT": str(REPO_ROOT),
            "FORECASTLAB_ENV": "local",
            "FORECASTLAB_DATABASE_URL": database_url,
            "FORECASTLAB_DATA_DIR": str(data_dir),
            "FORECASTLAB_CREDENTIALS_PATH": str(data_dir / "local" / "credentials.json"),
            "FORECASTLAB_MODEL_PROVIDER": "mock",
            "FORECASTLAB_MODEL_NAME": "mock-forecast-v1",
            "FORECASTLAB_MODEL_API_KEY": "",
            "FORECASTLAB_SEARCH_PROVIDER": "mock",
            "FORECASTLAB_SEARCH_API_KEY": "",
            "FORECASTLAB_FRED_API_KEY": "",
            "FORECASTLAB_GITHUB_CLIENT_ID": "",
            "FORECASTLAB_GITHUB_CLIENT_SECRET": "",
            "FORECASTLAB_SESSION_SECRET": "outcome-acceptance-session",
            "FORECASTLAB_INTERNAL_SECRET": "outcome-acceptance-internal",
            "FORECASTLAB_BLOB_TOKEN": "",
            "FORECASTLAB_EMBEDDED_WORKER": "false",
            "FORECASTLAB_ALLOW_LOCAL_FIXTURES": "true",
            "FORECASTLAB_WEB_ORIGIN": web_origin,
            "FORECASTLAB_API_ORIGIN": api_origin,
            "FORECASTLAB_DEPLOYMENT_REVISION": "outcome-acceptance",
        }
    )
    return env


def _patch_utcnow(now: datetime) -> list[tuple[Any, str, Any]]:
    import importlib

    restored: list[tuple[Any, str, Any]] = []

    def current() -> datetime:
        return now

    for name in UTCNOW_PATCH_TARGETS:
        module = importlib.import_module(name)
        if hasattr(module, "utcnow"):
            restored.append((module, "utcnow", module.utcnow))
            module.utcnow = current  # type: ignore[method-assign]
    return restored


def _restore_utcnow(restored: list[tuple[Any, str, Any]]) -> None:
    for module, attr, original in restored:
        setattr(module, attr, original)


def snapshot_application_runtime() -> dict[str, Any]:
    import importlib

    from forecastlab_api import autopilot_outcomes as outcomes
    from forecastlab_api import db as db_module
    from forecastlab_api import main as main_mod
    from forecastlab_api.config import settings

    return {
        "settings": {name: getattr(settings, name) for name in type(settings).model_fields},
        "engine": db_module.engine,
        "SessionLocal": db_module.SessionLocal,
        "main_SessionLocal": main_mod.SessionLocal,
        "safe_get": outcomes.safe_get,
        "utcnows": {
            name: importlib.import_module(name).utcnow
            for name in UTCNOW_PATCH_TARGETS
            if hasattr(importlib.import_module(name), "utcnow")
        },
    }


def restore_application_runtime(snapshot: dict[str, Any]) -> None:
    import importlib

    from forecastlab_api import autopilot_outcomes as outcomes
    from forecastlab_api import db as db_module
    from forecastlab_api import main as main_mod
    from forecastlab_api.config import settings

    current_engine = db_module.engine
    if current_engine is not None and current_engine is not snapshot["engine"]:
        current_engine.dispose()
    for name, value in snapshot["settings"].items():
        setattr(settings, name, value)
    db_module.engine = snapshot["engine"]
    db_module.SessionLocal = snapshot["SessionLocal"]
    main_mod.SessionLocal = snapshot["main_SessionLocal"]
    outcomes.safe_get = snapshot["safe_get"]
    for name, original in snapshot["utcnows"].items():
        importlib.import_module(name).utcnow = original


def application_runtime_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left["settings"] == right["settings"]
        and left["engine"] is right["engine"]
        and left["SessionLocal"] is right["SessionLocal"]
        and left["main_SessionLocal"] is right["main_SessionLocal"]
        and left["safe_get"] is right["safe_get"]
        and left["utcnows"] == right["utcnows"]
    )


def _ignore_web_copy(_directory: str, names: list[str]) -> list[str]:
    return [name for name in names if name in WEB_COPY_IGNORE_NAMES or name.startswith(".env")]


def _assert_private_web_copy(web_dir: Path) -> Path:
    resolved = web_dir.expanduser().resolve()
    if resolved == REPO_WEB_DIR.resolve():
        raise OutcomeAcceptanceError("refusing_to_use_repository_web_app")
    return resolved


def materialize_isolated_web(workspace: Path) -> Path:
    workspace = assert_isolated_workspace(workspace)
    dest = workspace / "web"
    if dest.exists():
        raise OutcomeAcceptanceError("isolated_web_already_exists")
    shutil.copytree(REPO_WEB_DIR, dest, ignore=_ignore_web_copy, symlinks=False)
    if (dest / ".next").exists():
        raise OutcomeAcceptanceError("isolated_web_inherited_next_build")
    if (dest / "node_modules").exists():
        raise OutcomeAcceptanceError("isolated_web_inherited_node_modules")
    if not (dest / "package-lock.json").is_file():
        raise OutcomeAcceptanceError("isolated_web_missing_package_lock")
    if any(dest.glob(".env*")):
        raise OutcomeAcceptanceError("isolated_web_inherited_env_file")
    return dest


def install_isolated_web_dependencies(web_dir: Path, *, env: dict[str, str], log_path: Path) -> Path:
    resolved = _assert_private_web_copy(web_dir)
    node_modules = resolved / "node_modules"
    if node_modules.exists():
        raise OutcomeAcceptanceError("isolated_web_had_preexisting_node_modules")
    if (resolved / ".next").exists():
        raise OutcomeAcceptanceError("isolated_web_had_preexisting_next_build")
    if not (resolved / "package-lock.json").is_file():
        raise OutcomeAcceptanceError("isolated_web_missing_package_lock")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    install_env = {**env, "NEXT_TELEMETRY_DISABLED": "1"}
    ci = subprocess.run(
        ["npm", "ci"],
        cwd=resolved,
        env=install_env,
        text=True,
        capture_output=True,
        check=False,
    )
    log_path.write_text(f"{ci.stdout or ''}\n{ci.stderr or ''}", encoding="utf-8")
    if ci.returncode != 0:
        tail = (ci.stderr or ci.stdout or "")[-1500:]
        raise OutcomeAcceptanceError(f"npm_ci_failed:{tail}")
    if node_modules.is_symlink() or not node_modules.is_dir():
        raise OutcomeAcceptanceError("isolated_web_node_modules_not_local")
    ci_text = f"{ci.stdout or ''}\n{ci.stderr or ''}"
    if "vulnerabilit" in ci_text.casefold() or "npm warn" in ci_text.casefold() or "npm error" in ci_text.casefold():
        audit = subprocess.run(
            ["npm", "audit"],
            cwd=resolved,
            env=install_env,
            text=True,
            capture_output=True,
            check=False,
        )
        log_path.with_name("npm-audit.log").write_text(f"{audit.stdout or ''}\n{audit.stderr or ''}", encoding="utf-8")
    return node_modules


def repo_web_next_fingerprint() -> dict[str, Any]:
    next_dir = REPO_WEB_DIR / ".next"
    build_id = next_dir / "BUILD_ID"
    if not next_dir.exists():
        return {"exists": False, "build_id": None, "build_id_mtime_ns": None}
    return {
        "exists": True,
        "build_id": build_id.read_text(encoding="utf-8") if build_id.is_file() else None,
        "build_id_mtime_ns": build_id.stat().st_mtime_ns if build_id.is_file() else None,
    }


def build_isolated_web(web_dir: Path, *, env: dict[str, str], log_path: Path) -> Path:
    resolved = _assert_private_web_copy(web_dir)
    next_dir = resolved / ".next"
    node_modules = resolved / "node_modules"
    if next_dir.exists():
        raise OutcomeAcceptanceError("isolated_web_had_preexisting_next_build")
    if node_modules.is_symlink():
        raise OutcomeAcceptanceError("isolated_web_node_modules_symlink")
    if not node_modules.is_dir():
        raise OutcomeAcceptanceError("isolated_web_node_modules_missing")
    build = subprocess.run(
        ["npx", "next", "build"],
        cwd=resolved,
        env={**env, "NEXT_TELEMETRY_DISABLED": "1"},
        text=True,
        capture_output=True,
        check=False,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"{build.stdout or ''}\n{build.stderr or ''}", encoding="utf-8")
    if build.returncode != 0:
        tail = (build.stderr or build.stdout or "")[-1500:]
        raise OutcomeAcceptanceError(f"web_build_failed:{tail}")
    if not (next_dir / "BUILD_ID").is_file():
        raise OutcomeAcceptanceError("isolated_next_build_did_not_produce_BUILD_ID")
    return next_dir


def install_isolated_runtime(workspace: Path, *, allow_existing_database: bool = False):
    from forecastlab_api import db as db_module
    from forecastlab_api import main as main_mod
    from forecastlab_api.config import settings
    from forecastlab_api.db import configure_sqlite_engine
    from forecastlab_api.migrate import apply_migrations

    workspace = assert_isolated_workspace(workspace)
    data_dir = workspace / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "local").mkdir(parents=True, exist_ok=True)
    db_path = workspace / "acceptance.sqlite"
    database_url = f"sqlite:///{db_path}"
    assert_isolated_database_url(database_url, workspace, allow_existing=allow_existing_database)
    apply_migrations(database_url)
    engine = create_engine(database_url, future=True, connect_args={"check_same_thread": False, "timeout": 30})
    configure_sqlite_engine(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    settings.database_url = database_url
    settings.data_dir = data_dir
    settings.credentials_path = data_dir / "local" / "credentials.json"
    settings.embedded_worker = False
    settings.env = "local"
    settings.allow_local_fixtures = True
    settings.model_provider = "mock"
    settings.search_provider = "mock"
    settings.model_api_key = None
    settings.search_api_key = None
    settings.github_client_id = None
    settings.github_client_secret = None
    settings.blob_token = None
    db_module.engine = engine
    db_module.SessionLocal = SessionLocal
    main_mod.SessionLocal = SessionLocal
    return engine, SessionLocal, database_url


def _write_seed_credentials() -> None:
    from forecastlab_api.secrets import save_secrets

    save_secrets(
        {
            "model_provider": "openai",
            "model_name": "gpt-5-mini",
            "model_api_key": "test-only",
            "search_provider": "tavily",
            "search_api_key": "test-only",
            "max_cost_usd": 5,
        }
    )


def write_mock_runtime_credentials(workspace: Path) -> None:
    path = workspace / "data" / "local" / "credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "model_provider": "mock",
                "model_name": "mock-forecast-v1",
                "model_api_key": "",
                "search_provider": "mock",
                "search_api_key": "",
                "max_cost_usd": 5,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _finalize_run(session, run_id: str, probability: float | None, *, status: str, when: datetime) -> None:
    from forecastlab_api.models import ForecastRun, ForecastVersion, Job, PersonalForecast, Question

    run = session.get(ForecastRun, run_id)
    run.status = status
    run.started_at = when - timedelta(seconds=90)
    run.finished_at = when
    run.latency_ms = 90000
    run.progress_stage = "report"
    run.progress_message = "Forecast ready" if status == "completed" else "Forecast failed"
    personal = session.get(PersonalForecast, run_id)
    result = json.loads(personal.result_json or "{}")
    result["probability"] = probability
    personal.result_json = json.dumps(result)
    personal.outcome_status = "forecasted" if probability is not None else "execution_failed"
    if run.job_id:
        job = session.get(Job, run.job_id)
        if job:
            job.status = "completed" if status == "completed" else "failed"
            job.finished_at = when
            job.available_at = None
            job.lease_owner = None
            job.lease_expires_at = None
    if probability is not None:
        session.add(
            ForecastVersion(
                id=str(uuid.uuid4()),
                question_id=run.question_id,
                run_id=run.id,
                ensemble_probability=probability,
                created_at=when,
            )
        )
    question = session.get(Question, run.question_id)
    question.status = "resolved" if status == "completed" else question.status
    session.commit()


def seed_outcome_acceptance(workspace: Path) -> dict[str, Any]:
    import httpx

    from forecastlab.http_client import SafeResponse
    from forecastlab.macro import fetch_latest_macro_snapshots
    from forecastlab.question_selection import choose_questions
    from forecastlab_api import autopilot
    from forecastlab_api import autopilot_outcomes as outcomes
    from forecastlab_api.autopilot import PolicyConfig
    from forecastlab_api.autopilot_models import AutopilotRun, ManagedQuestion
    from forecastlab_api.autopilot_store import state
    from forecastlab_api.models import PersonalForecast
    from forecastlab_api.question_suggestions import SelectionSources

    runtime = snapshot_application_runtime()
    clock_patches: list[tuple[Any, str, Any]] = []
    try:
        engine, SessionLocal, database_url = install_isolated_runtime(workspace)
        clock = {"now": SEED_NOW}
        clock_patches = _patch_utcnow(clock["now"])

        def current() -> datetime:
            return clock["now"]

        for module, _attr, _original in clock_patches:
            module.utcnow = current  # type: ignore[method-assign]

        _write_seed_credentials()
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=synthesized_observation_payload()))
        ) as http:
            snapshots = fetch_latest_macro_snapshots(client=http)
        calendar_releases = frozen_july_2026_cpi_releases()
        if len(calendar_releases) != 1:
            raise OutcomeAcceptanceError("frozen_calendar_must_be_single_july_cpi_release")
        release = calendar_releases[0]
        if (
            release.family != "cpi"
            or release.observation_period != "2026-07"
            or release.release_at != RELEASE_AT
            or release.checked_at != SEED_NOW
        ):
            raise OutcomeAcceptanceError("frozen_calendar_incoherent")
        sources = SelectionSources(
            checked_at=SEED_NOW,
            releases=calendar_releases,
            snapshots=snapshots,
            documents={
                release.source_url: {"key": "sources/test", "sha256": "test", "content_type": "text/html"}
            },
        )
        candidates, _ = choose_questions(sources.releases, snapshots, now=SEED_NOW)
        candidate = next(item for item in candidates if item.macro.indicator == "cpi")
        if candidate.macro.observation_period != "2026-07":
            raise OutcomeAcceptanceError(f"unexpected_seed_period:{candidate.macro.observation_period}")
        if candidate.macro.release_at != RELEASE_AT:
            raise OutcomeAcceptanceError(f"unexpected_seed_release_at:{candidate.macro.release_at}")
        if candidate.macro.threshold != 3.0:
            raise OutcomeAcceptanceError(f"unexpected_seed_threshold:{candidate.macro.threshold}")

        with SessionLocal() as session:
            autopilot.approve_policy(session, PolicyConfig(), approved_by="outcome-acceptance")
            current_state = state(session)
            current_state.enabled = True
            session.commit()
            initial = autopilot.dispatch(session, candidate, sources, kind="initial")
            if initial is None:
                raise OutcomeAcceptanceError("initial_dispatch_failed")
            _finalize_run(session, initial.id, FROZEN_PROBABILITY, status="completed", when=SEED_NOW)

        clock["now"] = REFRESH_AT
        with SessionLocal() as session:
            refreshed = autopilot.dispatch(
                session, candidate, sources, kind="refresh", question_id=initial.question_id
            )
            if refreshed is None:
                raise OutcomeAcceptanceError("refresh_dispatch_failed")
            _finalize_run(session, refreshed.id, None, status="failed", when=REFRESH_AT)

        pdf = official_pdf_bytes()

        def fake_get(url, **_):
            if url == OFFICIAL_DOL_URL:
                return SafeResponse(
                    url=url,
                    final_url=url,
                    status_code=200,
                    content=pdf,
                    content_type="application/pdf",
                )
            return SafeResponse(
                url=url,
                final_url=url,
                status_code=404,
                content=b"not used",
                content_type="text/html",
            )

        outcomes.safe_get = fake_get  # type: ignore[method-assign]
        clock["now"] = COLLECT_AT
        with SessionLocal() as session:
            managed = session.get(ManagedQuestion, initial.question_id)
            proposal = outcomes.collect_outcome(session, managed)
            if proposal is None:
                raise OutcomeAcceptanceError("outcome_proposal_missing")
            payload = json.loads(proposal.payload_json)
            if payload.get("outcome") != FROZEN_OUTCOME:
                raise OutcomeAcceptanceError(f"unexpected_proposal_outcome:{payload.get('outcome')}")
            personal = session.get(PersonalForecast, managed.initial_run_id)
            result = json.loads(personal.result_json)
            if result.get("probability") != FROZEN_PROBABILITY:
                raise OutcomeAcceptanceError("seed_probability_changed")
            current_state = state(session)
            current_state.enabled = False
            current_state.qualification_enabled = False
            current_state.pause_reason = "Isolated outcome acceptance fixture; automatic spending disabled"
            session.commit()
            proposal_id = proposal.id
            question_id = managed.question_id
            artifact = payload["artifact"]
            initial_run = session.get(AutopilotRun, initial.id)

        write_mock_runtime_credentials(workspace)
        receipt = {
            "schema_version": "autopilot_outcome_acceptance_receipt_v1",
            "synthetic_observations": True,
            "official_release_fixture": str(OFFICIAL_CPI_PDF.relative_to(REPO_ROOT)),
            "calendar_source": FROZEN_CALENDAR_SOURCE,
            "calendar_quote": FROZEN_CALENDAR_QUOTE,
            "observation_period": "2026-07",
            "release_at": RELEASE_AT.isoformat(),
            "seed_now": SEED_NOW.isoformat(),
            "checked_at": SEED_NOW.isoformat(),
            "workspace": str(workspace),
            "database_url": database_url,
            "database_path": str(local_sqlite_path(database_url)),
            "question_id": question_id,
            "proposal_id": proposal_id,
            "initial_run_id": initial.id,
            "refresh_run_id": refreshed.id,
            "probability": FROZEN_PROBABILITY,
            "proposed_outcome": FROZEN_OUTCOME,
            "source_url": payload["source_url"],
            "source_sha256": artifact["sha256"],
            "source_byte_length": artifact["byte_length"],
            "official_pdf_sha256": official_pdf_sha256(),
            "expected_initial": expected_initial_scores(),
            "expected_corrected": expected_corrected_scores(),
            "latest_prerelease_scored_questions": 0,
            "cutoff": initial_run.cutoff.isoformat() if initial_run and initial_run.cutoff else None,
        }
        receipt_path = workspace / "seed-receipt.json"
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        receipt["receipt_path"] = str(receipt_path)
        return receipt
    finally:
        _restore_utcnow(clock_patches)
        restore_application_runtime(runtime)


@contextmanager
def open_acceptance_session(workspace: Path) -> Iterator[tuple[Any, Any]]:
    runtime = snapshot_application_runtime()
    engine, SessionLocal, _ = install_isolated_runtime(workspace, allow_existing_database=True)
    try:
        yield engine, SessionLocal
    finally:
        restore_application_runtime(runtime)


def load_receipt(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_post_browser_database(workspace: Path, receipt: dict[str, Any], browser_actions: dict[str, Any]) -> dict[str, Any]:
    from forecastlab_api.autopilot_models import QuestionAdjudication
    from forecastlab_api.models import PersonalForecast

    with open_acceptance_session(workspace) as (engine, SessionLocal):
        with SessionLocal() as session:
            rows = list(
                session.scalars(
                    select(QuestionAdjudication)
                    .where(QuestionAdjudication.question_id == receipt["question_id"])
                    .order_by(QuestionAdjudication.revision)
                )
            )
            if len(rows) != 2:
                raise OutcomeAcceptanceError(f"expected_two_adjudications:{len(rows)}")
            first, second = rows
            if first.id == second.id or first.revision != 1 or second.revision != 2:
                raise OutcomeAcceptanceError("adjudication_identity_or_revision_mismatch")
            if first.outcome != FROZEN_OUTCOME or second.outcome != CORRECTED_OUTCOME:
                raise OutcomeAcceptanceError("adjudication_outcomes_mismatch")
            confirm = browser_actions["confirm"]
            correction = browser_actions["correction"]
            if first.id != confirm["adjudication_id"] or first.outcome != confirm["outcome"]:
                raise OutcomeAcceptanceError("first_adjudication_changed_or_mismatched")
            if second.id != correction["adjudication_id"] or second.outcome != correction["outcome"]:
                raise OutcomeAcceptanceError("correction_adjudication_mismatch")
            personal = session.get(PersonalForecast, receipt["initial_run_id"])
            probability = json.loads(personal.result_json).get("probability")
            if probability != FROZEN_PROBABILITY:
                raise OutcomeAcceptanceError(f"saved_probability_changed:{probability}")
            first_snapshot = {
                "id": first.id,
                "revision": first.revision,
                "outcome": first.outcome,
                "evidence_json": first.evidence_json,
                "confirmed_by": first.confirmed_by,
            }

        with engine.begin() as connection:
            try:
                connection.execute(
                    text("UPDATE question_adjudications SET outcome = 0 WHERE id = :id"),
                    {"id": first_snapshot["id"]},
                )
            except Exception as exc:
                if "append" not in str(exc).casefold():
                    raise OutcomeAcceptanceError(f"update_rejected_for_unexpected_reason:{exc}") from exc
            else:
                raise OutcomeAcceptanceError("raw_update_was_not_rejected")

        with engine.begin() as connection:
            try:
                connection.execute(text("DELETE FROM question_adjudications"))
            except Exception as exc:
                if "append" not in str(exc).casefold():
                    raise OutcomeAcceptanceError(f"delete_rejected_for_unexpected_reason:{exc}") from exc
            else:
                raise OutcomeAcceptanceError("raw_delete_was_not_rejected")

        with SessionLocal() as session:
            rows = list(
                session.scalars(
                    select(QuestionAdjudication)
                    .where(QuestionAdjudication.question_id == receipt["question_id"])
                    .order_by(QuestionAdjudication.revision)
                )
            )
            if len(rows) != 2:
                raise OutcomeAcceptanceError("append_only_rollback_lost_rows")
            first = rows[0]
            if (
                first.id != first_snapshot["id"]
                or first.revision != first_snapshot["revision"]
                or first.outcome != first_snapshot["outcome"]
                or first.evidence_json != first_snapshot["evidence_json"]
                or first.confirmed_by != first_snapshot["confirmed_by"]
            ):
                raise OutcomeAcceptanceError("first_adjudication_changed_after_rejected_sql")
            personal = session.get(PersonalForecast, receipt["initial_run_id"])
            if json.loads(personal.result_json).get("probability") != FROZEN_PROBABILITY:
                raise OutcomeAcceptanceError("saved_probability_changed_after_rejected_sql")
            second_id = rows[1].id
        return {
            "adjudication_ids": [first_snapshot["id"], second_id],
            "revisions": [1, 2],
            "probability": FROZEN_PROBABILITY,
            "append_only_update_rejected": True,
            "append_only_delete_rejected": True,
        }
