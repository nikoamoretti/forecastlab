from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from forecastlab.errors import BudgetExceeded
from forecastlab.ledger import RunUsageTotals
from forecastlab.pricing import estimate_call_cost, estimate_search_cost
from forecastlab.schemas import BudgetState, ForecastProfile, ModelUsage
from forecastlab.timeutil import utcnow

DEFAULT_MAX_OUTPUT_TOKENS = 4096
DEFAULT_CALL_WALL_CLOCK_SECONDS = 5.0


def estimate_prompt_tokens(*parts: str) -> int:
    text = " ".join(part for part in parts if part)
    return max(1, (len(text) + 8) // 4)


@dataclass
class Reservation:
    id: str
    stage: str
    model_calls: int
    estimated_input_tokens: int
    max_output_tokens: int
    reserved_tokens: int
    estimated_cost_usd: float
    wall_clock_seconds: float
    actual_prompt_tokens: int | None = None
    actual_completion_tokens: int | None = None
    actual_tokens: int | None = None
    actual_cost_usd: float | None = None
    unused_tokens: int = 0
    unused_cost_usd: float = 0.0
    cost_source: str = "reserved"
    reconciled: bool = False
    released: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "stage": self.stage,
            "model_calls": self.model_calls,
            "estimated_input_tokens": self.estimated_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "reserved_tokens": self.reserved_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "wall_clock_seconds": self.wall_clock_seconds,
            "actual_prompt_tokens": self.actual_prompt_tokens,
            "actual_completion_tokens": self.actual_completion_tokens,
            "actual_tokens": self.actual_tokens,
            "actual_cost_usd": self.actual_cost_usd,
            "unused_tokens": self.unused_tokens,
            "unused_cost_usd": self.unused_cost_usd,
            "cost_source": self.cost_source,
            "reconciled": self.reconciled,
            "released": self.released,
        }


