"""Explicitly opted-in live model-plus-search smoke forecast."""

from __future__ import annotations

import json
import math
import os
import sys
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.environment import working_tree_dirty
from forecastlab.execution import search_api_key_for
from forecastlab.gitinfo import ROOT
from forecastlab.hashing import redact_secrets
from forecastlab.profiles import load_profile
from forecastlab.schemas import ForecastProfile, ResolutionContract
from forecastlab.timeutil import utcnow
from forecastlab_api.migrate import apply_schema
from forecastlab_api.models import (
    EvidenceItem,
    ForecastRun,
    ForecastRunAttempt,
    ForecastVersion,
    ProviderCallLedger,
    Question,
)
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
INTERNAL_HOSTS = frozenset(
    {
        "fixtures.forecastlab.local",
        "127.0.0.1",
        "localhost",
        "::1",
    }
)
ALLOWED_LEDGER_STATUSES = frozenset({"succeeded", "failed"})
MIN_PROBABILITY = 0.01
MAX_PROBABILITY = 0.99


def credentials_ready(secrets: dict[str, Any] | None = None) -> bool:
    data = secrets if secrets is not None else load_secrets()
    model_ok = bool(data.get("model_api_key")) and str(data.get("model_provider") or "") not in {"", "mock", "demo"}
    search_provider = str(data.get("search_provider") or "")
    search_key = search_api_key_for(
        search_provider,
        data.get("search_api_key"),
        model_provider=data.get("model_provider"),
        model_api_key=data.get("model_api_key"),
    )
    search_ok = bool(search_key) and search_provider not in {"", "mock", "demo"}
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
    apply_schema()
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
        try:
            execute_run(session, run)
        except Exception as exc:
            session.refresh(run)
            if run.status == "failed":
                raise RuntimeError("smoke_forecast_failed") from exc
            raise
        session.refresh(run)
        payload = _audit(session, run, expected_model=expected_model, expected_search=expected_search, profile=profile)
        session.commit()
        return payload


def _internal_host(host: str) -> bool:
    name = host.lower().rstrip(".")
    if name in INTERNAL_HOSTS or name in FIXTURE_HOSTS:
        return True
    return name.endswith(".forecastlab.local") or "fixture" in name or name.startswith("demo.")


def _accepted_external_evidence(item: EvidenceItem) -> bool:
    if item.rejected or not item.as_of_eligible:
        return False
    url = (item.url or "").strip()
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    if not host or _internal_host(host):
        return False
    return True


def _require_valid_probability(value: Any) -> float:
    if value is None:
        raise RuntimeError("smoke_probability_missing")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise RuntimeError("smoke_probability_invalid") from None
    if not math.isfinite(number) or number < MIN_PROBABILITY or number > MAX_PROBABILITY:
        raise RuntimeError("smoke_probability_invalid")
    return number


def _audit(session: Session, run: ForecastRun, *, expected_model: str, expected_search: str, profile) -> dict[str, Any]:
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
    if run.status == "failed":
        raise RuntimeError("smoke_forecast_failed")
    if run.status != "completed":
        raise RuntimeError(f"run_not_terminal:{run.status}")
    if not ledger:
        raise RuntimeError("provider_ledger_empty")
    if any(item.provider_type == "model" and item.provider != expected_model for item in ledger):
        raise RuntimeError("vendor_identity_incorrect")
    if any(item.provider_type == "search" and item.provider != expected_search for item in ledger):
        raise RuntimeError("search_identity_incorrect")
    if any(item.status == "released" for item in ledger):
        raise RuntimeError("smoke_released_ledger_entry")
    if any(item.status not in ALLOWED_LEDGER_STATUSES for item in ledger):
        raise RuntimeError("smoke_nonterminal_ledger_entry")
    if not any(item.provider_type == "model" and item.status == "succeeded" for item in ledger):
        raise RuntimeError("smoke_no_successful_model_request")
    if not any(item.provider_type == "search" and item.status == "succeeded" for item in ledger):
        raise RuntimeError("smoke_no_successful_search_request")
    accepted = [item for item in evidence if _accepted_external_evidence(item)]
    accepted_urls = sorted({item.url for item in accepted if item.url})
    if not accepted:
        raise RuntimeError("smoke_no_accepted_external_evidence")
    ceiling = float(profile.max_estimated_cost_usd)
    total = float(run.total_cost_usd or run.cost_usd or 0)
    if total > ceiling + 1e-9:
        raise RuntimeError("lifetime_cost_exceeds_ceiling")
    version = session.scalars(select(ForecastVersion).where(ForecastVersion.run_id == run.id)).first()
    if version is None:
        raise RuntimeError("smoke_forecast_version_missing")
    probability = _require_valid_probability(version.ensemble_probability)
    ledger_rows = [
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
        for item in sorted(ledger, key=lambda item: (item.stage, item.provider_type, item.physical_attempt_number, item.id))
    ]
    return {
        "run_id": run.id,
        "status": run.status,
        "probability": probability,
        "model_provider": model_provider,
        "search_provider": search_provider,
        "model_cost_usd": run.model_cost_usd,
        "search_cost_usd": run.search_cost_usd,
        "failed_attempt_cost_usd": run.failed_attempt_cost_usd,
        "total_cost_usd": total,
        "cost_source": run.cost_source,
        "run_attempt_count": len(attempts),
        "provider_request_count": len(ledger),
        "accepted_external_evidence_count": len(accepted),
        "accepted_evidence_urls": accepted_urls,
        "ledger": ledger_rows,
        "checked_at": utcnow().isoformat(),
    }


def print_result(payload: dict[str, Any]) -> None:
    print(f"run_id={payload['run_id']}")
    print(f"status={payload['status']}")
    print(f"probability={payload['probability']}")
    print(f"model_provider={payload['model_provider']}")
    print(f"search_provider={payload['search_provider']}")
    print(f"model_cost_usd={payload['model_cost_usd']}")
    print(f"search_cost_usd={payload['search_cost_usd']}")
    print(f"failed_attempt_cost_usd={payload['failed_attempt_cost_usd']}")
    print(f"total_cost_usd={payload['total_cost_usd']}")
    print(f"cost_source={payload['cost_source']}")
    print(f"run_attempt_count={payload['run_attempt_count']}")
    print(f"provider_request_count={payload['provider_request_count']}")
    print(f"accepted_external_evidence_count={payload['accepted_external_evidence_count']}")
    for url in payload["accepted_evidence_urls"]:
        print(f"accepted_evidence_url={url}")
    print(f"checked_at={payload['checked_at']}")
    print("ledger:")
    for row in payload["ledger"]:
        print(json.dumps(row, sort_keys=True))


def normalize_smoke_reason(exc: BaseException) -> str:
    raw = redact_secrets(str(exc) or type(exc).__name__)
    compact = "-".join(raw.split())
    cleaned = "".join(ch if ch.isalnum() or ch in {":", "_", "-", "."} else "_" for ch in compact)
    return (cleaned or type(exc).__name__)[:180]


def main() -> int:
    if not opted_in():
        print(ABSENT_MESSAGE)
        return 0
    if not credentials_ready():
        print(CREDENTIALS_MISSING_MESSAGE)
        return 2
    try:
        payload = execute_live_smoke()
    except Exception as exc:
        print(f"paid_smoke_failed:{normalize_smoke_reason(exc)}", file=sys.stderr)
        return 1
    print_result(payload)
    return 0
