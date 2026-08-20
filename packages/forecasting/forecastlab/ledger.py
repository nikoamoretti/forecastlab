from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from forecastlab.schemas import ModelUsage
from forecastlab.timeutil import utcnow


@dataclass
class RunUsageTotals:
    model_calls: int = 0
    search_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_cost_usd: float = 0.0
    search_cost_usd: float = 0.0
    failed_attempt_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    provider_request_count: int = 0
    run_attempt_count: int = 0
    cost_label: str = "estimated"

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_calls": self.model_calls,
            "search_calls": self.search_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "model_cost_usd": self.model_cost_usd,
            "search_cost_usd": self.search_cost_usd,
            "failed_attempt_cost_usd": self.failed_attempt_cost_usd,
            "total_cost_usd": self.total_cost_usd,
            "provider_request_count": self.provider_request_count,
            "run_attempt_count": self.run_attempt_count,
            "cost_label": self.cost_label,
        }


@dataclass
class LedgerEntry:
    id: str
    run_id: str
    run_attempt_id: str | None
    logical_call_id: str
    physical_attempt_number: int
    stage: str
    provider_type: str
    provider: str
    model: str | None
    status: str
    reserved_input_tokens: int = 0
    reserved_output_tokens: int = 0
    reserved_cost_usd: float = 0.0
    actual_prompt_tokens: int | None = None
    actual_completion_tokens: int | None = None
    actual_cost_usd: float | None = None
    cost_source: str = "reserved"
    provider_request_id: str | None = None
    error_category: str | None = None
    error_message: str | None = None


@dataclass
class RunAttemptRef:
    id: str
    run_id: str
    job_id: str | None
    attempt_number: int
    status: str


class UsageLedger(Protocol):
    def begin_attempt(self, *, run_id: str, job_id: str | None, attempt_number: int) -> RunAttemptRef: ...
    def finish_attempt(self, attempt_id: str, *, status: str, error_category: str | None = None, error_message: str | None = None) -> None: ...
    def reserve(
        self,
        *,
        run_id: str,
        run_attempt_id: str | None,
        logical_call_id: str,
        physical_attempt_number: int,
        stage: str,
        provider_type: str,
        provider: str,
        model: str | None,
        reserved_input_tokens: int,
        reserved_output_tokens: int,
        reserved_cost_usd: float,
    ) -> LedgerEntry: ...
    def reconcile(self, entry_id: str, usage: ModelUsage | None, *, status: str = "succeeded") -> LedgerEntry: ...
    def fail(self, entry_id: str, *, error_category: str, error_message: str, usage: ModelUsage | None = None) -> LedgerEntry: ...
    def release(self, entry_id: str) -> LedgerEntry: ...
    def totals(self, run_id: str) -> RunUsageTotals: ...
    def entries(self, run_id: str) -> list[LedgerEntry]: ...


def _charge(entry: LedgerEntry) -> tuple[int, int, float, str]:
    if entry.status == "released":
        return 0, 0, 0.0, entry.cost_source
    prompt = entry.actual_prompt_tokens
    completion = entry.actual_completion_tokens
    if prompt is None and completion is None:
        tokens = entry.reserved_input_tokens + entry.reserved_output_tokens
        cost = entry.reserved_cost_usd
        source = "reserved" if entry.status == "reserved" else "estimated"
        return tokens, 0, cost, source
    prompt_n = int(prompt or 0)
    completion_n = int(completion or 0)
    cost = float(entry.actual_cost_usd if entry.actual_cost_usd is not None else entry.reserved_cost_usd)
    source = entry.cost_source or "estimated"
    return prompt_n + completion_n, completion_n, cost, source


def summarize_entries(entries: list[LedgerEntry], *, attempt_count: int = 0) -> RunUsageTotals:
    from forecastlab.pricing import combine_cost_labels

    totals = RunUsageTotals(run_attempt_count=attempt_count)
    labels: list[str] = []
    logical_model: set[str] = set()
    logical_search: set[str] = set()
    for entry in entries:
        if entry.status == "released":
            continue
        tokens, _completion, cost, source = _charge(entry)
        labels.append("estimated" if source in {"reserved", "estimated"} else source)
        totals.provider_request_count += 1
        if entry.provider_type == "search":
            logical_search.add(entry.logical_call_id)
            totals.search_cost_usd += cost
        else:
            logical_model.add(entry.logical_call_id)
            totals.model_cost_usd += cost
            if entry.actual_prompt_tokens is not None:
                totals.prompt_tokens += entry.actual_prompt_tokens
            else:
                totals.prompt_tokens += entry.reserved_input_tokens
            if entry.actual_completion_tokens is not None:
                totals.completion_tokens += entry.actual_completion_tokens
            else:
                totals.completion_tokens += entry.reserved_output_tokens
        if entry.status == "failed":
            totals.failed_attempt_cost_usd += cost
        totals.total_tokens += tokens
        totals.total_cost_usd += cost
    totals.model_calls = len(logical_model)
    totals.search_calls = len(logical_search)
    totals.cost_label = combine_cost_labels(labels) if labels else "estimated"
    return totals


