"""Questions the owner asks for Claude to forecast in the next daily run, at no API cost.

Each request is one ``app_settings`` row keyed ``question_request:<id>``. The daily
Claude run lists the waiting requests, records Claude's forecast as an artifact under
the same id (so the answer appears in the track record at ``/q/<id>``), and marks the
request answered.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.timeutil import utcnow
from forecastlab_api.autopilot_models import AppSetting
from forecastlab_api.db import get_db

router = APIRouter()
PREFIX = "question_request:"


class QuestionRequestIn(BaseModel):
    text: str = Field(min_length=10, max_length=500)


def _row(db: Session, request_id: str) -> AppSetting:
    row = db.get(AppSetting, PREFIX + request_id)
    if row is None:
        raise HTTPException(404, "Question request not found")
    return row


@router.get("/api/question-requests")
def list_requests(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.scalars(select(AppSetting).where(AppSetting.key.startswith(PREFIX))).all()
    return sorted((json.loads(row.value_json) for row in rows), key=lambda item: item["asked_at"], reverse=True)


@router.post("/api/question-requests", status_code=201)
def ask(body: QuestionRequestIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    text = " ".join(body.text.split())
    item = {"id": str(uuid.uuid4()), "text": text, "asked_at": utcnow().isoformat(), "status": "waiting"}
    db.add(AppSetting(key=PREFIX + item["id"], value_json=json.dumps(item)))
    db.commit()
    return item


@router.post("/api/question-requests/{request_id}/answered")
def mark_answered(request_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _row(db, request_id)
    item = json.loads(row.value_json) | {"status": "answered", "answered_at": utcnow().isoformat()}
    row.value_json = json.dumps(item)
    db.commit()
    return item


@router.delete("/api/question-requests/{request_id}")
def withdraw(request_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    row = _row(db, request_id)
    if json.loads(row.value_json)["status"] != "waiting":
        raise HTTPException(409, "Answered questions stay in the track record")
    db.delete(row)
    db.commit()
    return {"ok": True}