class Budget:
    def __init__(
        self,
        profile: ForecastProfile,
        *,
        provider: str = "mock",
        model: str = "mock-forecast-v1",
        search_provider: str = "mock",
        pricing_catalog: dict[str, Any] | None = None,
        prior_elapsed_seconds: float = 0.0,
    ) -> None:
        self.profile = profile
        self.provider = provider
        self.model = model
        self.search_provider = search_provider
        self.pricing_catalog = pricing_catalog
        self.prior_elapsed_seconds = max(0.0, prior_elapsed_seconds)
        self.state = BudgetState(started_monotonic=time.monotonic())
        self.reservations: list[Reservation] = []

    @classmethod
    def from_persisted(
        cls,
        profile: ForecastProfile,
        totals: RunUsageTotals,
        *,
        provider: str = "mock",
        model: str = "mock-forecast-v1",
        search_provider: str = "mock",
        pricing_catalog: dict[str, Any] | None = None,
        prior_elapsed_seconds: float = 0.0,
    ) -> Budget:
        budget = cls(
            profile,
            provider=provider,
            model=model,
            search_provider=search_provider,
            pricing_catalog=pricing_catalog,
            prior_elapsed_seconds=prior_elapsed_seconds,
        )
        budget.state.model_calls = totals.model_calls
        budget.state.search_calls = totals.search_calls
        budget.state.tokens = totals.total_tokens
        budget.state.prompt_tokens = totals.prompt_tokens
        budget.state.completion_tokens = totals.completion_tokens
        budget.state.cost_usd = totals.total_cost_usd
        budget.state.model_cost_usd = totals.model_cost_usd
        budget.state.search_cost_usd = totals.search_cost_usd
        budget.state.failed_attempt_cost_usd = totals.failed_attempt_cost_usd
        budget.state.cost_label = totals.cost_label
        budget.state.cost_is_estimated = totals.cost_label in {"estimated", "mixed", "unavailable"}
        budget.state.provider_request_count = totals.provider_request_count
        return budget

    def estimate_workload(self) -> dict[str, int | float]:
        if self.profile.execution_strategy == "graph_nodes":
            return {
                "tracks": 0,
                "subquestions": 0,
                "planned_search_calls": self.profile.max_search_calls,
                "planned_fetches": self.profile.max_fetched_documents,
                "planned_model_calls": self.profile.max_model_calls,
                "max_model_calls": self.profile.max_model_calls,
                "max_search_calls": self.profile.max_search_calls,
                "max_fetched_documents": self.profile.max_fetched_documents,
                "max_tokens": self.profile.max_tokens,
                "max_estimated_cost_usd": self.profile.max_estimated_cost_usd,
                "max_wall_clock_seconds": self.profile.max_wall_clock_seconds,
                "max_output_tokens_per_call": self.profile.max_output_tokens_per_call,
            }
        if self.profile.execution_strategy == "single_model":
            searches = 1 if self.profile.max_search_calls > 0 else 0
            fetches = (
                min(self.profile.fetches_per_subquestion, self.profile.max_fetched_documents)
                if searches and self.profile.max_fetched_documents > 0
                else 0
            )
            return {
                "tracks": 1,
                "subquestions": 0,
                "planned_search_calls": searches,
                "planned_fetches": fetches,
                "planned_model_calls": min(1, self.profile.max_model_calls),
                "max_model_calls": self.profile.max_model_calls,
                "max_search_calls": self.profile.max_search_calls,
                "max_fetched_documents": self.profile.max_fetched_documents,
                "max_tokens": self.profile.max_tokens,
                "max_estimated_cost_usd": self.profile.max_estimated_cost_usd,
                "max_wall_clock_seconds": self.profile.max_wall_clock_seconds,
                "max_output_tokens_per_call": self.profile.max_output_tokens_per_call,
            }
        tracks = len(self.profile.tracks)
        subq = tracks * self.profile.subquestions_per_track
        return {
            "tracks": tracks,
            "subquestions": subq,
            "planned_search_calls": subq,
            "planned_fetches": subq * self.profile.fetches_per_subquestion,
            "planned_model_calls": 1 + tracks + subq + tracks,
            "max_model_calls": self.profile.max_model_calls,
            "max_search_calls": self.profile.max_search_calls,
            "max_fetched_documents": self.profile.max_fetched_documents,
            "max_tokens": self.profile.max_tokens,
            "max_estimated_cost_usd": self.profile.max_estimated_cost_usd,
            "max_wall_clock_seconds": self.profile.max_wall_clock_seconds,
            "max_output_tokens_per_call": self.profile.max_output_tokens_per_call,
        }

    def _elapsed(self) -> float:
        return self.prior_elapsed_seconds + (time.monotonic() - self.state.started_monotonic)

    def _check_time(self, stage: str) -> None:
        if self._elapsed() > self.profile.max_wall_clock_seconds:
            self._stop(stage, "max_wall_clock_seconds")

    def _stop(self, stage: str, reason: str) -> None:
        self.state.stopped = True
        self.state.stop_stage = stage
        self.state.stop_reason = reason
        raise BudgetExceeded(stage, reason)

    def check(self, stage: str) -> None:
        if self.state.stopped:
            raise BudgetExceeded(self.state.stop_stage or stage, self.state.stop_reason or "stopped")
        self._check_time(stage)

    def max_output_tokens_for_call(self, estimated_input_tokens: int) -> int:
        remaining = self.profile.max_tokens - self.state.tokens
        allowed = remaining - max(0, estimated_input_tokens)
        return max(1, min(self.profile.max_output_tokens_per_call, allowed))

    def estimate_model_cost(self, estimated_input_tokens: int, max_output_tokens: int) -> float:
        return estimate_call_cost(
            self.provider,
            self.model,
            estimated_input_tokens,
            max_output_tokens,
            catalog=self.pricing_catalog,
        )

    def estimate_search_charge(self) -> tuple[float, str]:
        cost, label = estimate_search_cost(self.search_provider, catalog=self.pricing_catalog)
        return (0.0 if cost is None else cost, label)

    def reserve_model_call(
        self,
        stage: str,
        *,
        estimated_input_tokens: int,
        max_output_tokens: int,
        estimated_cost_usd: float | None = None,
        wall_clock_seconds: float = DEFAULT_CALL_WALL_CLOCK_SECONDS,
    ) -> Reservation:
        self.check(stage)
        reserved_tokens = max(0, estimated_input_tokens) + max(0, max_output_tokens)
        cost = self.estimate_model_cost(estimated_input_tokens, max_output_tokens) if estimated_cost_usd is None else estimated_cost_usd
        remaining_time = self.profile.max_wall_clock_seconds - self._elapsed()
        if remaining_time < max(0.0, wall_clock_seconds):
            self._stop(stage, "max_wall_clock_seconds")
        if self.state.model_calls + 1 > self.profile.max_model_calls:
            self._stop(stage, "max_model_calls")
        if self.state.tokens + reserved_tokens > self.profile.max_tokens:
            self._stop(stage, "max_tokens")
        if self.state.cost_usd + cost > self.profile.max_estimated_cost_usd + 1e-12:
            self._stop(stage, "max_estimated_cost_usd")
        reservation = Reservation(
            id=str(uuid.uuid4()),
            stage=stage,
            model_calls=1,
            estimated_input_tokens=estimated_input_tokens,
            max_output_tokens=max_output_tokens,
            reserved_tokens=reserved_tokens,
            estimated_cost_usd=cost,
            wall_clock_seconds=wall_clock_seconds,
        )
        self.state.model_calls += 1
        self.state.tokens += reserved_tokens
        self.state.cost_usd += cost
        self.state.model_cost_usd += cost
        self.state.reserved_tokens += reserved_tokens
        self.state.reserved_cost_usd += cost
        self.state.provider_request_count += 1
        self.reservations.append(reservation)
        return reservation

    def release_reservation(self, reservation: Reservation) -> None:
        if reservation.released or reservation.reconciled:
            return
        self.state.tokens = max(0, self.state.tokens - reservation.reserved_tokens)
        self.state.cost_usd = max(0.0, self.state.cost_usd - reservation.estimated_cost_usd)
        self.state.model_cost_usd = max(0.0, self.state.model_cost_usd - reservation.estimated_cost_usd)
        self.state.model_calls = max(0, self.state.model_calls - reservation.model_calls)
        self.state.reserved_tokens = max(0, self.state.reserved_tokens - reservation.reserved_tokens)
        self.state.reserved_cost_usd = max(0.0, self.state.reserved_cost_usd - reservation.estimated_cost_usd)
        self.state.provider_request_count = max(0, self.state.provider_request_count - 1)
        reservation.released = True
        reservation.cost_source = "released"

    def reconcile_model_call(self, reservation: Reservation, usage: ModelUsage | None) -> Reservation:
        if reservation.released or reservation.reconciled:
            return reservation
        if usage is None or (usage.prompt_tokens == 0 and usage.completion_tokens == 0):
            reservation.cost_source = "estimated"
            reservation.reconciled = True
            self.state.cost_is_estimated = True
            return reservation
        actual_tokens = usage.prompt_tokens + usage.completion_tokens
        actual_cost = float(usage.cost_usd)
        unused_tokens = max(0, reservation.reserved_tokens - actual_tokens)
        unused_cost = max(0.0, reservation.estimated_cost_usd - actual_cost)
        self.state.tokens = max(0, self.state.tokens - unused_tokens)
        self.state.cost_usd = max(0.0, self.state.cost_usd - unused_cost)
        self.state.model_cost_usd = max(0.0, self.state.model_cost_usd - unused_cost)
        if actual_tokens > reservation.reserved_tokens:
            self.state.tokens += actual_tokens - reservation.reserved_tokens
        if actual_cost > reservation.estimated_cost_usd:
            self.state.cost_usd += actual_cost - reservation.estimated_cost_usd
        reservation.actual_prompt_tokens = usage.prompt_tokens
        reservation.actual_completion_tokens = usage.completion_tokens
        reservation.actual_tokens = actual_tokens
        reservation.actual_cost_usd = actual_cost
        reservation.unused_tokens = unused_tokens
        reservation.unused_cost_usd = unused_cost
        reservation.cost_source = (
            usage.cost_source if "cost_source" in usage.model_fields_set else "provider_reported"
        )
        reservation.reconciled = True
        self.state.prompt_tokens += usage.prompt_tokens
        self.state.completion_tokens += usage.completion_tokens
        return reservation

    def add_model_call(self, stage: str, tokens: int, cost_usd: float) -> None:
        reservation = self.reserve_model_call(
            stage,
            estimated_input_tokens=max(0, tokens),
            max_output_tokens=0,
            estimated_cost_usd=cost_usd,
            wall_clock_seconds=0,
        )
        usage = ModelUsage(prompt_tokens=max(0, tokens), completion_tokens=0, cost_usd=cost_usd)
        self.reconcile_model_call(reservation, usage)

    def add_search(self, stage: str = "search") -> Reservation:
        self.check(stage)
        cost, label = self.estimate_search_charge()
        if self.state.search_calls + 1 > self.profile.max_search_calls:
            self._stop(stage, "max_search_calls")
        if self.state.cost_usd + cost > self.profile.max_estimated_cost_usd + 1e-12:
            self._stop(stage, "max_estimated_cost_usd")
        reservation = Reservation(
            id=str(uuid.uuid4()),
            stage=stage,
            model_calls=0,
            estimated_input_tokens=0,
            max_output_tokens=0,
            reserved_tokens=0,
            estimated_cost_usd=cost,
            wall_clock_seconds=0,
            cost_source=label,
        )
        self.state.search_calls += 1
        self.state.cost_usd += cost
        self.state.search_cost_usd += cost
        self.state.provider_request_count += 1
        if label == "estimated":
            self.state.cost_is_estimated = True
        self.reservations.append(reservation)
        reservation.reconciled = True
        return reservation

    def apply_physical_failure_cost(self, cost_usd: float, *, provider_type: str = "model") -> None:
        extra = max(0.0, cost_usd)
        self.state.cost_usd += extra
        self.state.failed_attempt_cost_usd += extra
        if provider_type == "search":
            self.state.search_cost_usd += extra
        else:
            self.state.model_cost_usd += extra
        self.state.provider_request_count += 1

    def add_fetch(self, stage: str = "fetch") -> None:
        self.check(stage)
        if self.state.fetches + 1 > self.profile.max_fetched_documents:
            self._stop(stage, "max_fetched_documents")
        self.state.fetches += 1

    def snapshot(self) -> dict[str, object]:
        return {
            **self.state.model_dump(),
            "elapsed_seconds": self._elapsed(),
            "now": utcnow().isoformat(),
            "estimate": self.estimate_workload(),
            "reservations": [item.as_dict() for item in self.reservations],
            "cost_is_estimated": self.state.cost_is_estimated,
            "cost_label": self.state.cost_label,
            "provider": self.provider,
            "model": self.model,
            "search_provider": self.search_provider,
            "total_cost_usd": self.state.cost_usd,
            "model_cost_usd": self.state.model_cost_usd,
            "search_cost_usd": self.state.search_cost_usd,
            "failed_attempt_cost_usd": self.state.failed_attempt_cost_usd,
        }
