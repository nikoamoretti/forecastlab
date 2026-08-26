from __future__ import annotations

import threading
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
    call_kind: str | None = None
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
            "call_kind": self.call_kind,
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


@dataclass
class ModelCallEnvelope:
    """Frozen logical-call quotas that protect graph node forecasts."""

    planner_version: str
    baseline_model_calls: int
    max_model_calls: int
    planned_calls_by_kind: dict[str, int]
    used_calls_by_kind: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        planned_total = sum(self.planned_calls_by_kind.values())
        used_total = sum(self.used_calls_by_kind.values())
        remaining = {
            kind: max(0, planned - self.used_calls_by_kind.get(kind, 0))
            for kind, planned in self.planned_calls_by_kind.items()
        }
        return {
            "planner_version": self.planner_version,
            "baseline_model_calls": self.baseline_model_calls,
            "max_model_calls": self.max_model_calls,
            "planned_calls_by_kind": dict(self.planned_calls_by_kind),
            "used_calls_by_kind": dict(self.used_calls_by_kind),
            "remaining_calls_by_kind": remaining,
            "planned_total_model_calls": planned_total,
            "used_total_model_calls": used_total,
            "reserved_node_forecast_calls": self.planned_calls_by_kind.get(
                "node_forecast",
                0,
            ),
            "remaining_node_forecast_calls": remaining.get("node_forecast", 0),
            "reserved_scenario_synthesis_calls": self.planned_calls_by_kind.get(
                "scenario_synthesis",
                0,
            ),
            "remaining_scenario_synthesis_calls": remaining.get(
                "scenario_synthesis",
                0,
            ),
            "model_call_headroom_after_plan": max(
                0,
                self.max_model_calls
                - self.baseline_model_calls
                - planned_total,
            ),
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
        self.model_call_envelope: ModelCallEnvelope | None = None
        self._lock = threading.RLock()

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

    def estimate_workload(self) -> dict[str, Any]:
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
                "graph_generation_max_completion_tokens": (
                    self.profile.graph_generation_max_completion_tokens
                    or self.profile.max_output_tokens_per_call
                ),
                "graph_generation_max_visible_output_tokens": (
                    self.profile.graph_generation_max_visible_output_tokens
                    or self.profile.max_output_tokens_per_call
                ),
                "graph_generation_transport": (
                    self.profile.graph_generation_transport
                ),
                "graph_generation_transport_max_characters": (
                    self.profile.graph_generation_transport_max_characters
                ),
                "max_candidate_fetch_attempts_per_node": (
                    self.profile.max_candidate_fetch_attempts_per_node
                    or self.profile.fetches_per_subquestion
                ),
                "search_candidate_pool_per_node": (
                    self.profile.search_candidate_pool_per_node
                    or self.profile.search_results_per_subquestion
                ),
                "prefer_distinct_candidate_hosts": (
                    self.profile.prefer_distinct_candidate_hosts
                ),
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
        with self._lock:
            if self.state.stopped:
                raise BudgetExceeded(
                    self.state.stop_stage or stage,
                    self.state.stop_reason or "stopped",
                )
            self._check_time(stage)

    def max_output_tokens_for_call(self, estimated_input_tokens: int) -> int:
        with self._lock:
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

    def freeze_model_call_envelope(
        self,
        *,
        planner_version: str,
        planned_calls_by_kind: dict[str, int],
        stage: str = "research_planning",
    ) -> ModelCallEnvelope:
        """Freeze graph logical-call quotas without creating provider usage."""

        with self._lock:
            self.check(stage)
            normalized = {
                str(kind): int(count)
                for kind, count in planned_calls_by_kind.items()
            }
            if not normalized or any(count < 0 for count in normalized.values()):
                raise ValueError("invalid_model_call_envelope")
            planned_total = sum(normalized.values())
            available = max(
                0,
                self.profile.max_model_calls - self.state.model_calls,
            )
            if planned_total > available:
                self._stop(stage, "planned_model_calls_exceed_budget")
            candidate = ModelCallEnvelope(
                planner_version=planner_version,
                baseline_model_calls=self.state.model_calls,
                max_model_calls=self.profile.max_model_calls,
                planned_calls_by_kind=normalized,
                used_calls_by_kind={kind: 0 for kind in normalized},
            )
            if self.model_call_envelope is not None:
                existing = self.model_call_envelope
                if (
                    existing.planner_version != candidate.planner_version
                    or existing.baseline_model_calls
                    != candidate.baseline_model_calls
                    or existing.max_model_calls != candidate.max_model_calls
                    or existing.planned_calls_by_kind
                    != candidate.planned_calls_by_kind
                ):
                    raise ValueError("conflicting_model_call_envelope")
                return existing
            self.model_call_envelope = candidate
            return candidate

    def _check_model_call_envelope(
        self,
        *,
        stage: str,
        call_kind: str | None,
    ) -> None:
        envelope = self.model_call_envelope
        if envelope is None:
            return
        if call_kind is None or call_kind not in envelope.planned_calls_by_kind:
            raise BudgetExceeded(stage, "unplanned_model_call")
        assert call_kind is not None
        planned = envelope.planned_calls_by_kind[call_kind]
        used = envelope.used_calls_by_kind[call_kind]
        if used >= planned:
            raise BudgetExceeded(stage, f"unplanned_{call_kind}_call")
        protected_call_kinds = ("node_forecast", "scenario_synthesis")
        remaining_protected: dict[str, int] = {}
        for protected_kind in protected_call_kinds:
            remaining_for_kind = max(
                0,
                envelope.planned_calls_by_kind.get(protected_kind, 0)
                - envelope.used_calls_by_kind.get(protected_kind, 0)
                - (1 if call_kind == protected_kind else 0),
            )
            remaining_protected[protected_kind] = remaining_for_kind
        if (
            self.state.model_calls + 1
            > self.profile.max_model_calls - sum(remaining_protected.values())
        ):
            reason = (
                "reserved_scenario_synthesis_call"
                if remaining_protected["scenario_synthesis"] > 0
                else "reserved_node_forecast_calls"
            )
            raise BudgetExceeded(stage, reason)

    def _consume_model_call_envelope(self, call_kind: str | None) -> None:
        if self.model_call_envelope is None:
            return
        assert call_kind is not None
        self.model_call_envelope.used_calls_by_kind[call_kind] += 1

    def reserve_model_call(
        self,
        stage: str,
        *,
        estimated_input_tokens: int,
        max_output_tokens: int,
        estimated_cost_usd: float | None = None,
        wall_clock_seconds: float = DEFAULT_CALL_WALL_CLOCK_SECONDS,
        call_kind: str | None = None,
    ) -> Reservation:
        with self._lock:
            self.check(stage)
            self._check_model_call_envelope(stage=stage, call_kind=call_kind)
            reserved_tokens = max(0, estimated_input_tokens) + max(
                0,
                max_output_tokens,
            )
            cost = (
                self.estimate_model_cost(estimated_input_tokens, max_output_tokens)
                if estimated_cost_usd is None
                else estimated_cost_usd
            )
            remaining_time = self.profile.max_wall_clock_seconds - self._elapsed()
            if remaining_time < max(0.0, wall_clock_seconds):
                self._stop(stage, "max_wall_clock_seconds")
            if self.state.model_calls + 1 > self.profile.max_model_calls:
                self._stop(stage, "max_model_calls")
            if self.state.tokens + reserved_tokens > self.profile.max_tokens:
                self._stop(stage, "max_tokens")
            if (
                self.state.cost_usd + cost
                > self.profile.max_estimated_cost_usd + 1e-12
            ):
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
                call_kind=call_kind,
            )
            self.state.model_calls += 1
            self.state.tokens += reserved_tokens
            self.state.cost_usd += cost
            self.state.model_cost_usd += cost
            self.state.reserved_tokens += reserved_tokens
            self.state.reserved_cost_usd += cost
            self.state.provider_request_count += 1
            self.reservations.append(reservation)
            self._consume_model_call_envelope(call_kind)
            return reservation

    def release_reservation(self, reservation: Reservation) -> None:
        with self._lock:
            if reservation.released or reservation.reconciled:
                return
            # A provider exception releases the existing token/cost reservation,
            # but it does not return the frozen logical-call slot. The external
            # call was admitted and another phase may not reuse that attempt.
            self.state.tokens = max(
                0,
                self.state.tokens - reservation.reserved_tokens,
            )
            self.state.cost_usd = max(
                0.0,
                self.state.cost_usd - reservation.estimated_cost_usd,
            )
            self.state.model_cost_usd = max(
                0.0,
                self.state.model_cost_usd - reservation.estimated_cost_usd,
            )
            self.state.model_calls = max(
                0,
                self.state.model_calls - reservation.model_calls,
            )
            self.state.reserved_tokens = max(
                0,
                self.state.reserved_tokens - reservation.reserved_tokens,
            )
            self.state.reserved_cost_usd = max(
                0.0,
                self.state.reserved_cost_usd - reservation.estimated_cost_usd,
            )
            self.state.provider_request_count = max(
                0,
                self.state.provider_request_count - 1,
            )
            reservation.released = True
            reservation.cost_source = "released"

    def reconcile_model_call(self, reservation: Reservation, usage: ModelUsage | None) -> Reservation:
        with self._lock:
            if reservation.released or reservation.reconciled:
                return reservation
            if usage is None or (
                usage.prompt_tokens == 0 and usage.completion_tokens == 0
            ):
                reservation.cost_source = "estimated"
                reservation.reconciled = True
                self.state.cost_is_estimated = True
                return reservation
            actual_tokens = usage.prompt_tokens + usage.completion_tokens
            actual_cost = float(usage.cost_usd)
            unused_tokens = max(0, reservation.reserved_tokens - actual_tokens)
            unused_cost = max(
                0.0,
                reservation.estimated_cost_usd - actual_cost,
            )
            self.state.tokens = max(0, self.state.tokens - unused_tokens)
            self.state.cost_usd = max(0.0, self.state.cost_usd - unused_cost)
            self.state.model_cost_usd = max(
                0.0,
                self.state.model_cost_usd - unused_cost,
            )
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
                usage.cost_source
                if "cost_source" in usage.model_fields_set
                else "provider_reported"
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
        with self._lock:
            self.check(stage)
            cost, label = self.estimate_search_charge()
            if self.state.search_calls + 1 > self.profile.max_search_calls:
                self._stop(stage, "max_search_calls")
            if (
                self.state.cost_usd + cost
                > self.profile.max_estimated_cost_usd + 1e-12
            ):
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
        with self._lock:
            extra = max(0.0, cost_usd)
            self.state.cost_usd += extra
            self.state.failed_attempt_cost_usd += extra
            if provider_type == "search":
                self.state.search_cost_usd += extra
            else:
                self.state.model_cost_usd += extra
            self.state.provider_request_count += 1

    def add_fetch(self, stage: str = "fetch") -> None:
        with self._lock:
            self.check(stage)
            if self.state.fetches + 1 > self.profile.max_fetched_documents:
                self._stop(stage, "max_fetched_documents")
            self.state.fetches += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                **self.state.model_dump(),
                "elapsed_seconds": self._elapsed(),
                "now": utcnow().isoformat(),
                "estimate": self.estimate_workload(),
                "reservations": [item.as_dict() for item in self.reservations],
                "model_call_envelope": (
                    self.model_call_envelope.as_dict()
                    if self.model_call_envelope is not None
                    else None
                ),
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
