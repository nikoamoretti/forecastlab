from __future__ import annotations

import uuid
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from forecastlab.fetch import fetch_document
from forecastlab.hashing import content_hash
from forecastlab.timeutil import utcnow
from forecastlab.watchers import canonical_watch_value, extract_json_path, watch_changed
from forecastlab_api.config import settings
from forecastlab_api.demo import get_indicator
from forecastlab_api.models import Question, Watch, WatchEvent


def attach_demo_watch(session: Session, question: Question) -> Watch:
    existing = next((item for item in question.watches), None)
    if existing:
        return existing
    watch = Watch(
        id=str(uuid.uuid4()),
        question_id=question.id,
        endpoint_url="http://127.0.0.1:8765/demo/indicators/unemployment",
        endpoint_type="json",
        json_path="$.value",
        poll_seconds=300,
        auto_rerun=False,
        status="active",
    )
    session.add(watch)
    return watch


def check_watch(session: Session, watch: Watch) -> WatchEvent:
    status = "ok"
    value = ""
    try:
        if watch.endpoint_type == "json":
            parsed = urlparse(watch.endpoint_url)
            if parsed.path.startswith("/demo/indicators/"):
                name = parsed.path.rstrip("/").split("/")[-1]
                payload = {"name": name, **get_indicator(name)}
            else:
                with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                    response = client.get(watch.endpoint_url)
                    response.raise_for_status()
                    payload = response.json()
            extracted = extract_json_path(payload, watch.json_path)
            value = canonical_watch_value(extracted)
        else:
            doc = fetch_document(
                watch.endpoint_url,
                allow_local_fixtures=settings.allow_local_fixtures,
            )
            if doc.rejected:
                raise RuntimeError(doc.rejection_reason or "fetch_failed")
            value = canonical_watch_value(doc.text[:5000])
    except Exception as exc:
        status = f"error:{exc.__class__.__name__}"
        value = watch.previous_value or ""

    digest = content_hash(value)
    changed = watch_changed(watch.previous_hash, digest) and status == "ok"
    event = WatchEvent(
        id=str(uuid.uuid4()),
        watch_id=watch.id,
        old_hash=watch.previous_hash,
        new_hash=digest,
        old_value=watch.previous_value,
        new_value=value,
        material=changed,
        fetch_status=status,
    )
    session.add(event)
    watch.last_checked_at = utcnow()
    if status == "ok":
        watch.previous_hash = digest
        watch.previous_value = value
    if changed:
        question = session.get(Question, watch.question_id)
        if question:
            question.stale = True
            question.status = "stale"
    return event


check_watch = check_watch
