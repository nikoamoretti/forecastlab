"""One monotonic execution deadline shared by paid calls and physical retries."""
from __future__ import annotations

import time
from dataclasses import dataclass

from forecastlab.errors import BudgetExceeded


@dataclass(frozen=True)
class ExecutionDeadline:
    end_monotonic: float

    @classmethod
    def after(cls, seconds: float) -> ExecutionDeadline:
        return cls(time.monotonic() + max(0.0, seconds))

    def remaining(self, stage: str) -> float:
        seconds = self.end_monotonic - time.monotonic()
        if seconds <= 0:
            raise BudgetExceeded(stage, "max_wall_clock_seconds")
        return seconds


def ledger_deadline(ledger: object | None) -> ExecutionDeadline | None:
    deadline = getattr(ledger, "deadline", None)
    return deadline if isinstance(deadline, ExecutionDeadline) else None


def request_timeout(ledger: object | None, default: float, stage: str) -> float:
    deadline = ledger_deadline(ledger)
    return min(default, deadline.remaining(stage)) if deadline else default


def check_deadline(ledger: object | None, stage: str) -> None:
    deadline = ledger_deadline(ledger)
    if deadline:
        deadline.remaining(stage)
