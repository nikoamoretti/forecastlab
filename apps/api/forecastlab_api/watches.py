from __future__ import annotations

import json
import uuid

from sqlalchemy.orm import Session

from forecastlab.fetch import fetch_document
from forecastlab.hashing import content_hash
from forecastlab.http_client import safe_get
from forecastlab.ssrf import UnsafeURLError, validate_url
from forecastlab.timeutil import utcnow
from forecastlab.watchers import canonical_watch_value, extract_json_path, watch_changed
from forecastlab_api.demo import get_indicator
from forecastlab_api.models import Question, Watch, WatchEvent

USER_WATCH_TYPES = frozenset({"json", "html"})
INTERNAL_WATCH_TYPE = "internal"
DEMO_INDICATOR_PREFIX = "demo:indicators/"


def is_internal_watch(watch: Watch) -> bool:
    if watch.endpoint_type == INTERNAL_WATCH_TYPE:
        return True
    return watch.endpoint_url.startswith("demo:") or "/demo/indicators/" in (watch.endpoint_url or "")


def validate_user_watch(*, endpoint_url: str, endpoint_type: str) -> None:
    if endpoint_type == INTERNAL_WATCH_TYPE or endpoint_url.startswith("demo:"):
        raise UnsafeURLError("Internal watch sources cannot be submitted by users")
    if endpoint_type not in USER_WATCH_TYPES:
        raise UnsafeURLError("Watch type must be json or html")
    if "/demo/indicators/" in endpoint_url:
        raise UnsafeURLError("Internal demo indicators cannot be submitted as external watches")
    validate_url(endpoint_url, allow_local_fixtures=False)


def attach_demo_watch(session: Session, question: Question) -> Watch:
    existing = next((item for item in question.watches), None)
    if existing:
        if existing.endpoint_type != INTERNAL_WATCH_TYPE:
            existing.endpoint_type = INTERNAL_WATCH_TYPE
            existing.endpoint_url = f"{DEMO_INDICATOR_PREFIX}unemployment"
        return existing
    watch = Watch(
        id=str(uuid.uuid4()),
        question_id=question.id,
        endpoint_url=f"{DEMO_INDICATOR_PREFIX}unemployment",
        endpoint_type=INTERNAL_WATCH_TYPE,
        json_path="$.value",
        poll_seconds=300,
        auto_rerun=False,
        status="active",
    )
    session.add(watch)
    return watch


def _internal_indicator_name(watch: Watch) -> str:
    if watch.endpoint_url.startswith("demo:"):
        return watch.endpoint_url.split(":")[-1].rstrip("/").split("/")[-1]
    return watch.endpoint_url.rstrip("/").split("/")[-1]


def check_watch(session: Session, watch: Watch) -> WatchEvent:
    status = "ok"
    value = ""
    try:
        if is_internal_watch(watch):
            name = _internal_indicator_name(watch)
            payload = {"name": name, **get_indicator(name)}
            extracted = extract_json_path(payload, watch.json_path)
            value = canonical_watch_value(extracted)
        elif watch.endpoint_type == "json":
            validate_url(watch.endpoint_url, allow_local_fixtures=False)
            response = safe_get(watch.endpoint_url, expect_json=True, allow_local_fixtures=False)
            payload = json.loads(response.content.decode("utf-8"))
            extracted = extract_json_path(payload, watch.json_path)
            value = canonical_watch_value(extracted)
        else:
            validate_url(watch.endpoint_url, allow_local_fixtures=False)
            doc = fetch_document(watch.endpoint_url, allow_local_fixtures=False)
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
