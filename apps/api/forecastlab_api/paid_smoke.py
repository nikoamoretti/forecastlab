"""Explicitly opted-in live model-plus-search smoke forecast."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.environment import working_tree_dirty
from forecastlab.gitinfo import ROOT
from forecastlab.profiles import load_profile
from forecastlab.schemas import ResolutionContract
from forecastlab.timeutil import utcnow
from forecastlab_api.models import EvidenceItem, ForecastRun, ForecastRunAttempt, ProviderCallLedger, Question
from forecastlab_api.persist import save_contract
from forecastlab_api.pipeline import execute_run, start_run
from forecastlab_api.secrets import load_secrets

SMOKE_PROFILE_ID = "live_smoke_v1"
SMOKE_QUESTION = (
    "Will the US civilian unemployment rate (U-3, seasonally adjusted) be at or above 5.0 percent "
    "for any month whose official BLS release date is on or before 30 June 2027?"
)
SMOKE_CONTRACT = ResolutionContract(
    exact_yes=(
        "The US Bureau of Labor Statistics U-3 unemployment rate, seasonally adjusted, "
        "is reported at or above 5.0% for any month whose official release date is on or before 30 June 2027."
    ),
    exact_no=(
        "No BLS U-3 seasonally adjusted monthly reading at or above 5.0% is published with a "
        "release date on or before 30 June 2027."
    ),
    resolution_deadline=datetime.fromisoformat("2027-07-15T00:00:00+00:00"),
    authoritative_source="https://www.bls.gov/news.release/empsit.toc.htm",
    fallback_sources=["https://fred.stlouisfed.org/series/UNRATE"],
    geography="United States",
    units="percent, seasonally adjusted U-3",
    ambiguity_notes="First official monthly release binds unless BLS restates that first-release figure before the deadline.",
    cancellation_conditions="Invalidate if BLS discontinues U-3.",
    resolver_risk_notes="Do not substitute U-6 or a three-month average.",
)
ABSENT_MESSAGE = "Paid live smoke test not executed because explicit opt-in was absent."
CREDENTIALS_MISSING_MESSAGE = "credentials_missing"
FIXTURE_HOSTS = ("fixtures.forecastlab.local",)


def credentials_ready(secrets: dict[str, Any] | None = None) -> bool:
    data = secrets if secrets is not None else load_secrets()
    model_ok = bool(data.get("model_api_key")) and str(data.get("model_provider") or "") not in {"", "mock", "demo"}
    search_ok = bool(data.get("search_api_key")) and str(data.get("search_provider") or "") not in {"", "mock", "demo"}
    return model_ok and search_ok


def opted_in() -> bool:
    return os.environ.get("FORECASTLAB_RUN_PAID_SMOKE") == "1"


def _session_factory() -> Callable[[], Session]:
    from forecastlab_api.db import SessionLocal

    return SessionLocal


def execute_live_smoke(*, session_factory: Callable[[], Session] | None = None) -> dict[str, Any]:
    if working_tree_dirty():
        raise RuntimeError("working_tree_dirty")
    if not (ROOT / "uv.lock").exists():
        raise RuntimeError("python_lockfile_required")
    secrets = load_secrets()
    if not credentials_ready(secrets):
        raise RuntimeError("credentials_missing")
    expected_model = str(secrets.get("model_provider"))
    expected_search = str(secrets.get("search_provider"))
    profile = load_profile(SMOKE_PROFILE_ID)
    factory = session_factory or _session_factory()
    with factory() as session:
        question = Question(
            id=str(uuid.uuid4()),
            original_text=SMOKE_QUESTION,
            notes="Opt-in live smoke. Manually specified resolution contract.",
            status="draft",
            requested_mode="live",
            requested_profile_id=SMOKE_PROFILE_ID,
            is_benchmark=False,
        )
        session.add(question)
        session.flush()
        save_contract(session, question, SMOKE_CONTRACT)
        run = start_run(
            session,
            question=question,
            profile_id=SMOKE_PROFILE_ID,
            mode="live",
            as_of=None,
            enqueue=False,
        )
        session.commit()
        execute_run(session, run)
        session.refresh(run)
        payload = _audit(session, run, expected_model=expected_model, expected_search=expected_search, profile=profile)
        session.commit()
        return payload


def _audit(session: Session, run: ForecastRun, *, expected_model: str, expected_search: str, profile) -> dict[str, Any]:
    from forecastlab.schemas import ForecastProfile

    assert isinstance(profile, ForecastProfile)
    attempts = session.scalars(select(ForecastRunAttempt).where(ForecastRunAttempt.run_id == run.id)).all()
    ledger = session.scalars(select(ProviderCallLedger).where(ProviderCallLedger.run_id == run.id)).all()
    evidence = session.scalars(select(EvidenceItem).where(EvidenceItem.run_id == run.id)).all()
    context = {}
    if run.execution_context_json:
        try:
            context = json.loads(run.execution_context_json)
        except json.JSONDecodeError:
            context = {}
    model_provider = context.get("model_provider") or expected_model
    search_provider = context.get("search_provider") or expected_search
    if context.get("model_is_mock") or context.get("search_is_mock") or context.get("fixture_evidence_used") or run.fixture_evidence_used:
        raise RuntimeError("mock_or_fixture_evidence_used")
    if model_provider != expected_model:
        raise RuntimeError(f"vendor_identity_incorrect:{model_provider}")
    if search_provider != expected_search:
        raise RuntimeError(f"search_identity_incorrect:{search_provider}")
    if any(any(host in (item.url or "") for host in FIXTURE_HOSTS) for item in evidence):
        raise RuntimeError("mock_or_fixture_evidence_used")
    if run.status not in {"completed", "failed"}:
        raise RuntimeError(f"run_not_terminal:{run.status}")
    if not ledger:
        raise RuntimeError("provider_ledger_empty")
    if any(item.provider_type == "model" and item.provider != expected_model for item in ledger):
        raise RuntimeError("vendor_identity_incorrect")
    if any(item.provider_type == "search" and item.provider != expected_search for item in ledger):
        raise RuntimeError("search_identity_incorrect")
    if not any(item.provider_type == "model" and item.status in {"succeeded", "failed"} for item in ledger):
        raise RuntimeError("completed_provider_request_missing_from_ledger")
    if not any(item.provider_type == "search" and item.status in {"succeeded", "failed"} for item in ledger):
        raise RuntimeError("completed_provider_request_missing_from_ledger")
    ceiling = float(profile.max_estimated_cost_usd)
    total = float(run.total_cost_usd or run.cost_usd or 0)
    if total > ceiling + 1e-9:
        raise RuntimeError("lifetime_cost_exceeds_ceiling")
    from forecastlab_api.models import ForecastVersion

    version = session.scalars(select(ForecastVersion).where(ForecastVersion.run_id == run.id)).first()
    return {
        "run_id": run.id,
        "status": run.status,
        "probability": version.ensemble_probability if version is not None else None,
        "model_provider": model_provider,
        "search_provider": search_provider,
        "model_cost_usd": run.model_cost_usd,
        "search_cost_usd": run.search_cost_usd,
        "failed_attempt_cost_usd": run.failed_attempt_cost_usd,
        "total_cost_usd": total,
        "cost_source": run.cost_source,
        "run_attempt_count": len(attempts),
        "provider_request_count": len(ledger),
        "ledger": [
            {
                "id": item.id,
                "stage": item.stage,
                "provider_type": item.provider_type,
                "provider": item.provider,
                "physical_attempt_number": item.physical_attempt_number,
                "status": item.status,
                "reserved_input_tokens": item.reserved_input_tokens,
                "reserved_output_tokens": item.reserved_output_tokens,
                "reserved_cost_usd": item.reserved_cost_usd,
                "actual_prompt_tokens": item.actual_prompt_tokens,
                "actual_completion_tokens": item.actual_completion_tokens,
                "actual_cost_usd": item.actual_cost_usd,
                "cost_source": item.cost_source,
            }
            for item in ledger
        ],
        "checked_at": utcnow().isoformat(),
    }


def print_result(payload: dict[str, Any]) -> None:
    print(f"status={payload['status']}")
    print(f"probability={payload['probability']}")
    print(f"model_provider={payload['model_provider']}")
    print(f"search_provider={payload['search_provider']}")
    print(f"model_cost_usd={payload['model_cost_usd']}")
    print(f"search_cost_usd={payload['search_cost_usd']}")
    print(f"failed_attempt_cost_usd={payload['failed_attempt_cost_usd']}")
    print(f"total_cost_usd={payload['total_cost_usd']}")
    print("ledger:")
    for row in payload["ledger"]:
        print(row)


def main() -> int:
    if not opted_in():
        print(ABSENT_MESSAGE)
        return 0
    if not credentials_ready():
        print(CREDENTIALS_MISSING_MESSAGE)
        return 2
    payload = execute_live_smoke()
    print_result(payload)
    return 0
