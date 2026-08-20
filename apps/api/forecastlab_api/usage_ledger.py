from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from forecastlab.errors import BudgetExceeded
from forecastlab.ledger import LedgerEntry, RunAttemptRef, RunUsageTotals, summarize_entries
from forecastlab.schemas import ModelUsage
from forecastlab.timeutil import utcnow
from forecastlab_api.models import ForecastRun, ForecastRunAttempt, ProviderCallLedger


def _entry_from_row(row: ProviderCallLedger) -> LedgerEntry:
    return LedgerEntry(
        id=row.id,
        run_id=row.run_id,
        run_attempt_id=row.run_attempt_id,
        logical_call_id=row.logical_call_id,
        physical_attempt_number=row.physical_attempt_number,
        stage=row.stage,
        provider_type=row.provider_type,
        provider=row.provider,
        model=row.model,
        status=row.status,
        reserved_input_tokens=row.reserved_input_tokens,
        reserved_output_tokens=row.reserved_output_tokens,
        reserved_cost_usd=row.reserved_cost_usd,
        actual_prompt_tokens=row.actual_prompt_tokens,
        actual_completion_tokens=row.actual_completion_tokens,
        actual_cost_usd=row.actual_cost_usd,
        cost_source=row.cost_source,
        provider_request_id=row.provider_request_id,
        error_category=row.error_category,
        error_message=row.error_message,
    )


def apply_totals_to_attempt(row: ForecastRunAttempt, totals: RunUsageTotals) -> None:
    row.model_cost_usd = totals.model_cost_usd
    row.search_cost_usd = totals.search_cost_usd
    row.total_cost_usd = totals.total_cost_usd
    row.prompt_tokens = totals.prompt_tokens
    row.completion_tokens = totals.completion_tokens
    row.search_calls = totals.search_calls
    row.provider_request_count = totals.provider_request_count


def apply_totals_to_run(run: ForecastRun, totals: RunUsageTotals) -> None:
    run.model_cost_usd = totals.model_cost_usd
    run.search_cost_usd = totals.search_cost_usd
    run.failed_attempt_cost_usd = totals.failed_attempt_cost_usd
    run.total_cost_usd = totals.total_cost_usd
    run.cost_usd = totals.total_cost_usd
    run.prompt_tokens = totals.prompt_tokens
    run.completion_tokens = totals.completion_tokens
    run.total_tokens = totals.total_tokens
    run.tokens = totals.total_tokens
    run.provider_request_count = totals.provider_request_count
    run.run_attempt_count = totals.run_attempt_count
    run.cost_source = totals.cost_label