@dataclass
class InMemoryUsageLedger:
    _attempts: list[RunAttemptRef] = field(default_factory=list)
    _entries: dict[str, LedgerEntry] = field(default_factory=dict)
    _by_key: dict[tuple[str, str, int], str] = field(default_factory=dict)

    def begin_attempt(self, *, run_id: str, job_id: str | None, attempt_number: int) -> RunAttemptRef:
        for item in self._attempts:
            if item.run_id == run_id and item.attempt_number == attempt_number:
                item.status = "running"
                item.job_id = job_id or item.job_id
                return item
        ref = RunAttemptRef(
            id=str(uuid.uuid4()),
            run_id=run_id,
            job_id=job_id,
            attempt_number=attempt_number,
            status="running",
        )
        self._attempts.append(ref)
        return ref

    def finish_attempt(
        self,
        attempt_id: str,
        *,
        status: str,
        error_category: str | None = None,
        error_message: str | None = None,
    ) -> None:
        for item in self._attempts:
            if item.id == attempt_id:
                item.status = status
                return

    def reserve(
        self,
        *,
        run_id: str,
        run_attempt_id: str | None,
        logical_call_id: str,
        physical_attempt_number: int,
        stage: str,
        provider_type: str,
        provider: str,
        model: str | None,
        reserved_input_tokens: int,
        reserved_output_tokens: int,
        reserved_cost_usd: float,
    ) -> LedgerEntry:
        key = (run_id, logical_call_id, physical_attempt_number)
        existing_id = self._by_key.get(key)
        if existing_id:
            return self._entries[existing_id]
        entry = LedgerEntry(
            id=str(uuid.uuid4()),
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            logical_call_id=logical_call_id,
            physical_attempt_number=physical_attempt_number,
            stage=stage,
            provider_type=provider_type,
            provider=provider,
            model=model,
            status="reserved",
            reserved_input_tokens=reserved_input_tokens,
            reserved_output_tokens=reserved_output_tokens,
            reserved_cost_usd=reserved_cost_usd,
            cost_source="reserved",
        )
        self._entries[entry.id] = entry
        self._by_key[key] = entry.id
        return entry

    def reconcile(self, entry_id: str, usage: ModelUsage | None, *, status: str = "succeeded") -> LedgerEntry:
        entry = self._entries[entry_id]
        if entry.status in {"succeeded", "failed", "released"}:
            return entry
        if usage is None or (usage.prompt_tokens == 0 and usage.completion_tokens == 0 and usage.cost_usd == 0):
            entry.cost_source = "estimated"
            entry.status = status
            entry.actual_cost_usd = entry.reserved_cost_usd
            return entry
        entry.actual_prompt_tokens = usage.prompt_tokens
        entry.actual_completion_tokens = usage.completion_tokens
        entry.actual_cost_usd = float(usage.cost_usd)
        entry.cost_source = usage.cost_source or "provider_reported"
        entry.provider_request_id = usage.request_id
        entry.status = status
        return entry

    def fail(self, entry_id: str, *, error_category: str, error_message: str, usage: ModelUsage | None = None) -> LedgerEntry:
        entry = self.reconcile(entry_id, usage, status="failed")
        entry.error_category = error_category
        entry.error_message = error_message[:500]
        entry.status = "failed"
        if usage is None:
            entry.cost_source = "estimated"
            entry.actual_cost_usd = entry.reserved_cost_usd
        return entry

    def release(self, entry_id: str) -> LedgerEntry:
        entry = self._entries[entry_id]
        if entry.status in {"succeeded", "failed"}:
            return entry
        entry.status = "released"
        entry.cost_source = "released"
        return entry

    def totals(self, run_id: str) -> RunUsageTotals:
        attempts = [item for item in self._attempts if item.run_id == run_id]
        return summarize_entries(self.entries(run_id), attempt_count=len(attempts))

    def entries(self, run_id: str) -> list[LedgerEntry]:
        return [item for item in self._entries.values() if item.run_id == run_id]

    def now_iso(self) -> str:
        return utcnow().isoformat()
