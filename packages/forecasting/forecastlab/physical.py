from __future__ import annotations

import time
from collections.abc import Callable

from forecastlab.deadline import check_deadline, request_timeout
from forecastlab.errors import TransientProviderError, UnknownProviderResult
from forecastlab.ledger import LedgerEntry, UsageLedger
from forecastlab.schemas import ModelUsage


def run_physical_attempts[T](
    *,
    ledger: UsageLedger | None,
    run_id: str | None,
    run_attempt_id: str | None,
    logical_call_id: str,
    stage: str,
    provider_type: str,
    provider: str,
    model: str | None,
    reserved_input_tokens: int,
    reserved_output_tokens: int,
    reserved_cost_usd: float,
    send: Callable[[int], tuple[T, ModelUsage | None]],
    max_attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    last_error: Exception | None = None
    for physical in range(1, max_attempts + 1):
        check_deadline(ledger, stage)
        entry: LedgerEntry | None = None
        if ledger is not None and run_id:
            entry = ledger.reserve(
                run_id=run_id,
                run_attempt_id=run_attempt_id,
                logical_call_id=logical_call_id,
                physical_attempt_number=physical,
                stage=stage,
                provider_type=provider_type,
                provider=provider,
                model=model,
                reserved_input_tokens=reserved_input_tokens,
                reserved_output_tokens=reserved_output_tokens,
                reserved_cost_usd=reserved_cost_usd,
            )
        try:
            result, usage = send(physical)
        except TransientProviderError as exc:
            last_error = exc
            if entry is not None and ledger is not None:
                ledger.fail(
                    entry.id,
                    error_category="TransientProviderError",
                    error_message=str(exc),
                )
            if getattr(ledger, "reconcile_ambiguous", False) and exc.__cause__ is not None and type(exc.__cause__).__name__ in {
                "ReadTimeout", "WriteTimeout", "ReadError", "WriteError", "RemoteProtocolError"}:
                raise UnknownProviderResult("interrupted_provider_result_requires_reconciliation") from exc
            if physical >= max_attempts:
                raise
            sleep(request_timeout(ledger, min(2 ** (physical - 1), 8), stage))
            continue
        except Exception as exc:
            if entry is not None and ledger is not None:
                ledger.fail(
                    entry.id,
                    error_category=exc.__class__.__name__,
                    error_message=str(exc),
                )
            raise
        if entry is not None and ledger is not None:
            ledger.reconcile(entry.id, usage, status="succeeded")
        # Retain the charged response even if it arrived too late to be used.
        check_deadline(ledger, stage)
        return result
    assert last_error is not None
    raise last_error