class PersistentUsageLedger:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        max_cost_usd: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.max_cost_usd = max_cost_usd
        self.max_tokens = max_tokens

    def _fits(self, totals: RunUsageTotals, *, extra_cost: float, extra_tokens: int) -> bool:
        if self.max_cost_usd is not None and totals.total_cost_usd + extra_cost > self.max_cost_usd + 1e-12:
            return False
        if self.max_tokens is not None and totals.total_tokens + extra_tokens > self.max_tokens:
            return False
        return True

    def begin_attempt(self, *, run_id: str, job_id: str | None, attempt_number: int) -> RunAttemptRef:
        with self.session_factory() as session:
            existing = session.scalar(
                select(ForecastRunAttempt).where(
                    ForecastRunAttempt.run_id == run_id,
                    ForecastRunAttempt.attempt_number == attempt_number,
                )
            )
            if existing is None:
                existing = ForecastRunAttempt(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    job_id=job_id,
                    attempt_number=attempt_number,
                    status="running",
                    started_at=utcnow(),
                )
                session.add(existing)
            else:
                existing.status = "running"
                existing.job_id = job_id or existing.job_id
            session.commit()
            session.refresh(existing)
            self._refresh_run(session, run_id)
            session.commit()
            return RunAttemptRef(
                id=existing.id,
                run_id=existing.run_id,
                job_id=existing.job_id,
                attempt_number=existing.attempt_number,
                status=existing.status,
            )

    def finish_attempt(
        self,
        attempt_id: str,
        *,
        status: str,
        error_category: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.session_factory() as session:
            row = session.get(ForecastRunAttempt, attempt_id)
            if row is None:
                return
            totals = self.attempt_totals(row.run_id, row.id)
            row.status = status
            row.completed_at = utcnow()
            row.error_category = error_category
            row.error_message = (error_message or "")[:500] or None
            apply_totals_to_attempt(row, totals)
            self._refresh_run(session, row.run_id)
            session.commit()

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
        with self.session_factory() as session:
            existing = session.scalar(
                select(ProviderCallLedger).where(
                    ProviderCallLedger.run_id == run_id,
                    ProviderCallLedger.logical_call_id == logical_call_id,
                    ProviderCallLedger.physical_attempt_number == physical_attempt_number,
                )
            )
            if existing is not None:
                self._refresh_run(session, run_id)
                session.commit()
                session.refresh(existing)
                return _entry_from_row(existing)
            pending_tokens = reserved_input_tokens + reserved_output_tokens
            if not self._fits(self._totals(session, run_id), extra_cost=reserved_cost_usd, extra_tokens=pending_tokens):
                raise BudgetExceeded("provider_reserve", "max_estimated_cost_usd")
            existing = ProviderCallLedger(
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
                request_started_at=utcnow(),
            )
            session.add(existing)
            self._refresh_run(session, run_id)
            self._refresh_attempt(session, run_attempt_id)
            session.commit()
            session.refresh(existing)
            return _entry_from_row(existing)

    def reconcile(self, entry_id: str, usage: ModelUsage | None, *, status: str = "succeeded") -> LedgerEntry:
        with self.session_factory() as session:
            row = session.get(ProviderCallLedger, entry_id)
            if row is None:
                raise KeyError(entry_id)
            if row.status in {"succeeded", "failed", "released"}:
                return _entry_from_row(row)
            if usage is None or (usage.prompt_tokens == 0 and usage.completion_tokens == 0 and usage.cost_usd == 0):
                row.actual_prompt_tokens = row.reserved_input_tokens
                row.actual_completion_tokens = row.reserved_output_tokens
                row.actual_cost_usd = row.reserved_cost_usd
                row.cost_source = "estimated"
            else:
                row.actual_prompt_tokens = usage.prompt_tokens
                row.actual_completion_tokens = usage.completion_tokens
                row.actual_cost_usd = float(usage.cost_usd)
                row.cost_source = usage.cost_source or "provider_reported"
                row.provider_request_id = usage.request_id
            row.status = status
            row.request_completed_at = utcnow()
            self._refresh_run(session, row.run_id)
            self._refresh_attempt(session, row.run_attempt_id)
            session.commit()
            session.refresh(row)
            return _entry_from_row(row)

    def fail(self, entry_id: str, *, error_category: str, error_message: str, usage: ModelUsage | None = None) -> LedgerEntry:
        entry = self.reconcile(entry_id, usage, status="failed")
        with self.session_factory() as session:
            row = session.get(ProviderCallLedger, entry_id)
            if row is None:
                return entry
            row.status = "failed"
            row.error_category = error_category
            row.error_message = error_message[:500]
            if usage is None:
                row.actual_prompt_tokens = row.reserved_input_tokens
                row.actual_completion_tokens = row.reserved_output_tokens
                row.actual_cost_usd = row.reserved_cost_usd
                row.cost_source = "estimated"
            self._refresh_run(session, row.run_id)
            self._refresh_attempt(session, row.run_attempt_id)
            session.commit()
            session.refresh(row)
            return _entry_from_row(row)

    def release(self, entry_id: str) -> LedgerEntry:
        with self.session_factory() as session:
            row = session.get(ProviderCallLedger, entry_id)
            if row is None:
                raise KeyError(entry_id)
            if row.status in {"succeeded", "failed"}:
                return _entry_from_row(row)
            row.status = "released"
            row.cost_source = "released"
            row.request_completed_at = utcnow()
            self._refresh_run(session, row.run_id)
            session.commit()
            session.refresh(row)
            return _entry_from_row(row)

    def totals(self, run_id: str) -> RunUsageTotals:
        with self.session_factory() as session:
            return self._totals(session, run_id)

    def attempt_totals(self, run_id: str, run_attempt_id: str) -> RunUsageTotals:
        with self.session_factory() as session:
            return self._attempt_totals(session, run_id, run_attempt_id)

    def entries(self, run_id: str) -> list[LedgerEntry]:
        with self.session_factory() as session:
            rows = session.scalars(select(ProviderCallLedger).where(ProviderCallLedger.run_id == run_id)).all()
            return [_entry_from_row(item) for item in rows]

    def _totals(self, session: Session, run_id: str) -> RunUsageTotals:
        rows = session.scalars(select(ProviderCallLedger).where(ProviderCallLedger.run_id == run_id)).all()
        attempts = session.scalars(select(ForecastRunAttempt).where(ForecastRunAttempt.run_id == run_id)).all()
        return summarize_entries([_entry_from_row(item) for item in rows], attempt_count=len(attempts))

    def _attempt_totals(self, session: Session, run_id: str, run_attempt_id: str) -> RunUsageTotals:
        rows = session.scalars(select(ProviderCallLedger).where(ProviderCallLedger.run_id == run_id)).all()
        return summarize_entries([_entry_from_row(item) for item in rows], run_attempt_id=run_attempt_id)

    def _refresh_run(self, session: Session, run_id: str) -> None:
        run = session.get(ForecastRun, run_id)
        if run is None:
            return
        apply_totals_to_run(run, self._totals(session, run_id))

    def _refresh_attempt(self, session: Session, run_attempt_id: str | None) -> None:
        if not run_attempt_id:
            return
        row = session.get(ForecastRunAttempt, run_attempt_id)
        if row is None:
            return
        apply_totals_to_attempt(row, self._attempt_totals(session, row.run_id, row.id))


def default_ledger() -> PersistentUsageLedger:
    from forecastlab_api.db import SessionLocal

    return PersistentUsageLedger(SessionLocal)
