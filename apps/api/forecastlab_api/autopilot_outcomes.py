"""Official release proposals and human-confirmed, append-only scoring history."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select

from forecastlab.evaluation import brier_score, log_loss, mean
from forecastlab.http_client import safe_get
from forecastlab.macro import MacroDataError, MacroSpec
from forecastlab.macro_evidence import parse_first_release
from forecastlab.root_event import contract_hash, digest
from forecastlab.schemas import ForecastContract
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.artifact_store import get_bytes, put_bytes
from forecastlab_api.autopilot_models import (
    AppSetting,
    AutopilotRun,
    ManagedQuestion,
    OutcomeProposal,
    QuestionAdjudication,
)
from forecastlab_api.autopilot_store import insert_once, notify
from forecastlab_api.models import ForecastRun, PersonalForecast


def collect_outcome(session, managed: ManagedQuestion):
    existing = session.scalar(select(OutcomeProposal).where(OutcomeProposal.question_id == managed.question_id))
    if existing:
        return existing
    if managed.last_checked_at and utcnow() - as_utc(managed.last_checked_at) < timedelta(minutes=30):
        return None
    spec = MacroSpec.model_validate_json(managed.macro_json)
    if utcnow() < spec.release_at:
        return None
    managed.last_checked_at = utcnow()
    session.commit()
    family = "cpi" if spec.indicator == "cpi" else "empsit"
    suffix = spec.release_at.astimezone(ZoneInfo("America/New_York")).strftime("%m%d%Y")
    cache_key = "release:" + managed.release_event
    cached = session.get(AppSetting, cache_key)
    urls = [f"https://www.bls.gov/news.release/archives/{family}_{suffix}.htm",
            f"https://www.bls.gov/news.release/{family}.nr0.htm"]
    problems = []
    for url in urls:
        try:
            if cached:
                manifest = json.loads(cached.value_json)
                html = get_bytes(manifest["artifact"]).decode("utf-8")
                url = manifest["source_url"]
                retrieved = datetime.fromisoformat(manifest["retrieved_at"])
            else:
                response = safe_get(url, timeout=8)
                if response.status_code != 200 or response.final_url != url:
                    raise MacroDataError("official_release_unavailable")
                html = response.content.decode("utf-8")
                retrieved = utcnow()
                manifest = {"artifact": put_bytes(response.content, content_type="text/html", prefix="releases"),
                            "source_url": url, "retrieved_at": retrieved.isoformat()}
            measurement = parse_first_release(html, spec, source_url=url, retrieved_at=retrieved)
            personal = session.get(PersonalForecast, managed.initial_run_id)
            contract = ForecastContract.model_validate_json(personal.contract_json)
            payload = {**measurement, "artifact": manifest["artifact"], "release_event": managed.release_event}
            proposal = OutcomeProposal(id=digest({"contract": contract_hash(contract), "measurement": payload}),
                question_id=managed.question_id, contract_hash=contract_hash(contract), payload_json=json.dumps(payload))
            session.add(proposal)
            insert_once(session, AppSetting, {"key": cache_key, "value_json": json.dumps(manifest)})
            notify(session, "outcome:" + proposal.id, "outcome", "Outcome ready for confirmation",
                   f"{spec.indicator}, {spec.observation_period}: {measurement['value']:g} {measurement['units']}. Confirm before scoring.", managed.question_id)
            session.commit()
            return proposal
        except Exception as exc:
            session.rollback()
            problems.append(str(exc) if isinstance(exc, MacroDataError) else type(exc).__name__)
            if cached:
                break
    notify(session, "outcome-gap:" + managed.question_id, "incident", "First-release evidence is missing",
        "; ".join(problems) + ". Revised API observations cannot resolve this question.", managed.question_id)
    session.commit()
    return None


def confirm(session, proposal_id: str, *, confirmed_by: str, correction: dict | None = None):
    proposal = session.get(OutcomeProposal, proposal_id)
    if not proposal:
        raise HTTPException(404, "Outcome proposal not found")
    managed = session.scalar(select(ManagedQuestion).where(ManagedQuestion.question_id == proposal.question_id).with_for_update())
    personal = session.get(PersonalForecast, managed.initial_run_id)
    contract = ForecastContract.model_validate_json(personal.contract_json)
    spec = MacroSpec.model_validate_json(managed.macro_json)
    if contract_hash(contract) != proposal.contract_hash or utcnow() < spec.release_at:
        raise HTTPException(409, "Outcome does not match the frozen contract or is not due")
    payload = json.loads(proposal.payload_json)
    verified = parse_first_release(get_bytes(payload["artifact"]).decode("utf-8"), spec,
        source_url=payload["source_url"], retrieved_at=datetime.fromisoformat(payload["retrieved_at"]))
    if verified["outcome"] != payload["outcome"] or verified["value"] != payload["value"]:
        raise HTTPException(409, "Outcome evidence failed verification")
    previous = session.scalar(select(QuestionAdjudication).where(QuestionAdjudication.question_id == managed.question_id)
        .order_by(QuestionAdjudication.revision.desc()).limit(1))
    if previous and not correction:
        return previous
    if correction and not previous:
        raise HTTPException(409, "Confirm the original outcome before recording a correction")
    record = QuestionAdjudication(id=str(uuid.uuid4()), question_id=managed.question_id,
        revision=previous.revision + 1 if previous else 1, proposal_id=proposal.id, contract_hash=proposal.contract_hash,
        outcome=correction["outcome"] if correction else payload["outcome"],
        evidence_json=json.dumps({"proposal": payload, "correction": correction}), confirmed_by=confirmed_by)
    session.add(record)
    managed.status = "resolved" if record.outcome is not None else "cancelled"
    notify(session, "adjudication:" + record.id, "outcome", "Outcome corrected" if correction else "Outcome confirmed",
           f"Adjudication {record.revision}; prior records retained", managed.question_id)
    session.commit()
    return record


def proposal_list(session) -> list[dict]:
    proposals = session.scalars(select(OutcomeProposal).order_by(OutcomeProposal.created_at.desc()).limit(100)).all()
    result = []
    for proposal in proposals:
        latest = session.scalar(select(QuestionAdjudication).where(QuestionAdjudication.question_id == proposal.question_id)
            .order_by(QuestionAdjudication.revision.desc()).limit(1))
        result.append({"id": proposal.id, "question_id": proposal.question_id, "created_at": proposal.created_at,
            **json.loads(proposal.payload_json), "confirmed": latest is not None,
            "adjudication_revision": latest.revision if latest else None,
            "confirmed_outcome": latest.outcome if latest else None})
    return result


def metrics(session) -> dict:
    managed = session.scalars(select(ManagedQuestion)).all()
    rows = session.execute(select(AutopilotRun, ForecastRun, PersonalForecast).join(ForecastRun,
        ForecastRun.id == AutopilotRun.run_id).join(PersonalForecast, PersonalForecast.run_id == ForecastRun.id)).all()
    grouped: dict[str, list] = {}
    for auto, run, personal in rows:
        grouped.setdefault(auto.question_id, []).append((auto, run, personal))
    initial_scores, latest_scores, matched_initial, matched_latest = [], [], [], []
    resolved = 0
    groups: dict[str, list[str]] = {}
    for question in managed:
        groups.setdefault(question.release_event, []).append(question.question_id)
        adjudication = session.scalar(select(QuestionAdjudication).where(QuestionAdjudication.question_id == question.question_id)
            .order_by(QuestionAdjudication.revision.desc()).limit(1))
        if not adjudication or adjudication.outcome is None:
            continue
        resolved += 1
        versions = sorted(grouped.get(question.question_id, []), key=lambda r: as_utc(r[0].cutoff))
        eligible = [v for v in versions if as_utc(v[0].cutoff) < as_utc(v[0].stop_at)]
        initial = next((r for r in versions if r[1].id == question.initial_run_id), None)
        latest = eligible[-1] if eligible else None

        def score(version, question=question, adjudication=adjudication):
            if not version:
                return None
            auto, run, personal = version
            if run.status != "completed" or personal.outcome_status != "forecasted" or not run.finished_at or as_utc(run.finished_at) >= as_utc(auto.stop_at):
                return None
            probability = json.loads(personal.result_json).get("probability")
            if probability is None:
                return None
            return {"question_id": question.question_id, "run_id": run.id,
                    "brier": brier_score(probability, adjudication.outcome), "log_loss": log_loss(probability, adjudication.outcome)}
        a, b = score(initial), score(latest)
        if a:
            initial_scores.append(a)
        if b:
            latest_scores.append(b)
        if a and b:
            matched_initial.append(a)
            matched_latest.append(b)

    def summarize(scores):
        return {"scored_questions": len(scores), "resolved_questions": resolved,
            "coverage": len(scores) / resolved if resolved else None,
            "brier_score": mean([s["brier"] for s in scores]), "log_loss": mean([s["log_loss"] for s in scores])}
    forecasted = sum(p.outcome_status == "forecasted" and r.status == "completed" for _, r, p in rows)
    return {"questions": len(managed), "versions": len(rows), "resolved_questions": resolved,
        "forecasted": forecasted, "coverage": forecasted / len(rows) if rows else None,
        "abstentions": sum(p.outcome_status == "insufficient_evidence" and r.status == "completed" for _, r, p in rows),
        "failures": sum(r.status == "failed" for _, r, _ in rows), "cost_usd": sum(r.total_cost_usd for _, r, _ in rows),
        "latency_ms": mean([r.latency_ms for _, r, _ in rows if r.finished_at]),
        "initial": summarize(initial_scores), "latest_prerelease": summarize(latest_scores),
        "matched": {"questions": len(matched_initial), "initial": summarize(matched_initial), "latest_prerelease": summarize(matched_latest)},
        "release_groups": [{"release_event": event, "question_ids": ids} for event, ids in sorted(groups.items())]}
