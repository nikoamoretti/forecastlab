"""Local public-data cache and review handoff for automatic macro questions."""
from __future__ import annotations

import fcntl
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.macro import MacroDataError, MacroSnapshot, MacroSpec, fetch_latest_macro_snapshots
from forecastlab.question_selection import (
    HORIZON_DAYS,
    SELECTION_VERSION,
    ScheduledRelease,
    choose_questions,
    parse_bls_calendar,
)
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.config import settings
from forecastlab_api.models import ForecastRun, PersonalForecast, Question
from forecastlab_api.personal_forecasts import create_draft


class SelectionSources(BaseModel):
    version: str = SELECTION_VERSION
    checked_at: datetime
    releases: list[ScheduledRelease] = Field(default_factory=list)
    snapshots: dict[str, MacroSnapshot] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list)


def _fetch_sources(now: datetime) -> SelectionSources:
    result = SelectionSources(checked_at=now)
    with httpx.Client(timeout=12, follow_redirects=False, headers={"User-Agent": "ForecastLab/1.0 public calendar"}) as http:
        for year in sorted({now.year, (now + timedelta(days=HORIZON_DAYS)).year}):
            url = f"https://www.bls.gov/schedule/{year}/home.htm"
            try:
                # Fixed official origin, no model/user URL or redirects.
                with http.stream("GET", url) as response:
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 2_000_000:
                            raise MacroDataError("calendar_response_too_large")
                result.releases.extend(parse_bls_calendar(content.decode("utf-8"), source_url=url, checked_at=now))
            except (httpx.HTTPError, ValueError) as exc:
                reason = str(exc) if isinstance(exc, MacroDataError) else type(exc).__name__
                result.gaps.append(f"BLS {year} release calendar unavailable ({reason}).")
        if result.releases:
            try:
                result.snapshots = fetch_latest_macro_snapshots(client=http)
            except MacroDataError as exc:
                result.gaps.append(f"BLS observations unavailable ({exc}).")
    return result


def _fresh(sources: SelectionSources, now: datetime) -> bool:
    age = now - as_utc(sources.checked_at)
    ttl = timedelta(minutes=10) if sources.gaps else timedelta(hours=12)
    if sources.version != SELECTION_VERSION or not timedelta(0) <= age < ttl:
        return False
    # Invalidate immediately after a scheduled release, even inside the TTL.
    return not any(as_utc(sources.checked_at) < as_utc(r.release_at) <= now for r in sources.releases)


def selection_sources() -> SelectionSources:
    """Cache public inputs across requests, workers and restarts; never serve stale data.

    Serializing refreshes prevents page reloads/concurrent requests consuming the
    small unauthenticated BLS quota. Failures cool down for ten minutes.
    """
    path = settings.data_dir / "local" / "question-selection-v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = utcnow()
        try:
            cached = SelectionSources.model_validate_json(path.read_text())
            if _fresh(cached, now):
                return cached
        except (OSError, ValueError):
            pass
        sources = _fetch_sources(now)
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as out:
            temporary = Path(out.name)
            out.write(sources.model_dump_json())
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return sources


def suggestions(session: Session) -> dict:
    sources = selection_sources()
    tracked = []
    cohort_questions = []
    # Latest personal runs first. Do not count fixtures or backtests as live work.
    rows = session.execute(select(ForecastRun.question_id, ForecastRun.id, ForecastRun.status,
        PersonalForecast.macro_json, Question.is_benchmark)
        .join(PersonalForecast, PersonalForecast.run_id == ForecastRun.id).join(Question, Question.id == ForecastRun.question_id)
        .where(ForecastRun.mode == "live", ForecastRun.synthetic_fixture_run.is_(False))
        .order_by(PersonalForecast.created_at.desc(), ForecastRun.id.desc())).all()
    for question_id, run_id, status, macro_json, benchmark in rows:
        if macro_json == "{}":
            continue
        try:
            macro = MacroSpec.model_validate_json(macro_json)
            if benchmark:
                # A frozen pilot assignment cannot be resumed/changed as an
                # ordinary forecast, but its correlated outcome stays visible.
                cohort_questions.append((question_id, macro))
            else:
                tracked.append((question_id, run_id if status in {"preparing", "awaiting_review"} else None, macro))
        except ValueError:
            continue  # Legacy/non-macro artifacts cannot establish identity.
    items, gaps = choose_questions(sources.releases, sources.snapshots, now=utcnow(), tracked=tracked,
                                  cohort_questions=cohort_questions)
    return {"selection_version": SELECTION_VERSION, "checked_at": sources.checked_at,
            "items": [item.model_dump(mode="json") for item in items],
            "gaps": sources.gaps + gaps, "selection_cost_usd": 0,
            "policy": "Next release per indicator; latest observed value as threshold; first published outcome. "
                      "New questions first, then earliest release. Review before research."}


def suggestion_draft(session: Session, suggestion_id: str) -> ForecastRun:
    if len(suggestion_id) != 64 or any(c not in "0123456789abcdef" for c in suggestion_id):
        raise HTTPException(404, "Suggested question not found")
    key = f"question-selection:{suggestion_id}"
    existing = session.scalar(select(PersonalForecast).where(PersonalForecast.request_key == key))
    if existing:
        run = session.get(ForecastRun, existing.run_id)
        if run is None:
            raise HTTPException(404, "Draft run not found")
        return run
    candidate = next((item for item in suggestions(session)["items"] if item["id"] == suggestion_id), None)
    if candidate is None:
        raise HTTPException(409, "This suggestion has changed or expired. Refresh the suggested questions.")
    if candidate["existing_question_id"]:
        raise HTTPException(409, "This question is already on your board. Open its existing forecast or draft.")
    return create_draft(session, question="", mode="live", as_of=None, request_key=key,
                        macro=MacroSpec.model_validate(candidate["macro"]), question_selection=candidate)
