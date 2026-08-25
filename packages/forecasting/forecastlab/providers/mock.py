from __future__ import annotations

import json
import re
import uuid
from typing import Any, Literal

from forecastlab.ledger import UsageLedger
from forecastlab.providers.base import ChatResult
from forecastlab.schemas import ModelUsage, SearchHit
from forecastlab.timeutil import parse_datetime

SAMPLE_QUESTION = "Will the US unemployment rate exceed 5% before 30 June 2027?"

UNEMPLOYMENT_CONTRACT = {
    "exact_yes": (
        "The US Bureau of Labor Statistics U-3 unemployment rate, seasonally adjusted, "
        "is reported at or above 5.0% for any month whose official release date is on or before 30 June 2027."
    ),
    "exact_no": (
        "No BLS U-3 seasonally adjusted monthly reading at or above 5.0% is published with a "
        "release date on or before 30 June 2027."
    ),
    "resolution_deadline": "2027-07-15T00:00:00+00:00",
    "authoritative_source": "https://fixtures.forecastlab.local/bls-employment-situation",
    "fallback_sources": [
        "https://fixtures.forecastlab.local/fred-unrate",
        "https://fixtures.forecastlab.local/cbo-outlook",
    ],
    "geography": "United States",
    "units": "percent, seasonally adjusted U-3",
    "ambiguity_notes": (
        "Revisions after the first official monthly release do not count unless BLS restates the "
        "first-release figure before the deadline. Payroll vs U-3 is out of scope."
    ),
    "cancellation_conditions": (
        "Invalidate if BLS discontinues U-3 or the federal statistical system cannot publish "
        "a comparable series before the deadline."
    ),
    "resolver_risk_notes": (
        "A resolver who uses household-survey U-3 should not substitute U-6 or a three-month average. "
        "First-release vs revised prints can differ by a tenth of a point."
    ),
}


def _prompt_id(system: str) -> str:
    match = re.search(r"PROMPT_ID:\s*(\S+)", system)
    return match.group(1) if match else "unknown"


