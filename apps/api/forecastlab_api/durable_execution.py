"""Persist provider responses before resuming orchestration, without caching across runs."""
from __future__ import annotations

import json
from dataclasses import asdict

from sqlalchemy import select

from forecastlab.errors import BudgetExceeded, PermanentProviderError, TransientProviderError, UnknownProviderResult
from forecastlab.providers.base import ChatResult, StructuredOutputDiagnostics
from forecastlab.root_event import digest
from forecastlab.schemas import ModelUsage, SearchHit
from forecastlab.timeutil import as_utc, utcnow
from forecastlab_api.autopilot_models import ExecutionCheckpoint
from forecastlab_api.models import ForecastRunAttempt


def elapsed_execution(session, run_id: str, *, exclude_attempt_id=None) -> float:
    rows = session.scalars(select(ForecastRunAttempt).where(ForecastRunAttempt.run_id == run_id)).all()
    # Unknown interrupted attempt duration is conservatively charged up to its
    # 300-second execution allowance, never the days spent in a review queue.
    return sum(min(300.0, max(0.0, (as_utc(r.completed_at or utcnow()) - as_utc(r.started_at)).total_seconds()))
               for r in rows if r.id != exclude_attempt_id)


def checkpoint_key(run_id: str, kind: str, request: dict) -> str:
    semantic = {k: v for k, v in request.items() if k not in {
        "timeout", "estimated_input_tokens", "max_output_tokens", "max_completion_tokens", "max_visible_output_tokens"}}
    return digest({"run_id": run_id, "kind": kind, "request": semantic})


def load_checkpoint(key: str):
    from forecastlab_api.db import SessionLocal
    with SessionLocal() as session:
        row = session.get(ExecutionCheckpoint, key)
        if row and row.status == "started":
            raise PermanentProviderError("interrupted_provider_call_requires_reconciliation:" + key)
        return json.loads(row.payload_json) if row and row.status == "completed" else None


def start_checkpoint(key: str, run_id: str, stage: str):
    from forecastlab_api.db import SessionLocal
    with SessionLocal() as session:
        from forecastlab_api.autopilot_store import assert_worker_fence
        from forecastlab_api.models import ForecastRun
        assert_worker_fence(session, run_id)
        session.scalar(select(ForecastRun.id).where(ForecastRun.id == run_id).with_for_update())
        row = session.get(ExecutionCheckpoint, key)
        if row and row.status in {"started", "completed"}:
            raise PermanentProviderError("concurrent_or_interrupted_provider_call:" + key)
        if row is None:
            session.add(ExecutionCheckpoint(id=key, run_id=run_id, stage=stage, status="started"))
        else:
            row.status = "started"
        session.commit()


def finish_checkpoint(key: str, payload: dict, status="completed"):
    from forecastlab_api.db import SessionLocal
    with SessionLocal() as session:
        row = session.get(ExecutionCheckpoint, key)
        if row is None or row.status != "started":
            raise PermanentProviderError("checkpoint_state_changed")
        from forecastlab_api.autopilot_store import assert_worker_fence
        assert_worker_fence(session, row.run_id)
        row.payload_json = json.dumps(payload)
        row.status = status
        row.completed_at = utcnow()
        session.commit()


class DurableModel:
    def __init__(self, delegate, run_id):
        self.delegate, self.run_id = delegate, run_id
        self.name, self.model = delegate.name, str(getattr(delegate, "model", delegate.name))

    def get_cached_result(self, **kwargs):
        payload = load_checkpoint(checkpoint_key(self.run_id, "model", kwargs))
        if payload is None:
            return None
        return ChatResult(content=payload["content"], parsed=payload["parsed"],
            usage=ModelUsage.model_validate(payload["usage"]), raw_error=payload.get("raw_error"),
            diagnostics=StructuredOutputDiagnostics(**payload["diagnostics"]) if payload.get("diagnostics") else None)

    def complete_json(self, **kwargs):
        cached = self.get_cached_result(**kwargs)
        if cached is not None:
            return cached
        key = checkpoint_key(self.run_id, "model", kwargs)
        start_checkpoint(key, self.run_id, kwargs["schema_name"])
        from forecastlab_api.autopilot_store import paid_stage
        stage = ("root_estimate:" + json.loads(kwargs["user"])["role"]
                 if kwargs["schema_name"] == "root_event" else kwargs["schema_name"])
        token = paid_stage.set(stage)
        try:
            result = self.delegate.complete_json(**kwargs)
        except UnknownProviderResult:
            raise  # Leave the checkpoint started; never silently repeat this call.
        except (BudgetExceeded, PermanentProviderError, TransientProviderError):
            finish_checkpoint(key, {}, "failed")
            raise
        finally:
            paid_stage.reset(token)
        payload = asdict(result)
        payload["usage"] = result.usage.model_dump(mode="json")
        finish_checkpoint(key, payload)
        return result


class DurableSearch:
    def __init__(self, delegate, run_id):
        self.delegate, self.run_id, self.name = delegate, run_id, delegate.name

    def get_cached_result(self, query, *, max_results=5):
        key = checkpoint_key(self.run_id, "search", {"query": query, "max_results": max_results})
        payload = load_checkpoint(key)
        return [SearchHit.model_validate(item) for item in payload["hits"]] if payload is not None else None

    def search(self, query, *, max_results=5):
        cached = self.get_cached_result(query, max_results=max_results)
        if cached is not None:
            return cached
        key = checkpoint_key(self.run_id, "search", {"query": query, "max_results": max_results})
        start_checkpoint(key, self.run_id, "search")
        try:
            hits = self.delegate.search(query, max_results=max_results)
        except UnknownProviderResult:
            raise
        except (BudgetExceeded, PermanentProviderError, TransientProviderError):
            finish_checkpoint(key, {}, "failed")
            raise
        finish_checkpoint(key, {"hits": [item.model_dump(mode="json") for item in hits]})
        return hits


class DurableDocumentStore:
    def __init__(self, run_id: str):
        self.run_id = run_id

    def fetch(self, url: str, **kwargs):
        from forecastlab.fetch import fetch_document
        from forecastlab.schemas import FetchedDocument
        from forecastlab_api.artifact_store import get_bytes, put_bytes
        from forecastlab_api.db import SessionLocal
        semantic = {k: v.isoformat() if hasattr(v, "isoformat") else v
                    for k, v in kwargs.items() if k != "limits"}
        key = checkpoint_key(self.run_id, "document", {"url": url, **semantic})
        with SessionLocal() as session:
            row = session.get(ExecutionCheckpoint, key)
            if row and row.status == "completed":
                return FetchedDocument.model_validate_json(get_bytes(json.loads(row.payload_json)["document"]))
        raw = {}

        def retain(data, content_type):
            raw.update(put_bytes(data, content_type=content_type))

        document = fetch_document(url, **kwargs, retain_bytes=retain)
        artifact = put_bytes(document.model_dump_json().encode(), content_type="application/json", prefix="documents")
        with SessionLocal() as session:
            from forecastlab_api.autopilot_store import assert_worker_fence, insert_once
            assert_worker_fence(session, self.run_id)
            insert_once(session, ExecutionCheckpoint, {"id": key, "run_id": self.run_id, "stage": "document",
                "status": "completed", "payload_json": json.dumps({"document": artifact, "raw": raw,
                    "url": url, "text_hash": document.extracted_text_hash}), "completed_at": utcnow()})
            session.commit()
        return document
