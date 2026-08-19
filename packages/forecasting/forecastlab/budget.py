from __future__ import annotations

import time

from forecastlab.providers.base import BudgetExceeded
from forecastlab.schemas import BudgetState, ForecastProfile
from forecastlab.timeutil import utcnow


class Budget:
    def __init__(self, profile: ForecastProfile) -> None:
        self.profile = profile
        self.state = BudgetState(started_monotonic=time.monotonic())

    def estimate_workload(self) -> dict[str, int | float]:
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
        }

    def _check_time(self, stage: str) -> None:
        elapsed = time.monotonic() - self.state.started_monotonic
        if elapsed > self.profile.max_wall_clock_seconds:
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

    def add_model_call(self, stage: str, tokens: int, cost_usd: float) -> None:
        self.check(stage)
        self.state.model_calls += 1
        self.state.tokens += tokens
        self.state.cost_usd += cost_usd
        if self.state.model_calls > self.profile.max_model_calls:
            self._stop(stage, "max_model_calls")
        if self.state.tokens > self.profile.max_tokens:
            self._stop(stage, "max_tokens")
        if self.state.cost_usd > self.profile.max_estimated_cost_usd:
            self._stop(stage, "max_estimated_cost_usd")

    def add_search(self, stage: str = "search") -> None:
        self.check(stage)
        self.state.search_calls += 1
        if self.state.search_calls > self.profile.max_search_calls:
            self._stop(stage, "max_search_calls")

    def add_fetch(self, stage: str = "fetch") -> None:
        self.check(stage)
        self.state.fetches += 1
        if self.state.fetches > self.profile.max_fetched_documents:
            self._stop(stage, "max_fetched_documents")

    def snapshot(self) -> dict[str, object]:
        return {
            **self.state.model_dump(),
            "elapsed_seconds": time.monotonic() - self.state.started_monotonic,
            "now": utcnow().isoformat(),
            "estimate": self.estimate_workload(),
        }