def _usage(model: str, content: str) -> ModelUsage:
    tokens = max(32, len(content) // 4)
    return ModelUsage(
        prompt_tokens=tokens,
        completion_tokens=tokens,
        cost_usd=0.0,
        latency_ms=12,
        model=model,
        provider="mock",
    )


class MockModelProvider:
    name = "mock"

    def __init__(
        self,
        model: str = "mock-forecast-v1",
        *,
        ledger: UsageLedger | None = None,
        run_id: str | None = None,
        run_attempt_id: str | None = None,
    ) -> None:
        self.model = model
        self.ledger = ledger
        self.run_id = run_id
        self.run_attempt_id = run_attempt_id

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        temperature: float = 0.2,
        timeout: float | None = None,
        max_output_tokens: int | None = None,
        estimated_input_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None,
    ) -> ChatResult:
        prompt_id = _prompt_id(system)
        payload = self._payload(prompt_id, user, schema_name)
        content = json.dumps(payload)
        usage = _usage(self.model, content)
        result = ChatResult(content=content, parsed=payload, usage=usage)
        if self.ledger is not None and self.run_id:
            entry = self.ledger.reserve(
                run_id=self.run_id,
                run_attempt_id=self.run_attempt_id,
                logical_call_id=str(uuid.uuid4()),
                physical_attempt_number=1,
                stage=schema_name,
                provider_type="model",
                provider=self.name,
                model=self.model,
                reserved_input_tokens=usage.prompt_tokens,
                reserved_output_tokens=usage.completion_tokens,
                reserved_cost_usd=usage.cost_usd,
            )
            self.ledger.reconcile(entry.id, usage)
        return result

    def _payload(self, prompt_id: str, user: str, schema_name: str) -> dict[str, Any]:
        if "operationalize" in prompt_id or schema_name == "resolution_contract":
            return dict(UNEMPLOYMENT_CONTRACT)
        if schema_name == "graph_research_plan":
            return self._graph_research_plan(user)
        if "plan" in prompt_id or schema_name == "research_plan":
            return self._plan(user)
        if "extract" in prompt_id or schema_name == "evidence_extract":
            return self._extract(user)
        if schema_name == "forecast_node":
            return self._forecast_node(user)
        if schema_name == "single_model_forecast":
            return self._single_model_forecast(user)
        if "forecast" in prompt_id or schema_name == "track_forecast":
            return self._forecast(user)
        if "disagreement" in prompt_id or schema_name == "disagreement_summary":
            return {
                "summary": (
                    "The base-rate track is more cautious because postwar expansions rarely break 5% U-3 "
                    "quickly from a mid-4s starting point. Current-evidence is lower because the latest "
                    "prints and vacancies still look orderly. The skeptic raises resolver and revision risk "
                    "without finding a decisive overturning fact."
                )
            }
        return {"note": "mock_unrecognized_prompt", "prompt_id": prompt_id}

    def _plan(self, user: str) -> dict[str, Any]:
        if "base_rate" in user:
            track = "base_rate"
            questions = [
                (
                    "What is the historical frequency of U-3 crossing 5% within 24 months from similar levels?",
                    "Establish the reference class.",
                    ["official statistics", "NBER chronology"],
                ),
                (
                    "How often do expansions of this length end in a labor-market break?",
                    "Condition the prior on expansion age.",
                    ["NBER", "academic reviews"],
                ),
                (
                    "What base-rate adjustments are implied by current labor-force composition?",
                    "Note structural differences vs the 1970s-80s class.",
                    ["BLS", "CBO"],
                ),
                (
                    "Which historical analogues failed to reach 5% despite similar warnings?",
                    "Avoid recency-only priors.",
                    ["BLS", "Fed reviews"],
                ),
            ]
        elif "skeptic" in user:
            track = "skeptic"
            questions = [
                (
                    "What would make the official resolver score this as no even if unemployment feels high?",
                    "Resolver-risk mapping.",
                    ["BLS definitions", "FAQ"],
                ),
                (
                    "Which correlated assumptions are hiding in a 'soft landing continues' story?",
                    "Find shared-cause failure modes.",
                    ["Fed", "CBO"],
                ),
                (
                    "What data revisions or definition changes have flipped similar questions?",
                    "Look for overturning technicalities.",
                    ["BLS technical notes"],
                ),
                (
                    "Which leading indicators currently contradict a 5% breach?",
                    "Steelman the opposite case.",
                    ["JOLTS", "claims"],
                ),
            ]
        else:
            track = "current_evidence"
            questions = [
                (
                    "What is the latest official U-3 print and the recent trend?",
                    "Anchor on primary data.",
                    ["BLS CPS"],
                ),
                (
                    "What do claims, JOLTS, and payrolls imply about the next 12-18 months?",
                    "Map leading indicators.",
                    ["BLS", "DOL"],
                ),
                (
                    "What do CBO or Fed staff projections say about crossing 5% by mid-2027?",
                    "Institutional outlooks as secondary evidence.",
                    ["CBO", "FOMC SEP"],
                ),
                (
                    "Which policy-rate and demand signals would most quickly lift unemployment?",
                    "Identify drivers, not narratives.",
                    ["Fed", "BEA"],
                ),
            ]
        subquestions = []
        for text, purpose, sources in questions:
            subquestions.append(
                {
                    "text": text,
                    "purpose": purpose,
                    "preferred_source_types": sources,
                    "search_queries": [text, f"{track} US unemployment 5 percent 2027"],
                    "expected_output": "A sourced quantitative or definitional finding.",
                    "relationship_to_forecast": purpose,
                }
            )
        return {
            "objective": f"Independent {track} investigation of whether U-3 exceeds 5% before mid-2027.",
            "approach": "Generate targeted subquestions, prefer primary statistical agencies, then estimate.",
            "subquestions": subquestions,
        }

    def _graph_research_plan(self, user: str) -> dict[str, Any]:
        try:
            payload = json.loads(user)
            node = payload.get("node") or {}
        except (json.JSONDecodeError, TypeError, AttributeError):
            node = {}
        question = str(node.get("question") or "What evidence bears on this forecast node?")
        preferred_sources = [
            str(value)
            for value in node.get("preferred_sources") or []
            if str(value).strip()
        ]
        source_queries = [f"{question} {source}" for source in preferred_sources[:2]]
        return {
            "primary_research_question": question,
            "supporting_search_queries": [
                question,
                *source_queries,
                f"{question} official dated evidence",
            ][:4],
            "preferred_sources": preferred_sources or ["official primary sources"],
            "required_evidence_types": [
                str(node.get("required_output_type") or "dated factual finding").replace(
                    "_", " "
                ),
                "dated primary-source excerpt",
            ],
        }

    def _extract(self, user: str) -> dict[str, Any]:
        excerpt = "Excerpt unavailable"
        if "U-3" in user or "unemployment" in user.lower():
            excerpt = "The seasonally adjusted U-3 unemployment rate is the official BLS headline measure."
        return {
            "excerpt": excerpt[:800],
            "published_at": "2024-06-07T00:00:00+00:00",
            "publisher": "Fixture statistical agency",
            "source_class": "primary" if "bls" in user.lower() else "secondary",
            "notes": "Extracted from supplied document text only.",
        }

    def _forecast_node(self, user: str) -> dict[str, Any]:
        try:
            payload = json.loads(user)
            claims = payload.get("evidence_claims") or []
        except (json.JSONDecodeError, TypeError, AttributeError):
            claims = []
        supporting = [str(claim["id"]) for claim in claims if claim.get("supports_or_refutes") == "supports"]
        opposing = [str(claim["id"]) for claim in claims if claim.get("supports_or_refutes") == "refutes"]
        total = len(supporting) + len(opposing)
        directional_balance = (len(supporting) - len(opposing)) / total if total else 0.0
        probability = min(0.95, max(0.05, 0.5 + 0.1 * directional_balance))
        return {
            "probability": probability,
            "reasoning": (
                f"The mock node forecast cites {len(supporting)} supporting and {len(opposing)} opposing "
                "provenance-linked Evidence Claims."
            ),
            "supporting_claim_ids": supporting,
            "opposing_claim_ids": opposing,
            "uncertainty_notes": ["Synthetic mock evidence is not real-world forecasting evidence."],
        }

    def _single_model_forecast(self, user: str) -> dict[str, Any]:
        try:
            payload = json.loads(user)
            packet = payload.get("evidence_packet") or []
        except (json.JSONDecodeError, TypeError, AttributeError):
            packet = []
        evidence_ids = [str(item["id"]) for item in packet if isinstance(item, dict) and item.get("id")]
        return {
            "probability": 0.36,
            "reasoning": (
                "The mock single-model baseline evaluates one approved Forecast Contract against "
                f"one packet containing {len(evidence_ids)} provenance-bearing evidence excerpts."
            ),
            "uncertainty": ["Synthetic mock evidence is not real-world forecasting evidence."],
            "evidence_ids": evidence_ids[:2],
        }

    def _forecast(self, user: str) -> dict[str, Any]:
        evidence_ids: list[str] = []
        try:
            payload = json.loads(user)
            evidence_ids = [str(item["id"]) for item in payload.get("evidence") or [] if "id" in item]
        except (json.JSONDecodeError, TypeError, AttributeError):
            evidence_ids = []
        cited = evidence_ids[:2]
        inference = not cited
        if "base_rate" in user:
            p, prior, quality = 0.42, 0.40, 0.78
            drivers = [
                {
                    "factor": "Postwar frequency of 5% U-3 from mid-4s within two years",
                    "direction": "up",
                    "importance": 0.7,
                    "evidence_ids": cited,
                    "inference": inference,
                }
            ]
            summary = (
                "Historical analogues from similar unemployment levels put a 5% breach before mid-2027 "
                "in a moderate band. The present cycle is longer than average, which slightly raises the prior."
            )
        elif "skeptic" in user:
            p, prior, quality = 0.38, 0.42, 0.64
            drivers = [
                {
                    "factor": "Resolver could require a first-release U-3 print, not a narrative recession",
                    "direction": "down",
                    "importance": 0.55,
                    "evidence_ids": cited,
                    "inference": inference,
                }
            ]
            summary = (
                "The skeptic track does not find a hidden near-certainty of a 5% print, but it does find "
                "non-trivial resolver and revision risk that should keep the probability away from extremes."
            )
        else:
            p, prior, quality = 0.31, 0.42, 0.74
            drivers = [
                {
                    "factor": "Latest official prints remain below 5% with an orderly trend",
                    "direction": "down",
                    "importance": 0.8,
                    "evidence_ids": cited,
                    "inference": inference,
                }
            ]
            summary = (
                "Current primary labor-market prints and institutional outlooks do not show a path that "
                "makes a 5% U-3 print before June 2027 the modal outcome."
            )
        return {
            "probability": p,
            "prior_probability": prior,
            "key_drivers": drivers,
            "counterarguments": [
                "A rapid demand shock or delayed recession timing could still push U-3 through 5% inside the window."
            ],
            "unresolved_uncertainties": [
                "Policy reaction function if inflation reaccelerates",
                "Whether BLS first-release prints or revisions bind the resolver",
            ],
            "resolver_risk": 0.08 if "skeptic" in user else 0.04,
            "evidence_quality": quality,
            "reasoning_summary": summary,
        }


