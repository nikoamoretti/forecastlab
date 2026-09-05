"""Bounded, free checks of documents that actually entered the last evidence packet."""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

from sqlalchemy import select

from forecastlab.fetch import FetchLimits, fetch_document
from forecastlab.root_event import digest
from forecastlab.schemas import FetchedDocument
from forecastlab_api.artifact_store import get_bytes, put_bytes
from forecastlab_api.autopilot_models import AppSetting, ExecutionCheckpoint
from forecastlab_api.models import ForecastRun, PersonalForecast


def meaningful_change(old: str, new: str) -> bool:
    a, b = (" ".join(text.lower().split()) for text in (old, new))
    if not a or not b or a == b:
        return False
    # A changed measurement is material even when the surrounding prose is identical.
    def numbers(s):
        return re.findall(r"(?<!\w)[+-]?\d[\d,.]*\s*(?:%|percent|jobs|million|thousand)", s)
    return numbers(a) != numbers(b) or SequenceMatcher(None, a[:20000], b[:20000], autojunk=False).ratio() < 0.95


def accepted_sources_changed(session, managed) -> bool:
    latest = session.scalar(select(PersonalForecast).join(ForecastRun).where(ForecastRun.question_id == managed.question_id,
        PersonalForecast.outcome_status == "forecasted").order_by(PersonalForecast.created_at.desc()).limit(1))
    if latest is None:
        return False
    packet = json.loads(latest.result_json).get("evidence_assessments", [])
    urls = {item["url"] for item in packet if item.get("usable") and item.get("quote")}
    rows = session.scalars(select(ExecutionCheckpoint).where(ExecutionCheckpoint.run_id == latest.run_id,
        ExecutionCheckpoint.stage == "document", ExecutionCheckpoint.status == "completed")).all()
    changed = False
    rows = [r for r in sorted(rows, key=lambda r: r.id) if json.loads(r.payload_json)["url"] in urls]
    cursor_key = "source_cursor:" + managed.question_id
    cursor = session.get(AppSetting, cursor_key)
    offset = int(json.loads(cursor.value_json)) % len(rows) if cursor and rows else 0
    ordered = rows[offset:] + rows[:offset]
    for row in ordered[:3]:
        manifest = json.loads(row.payload_json)
        if manifest["url"] not in urls:
            continue
        previous = FetchedDocument.model_validate_json(get_bytes(manifest["document"]))
        current = fetch_document(manifest["url"], allow_local_fixtures=False, limits=FetchLimits(timeout=4))
        if current.rejected:
            continue
        if meaningful_change(previous.text, current.text):
            artifact = put_bytes(current.model_dump_json().encode(), prefix="monitor", content_type="application/json")
            from forecastlab_api.autopilot import _decision
            _decision(session, "source:" + digest({"question": managed.question_id, "hash": current.content_hash}),
                "source_changed", "Accepted source text changed", question_id=managed.question_id, artifact=artifact)
            changed = True
    if rows:
        session.merge(AppSetting(key=cursor_key, value_json=json.dumps((offset + min(3, len(rows))) % len(rows))))
    return changed
