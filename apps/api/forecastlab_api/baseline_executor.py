"""Execution of ``statistical_baseline_v1``: official observations in, one deterministic probability out.

No model, search, or paid call is made, so the run's usage ledger stays at zero.
The method reuses the root method's official-data path (``fetch_macro``) and its
point-in-time snapshot validation. Anything it cannot compute honestly becomes
an evidence gap and a withheld (null) probability, never a guess.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.budget import Budget
from forecastlab.deadline import check_deadline, request_timeout
from forecastlab.errors import BudgetExceeded, PermanentProviderError
from forecastlab.macro import MacroDataError, MacroSnapshot, MacroSpec, fetch_macro
from forecastlab.macro_evidence import validate_snapshot
from forecastlab.statistical_baseline import (
    METHOD,
    BaselineUnavailable,
    aggregation_summary,
    forecast_from_snapshot,
    fred_history_options,
)
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.models import ForecastRun, ForecastVersion, PersonalForecast
from forecastlab_api.secrets import load_secrets

MACRO_SPEC_REQUIRED = "statistical_baseline_requires_macro_spec"
DEMO_MODE_GAP = "statistical_baseline_demo_mode_no_official_data"


def _point_in_time_check(snapshot: MacroSnapshot, spec: MacroSpec, cutoff: datetime, *, historical: bool) -> None:
    """The root method's snapshot checks: identity, hash, units, target, and availability at the cutoff.

    A historical (ALFRED vintage) snapshot is necessarily retrieved after its
    cutoff; there the vintage bound on each observation's ``available_at`` is
    the availability proof, and it is still checked against the cutoff.
    """
    if historical:
        snapshot = snapshot.model_copy(update={"retrieved_at": min(as_utc(snapshot.retrieved_at), as_utc(cutoff))})
    validate_snapshot(snapshot, spec, cutoff)


def execute_statistical_baseline(session: Session, *, run: ForecastRun, profile, context, ledger, progress,
                                 prior_elapsed_seconds: float = 0.0) -> None:
    record = session.get(PersonalForecast, run.id)
    checkpoint = json.loads(record.result_json or "{}") if record else {}
    macro = MacroSpec.model_validate_json(record.macro_json) if record and record.macro_json != "{}" else None
    contract = json.loads(record.contract_json) if record and record.contract_json != "{}" else None
    budget = Budget.from_persisted(profile, ledger.totals(run.id), provider=context.model_provider,
        model=context.model_name, search_provider=context.search_provider,
        prior_elapsed_seconds=prior_elapsed_seconds)
    checkpoint.setdefault("forecast_cutoff", as_utc(run.as_of).isoformat() if run.as_of else utcnow().isoformat())
    gaps: list[str] = []
    result = None

    def save() -> None:
        if record is None:
            return
        from forecastlab_api.autopilot_store import assert_worker_fence
        assert_worker_fence(session, run.id)
        record.result_json = json.dumps(checkpoint)
        run.budget_json = json.dumps(budget.snapshot())
        session.commit()

    try:
        if macro is None:
            gaps.append(MACRO_SPEC_REQUIRED)
        elif run.mode == "demo":
            # Demo plumbing never fetches official data and never invents a series.
            gaps.append(DEMO_MODE_GAP)
        else:
            historical = run.mode == "backtest"
            if "macro_snapshot" not in checkpoint:
                progress("evidence", "Retrieving official macro observations", 0.2)
                budget.add_fetch("macro_observations")
                try:
                    snapshot = fetch_macro(macro, as_of=as_utc(run.as_of) if historical and run.as_of else None,
                        fred_api_key=load_secrets().get("fred_api_key"),
                        timeout=request_timeout(ledger, budget.remaining_seconds("macro_observations"),
                                                "macro_observations"),
                        **fred_history_options(macro.indicator))
                    checkpoint["macro_snapshot"] = snapshot.model_dump(mode="json")
                except MacroDataError as exc:
                    if str(exc).startswith(("macro_request_failed", "bls_request_not_succeeded")):
                        raise PermanentProviderError(f"Macro data provider failed: {exc}") from exc
                    gaps.append(str(exc))
                save()
            if "macro_snapshot" in checkpoint:
                progress("forecast", "Applying the statistical baseline rule", 0.7)
                snapshot = MacroSnapshot.model_validate(checkpoint["macro_snapshot"])
                _point_in_time_check(snapshot, macro, datetime.fromisoformat(checkpoint["forecast_cutoff"]),
                                     historical=historical)
                result = forecast_from_snapshot(macro, snapshot)
    except BudgetExceeded as exc:
        if exc.reason == "max_wall_clock_seconds":
            checkpoint["evidence_gaps"] = [f"execution_timeout:{exc.stage}"]
            save()
            raise
        gaps.append(f"budget_or_time_exhausted:{exc}")
    except (BaselineUnavailable, MacroDataError) as exc:
        gaps.append(str(exc))
    check_deadline(ledger, "complete_statistical_baseline")
    probability = result["probability"] if result else None
    outcome = "forecasted" if probability is not None else "insufficient_evidence"
    aggregation = aggregation_summary(result) if result else {}
    run.budget_json = json.dumps(budget.snapshot())
    checkpoint.update({
        "schema_version": "personal_forecast_v1", "outcome_status": outcome, "probability": probability,
        "evidence_gaps": sorted(set(gaps)), "statistical_baseline": result, "aggregation": aggregation or None,
        "estimates": [], "evidence_assessments": [], "developments_to_watch": [],
        "method": METHOD, "model_calls": 0, "search_calls": 0,
        "data_basis": ("demo_mode_no_data" if run.mode == "demo" else
                       "official_macro_snapshot" if result else "none"),
        "macro_validation_domain": "us_macro" if macro else "general_unvalidated", "contract": contract})
    if record is not None:
        record.outcome_status = outcome
        record.result_json = json.dumps(checkpoint)
    run.aggregation_json = json.dumps(aggregation)
    run.status = "completed"
    run.finished_at = utcnow()
    run.progress_stage = "report"
    run.progress_pct = 100
    if result:
        run.progress_message = "Statistical baseline ready"
    elif record is None:
        # Without a personal envelope there is no result document, so the reason goes on the run itself.
        run.progress_message = f"Probability withheld: {', '.join(sorted(set(gaps)))}"[:500]
    else:
        run.progress_message = ("Demo mode: no official data, probability withheld" if run.mode == "demo" else
                                "Probability withheld; review evidence gaps")
    run.question.status = "completed"
    run.question.stale = False
    previous = session.scalar(select(ForecastVersion).where(ForecastVersion.question_id == run.question_id)
                              .order_by(ForecastVersion.created_at.desc()).limit(1))
    session.add(ForecastVersion(id=str(uuid.uuid4()), question_id=run.question_id, run_id=run.id,
        ensemble_probability=probability, aggregation_json=run.aggregation_json, shrinkage=0.0, track_spread=None,
        raw_track_probabilities_json=json.dumps({"statistical_baseline": probability} if result else {}),
        evidence_ids_json=json.dumps([f"macro:{result['snapshot']['raw_hash']}"] if result else []),
        previous_version_id=previous.id if previous else None))
    session.commit()