def mock_search_hits(query: str, *, max_results: int = 3) -> list[SearchHit]:
    catalog = [
        SearchHit(
            title="BLS Employment Situation technical note",
            url="https://fixtures.forecastlab.local/bls-employment-situation",
            snippet="U-3 is the official seasonally adjusted unemployment rate from the CPS.",
            published_at=parse_datetime("2024-06-07"),
            score=0.9,
            source_class="primary",
        ),
        SearchHit(
            title="FRED UNRATE series description",
            url="https://fixtures.forecastlab.local/fred-unrate",
            snippet="Monthly civilian unemployment rate, seasonally adjusted, sourced from BLS.",
            published_at=parse_datetime("2024-06-01"),
            score=0.8,
            source_class="secondary",
        ),
        SearchHit(
            title="CBO economic outlook labor market chapter",
            url="https://fixtures.forecastlab.local/cbo-outlook",
            snippet="Projection discussion for unemployment over the next two years.",
            published_at=parse_datetime("2024-02-15"),
            score=0.7,
            source_class="secondary",
        ),
        SearchHit(
            title="NBER US business cycle chronology",
            url="https://fixtures.forecastlab.local/nber-cycles",
            snippet="Reference dates for US expansions and contractions.",
            published_at=parse_datetime("2023-12-01"),
            score=0.6,
            source_class="secondary",
        ),
        SearchHit(
            title="JOLTS job openings and labor turnover",
            url="https://fixtures.forecastlab.local/jolts",
            snippet="Job openings, hires, and separations as labor-market indicators.",
            published_at=parse_datetime("2024-05-28"),
            score=0.65,
            source_class="primary",
        ),
    ]
    q = query.lower()
    scored = []
    for hit in catalog:
        bonus = 0.0
        if "bls" in q and "bls" in hit.url:
            bonus += 1
        if "cbo" in q and "cbo" in hit.url:
            bonus += 1
        if "nber" in q and "nber" in hit.url:
            bonus += 1
        if "jolts" in q and "jolts" in hit.url:
            bonus += 1
        scored.append((hit.score + bonus, hit))
    scored.sort(key=lambda item: (-item[0], item[1].url))
    return [item[1] for item in scored[:max_results]]


class MockSearchProvider:
    name = "mock"

    def __init__(
        self,
        *,
        ledger: UsageLedger | None = None,
        run_id: str | None = None,
        run_attempt_id: str | None = None,
    ) -> None:
        self.ledger = ledger
        self.run_id = run_id
        self.run_attempt_id = run_attempt_id

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        hits = mock_search_hits(query, max_results=max_results)
        if self.ledger is not None and self.run_id:
            usage = ModelUsage(provider="mock", model="mock-search", cost_source="estimated")
            entry = self.ledger.reserve(
                run_id=self.run_id,
                run_attempt_id=self.run_attempt_id,
                logical_call_id=str(uuid.uuid4()),
                physical_attempt_number=1,
                stage="search",
                provider_type="search",
                provider=self.name,
                model="mock-search",
                reserved_input_tokens=0,
                reserved_output_tokens=0,
                reserved_cost_usd=0.0,
            )
            self.ledger.reconcile(entry.id, usage)
        return hits
