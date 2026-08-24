from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from forecastlab.budget import Budget
from forecastlab.fetch import FIXTURE_PAGES, FIXTURES_DIR, _extract_html, _published_from_html, fetch_document
from forecastlab.hashing import canonical_json, content_hash, sha256_text
from forecastlab.ledger import UsageLedger
from forecastlab.pricing import pricing_hash
from forecastlab.research_planning import ResearchPlan, ResearchPlanner
from forecastlab.schemas import FetchedDocument, ForecastGraph, ModelUsage, SearchHit
from forecastlab.timeutil import as_utc
from forecastlab.wayback import WaybackSnapshot
from forecastlab_api.config import ROOT

CORPUS_ID = "forecastlab.graph_validation.cutoff_consistent"
CORPUS_VERSION = 1
CORPUS_DIR = ROOT / "fixtures" / "backtests" / "graph_validation_v1"
MANIFEST_PATH = CORPUS_DIR / "manifest.json"
FIXTURE_URL_PREFIX = (
    "https://fixtures.forecastlab.local/backtests/graph_validation_v1/v1/"
)

SHADOW_MODEL_PROVIDER = "openai"
SHADOW_MODEL = "gpt-5-mini-2025-08-07"
SHADOW_SEARCH_PROVIDER = "tavily"
SHADOW_COST_CEILING_USD = 0.25

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_FIXTURE_REGISTRATION_LOCK = threading.Lock()


class CutoffConsistentMockError(RuntimeError):
    """The frozen validation corpus or its explicit execution binding is invalid."""


class CorpusDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question_hash: str = Field(min_length=64, max_length=64)
    domain: str = Field(min_length=1)
    category: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    canonical_source_url: str = Field(min_length=1)
    fixture_url: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    source_class: Literal["primary", "secondary"]
    published_at: datetime
    snapshot_at: datetime
    query_terms: list[str] = Field(min_length=1)
    content_file: str = Field(min_length=1)
    content_hash: str = Field(min_length=64, max_length=64)
    outcome_blind: bool
    synthetic_workflow_only: bool

    @field_validator("query_terms")
    @classmethod
    def normalize_query_terms(cls, values: list[str]) -> list[str]:
        normalized = [" ".join(value.split()).strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("blank_corpus_query_term")
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_fixture_identity(self) -> CorpusDocument:
        if not self.fixture_url.startswith(FIXTURE_URL_PREFIX):
            raise ValueError("validation_fixture_url_prefix_required")
        if self.fixture_url == self.canonical_source_url:
            raise ValueError("fixture_and_canonical_urls_must_differ")
        if not self.outcome_blind:
            raise ValueError("validation_fixture_must_be_outcome_blind")
        if not self.synthetic_workflow_only:
            raise ValueError("validation_fixture_must_be_synthetic_workflow_only")
        return self


class CorpusManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus_id: str
    corpus_version: int
    description: str = Field(min_length=1)
    created_at: datetime
    corpus_hash: str = Field(min_length=64, max_length=64)
    synthetic_workflow_only: bool
    documents: list[CorpusDocument] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_identity_and_uniqueness(self) -> CorpusManifest:
        if self.corpus_id != CORPUS_ID:
            raise ValueError("unknown_validation_corpus_id")
        if self.corpus_version != CORPUS_VERSION:
            raise ValueError("unknown_validation_corpus_version")
        if not self.synthetic_workflow_only:
            raise ValueError("validation_corpus_must_be_synthetic_workflow_only")
        document_ids = [document.document_id for document in self.documents]
        fixture_urls = [document.fixture_url for document in self.documents]
        canonical_urls = [document.canonical_source_url for document in self.documents]
        if len(set(document_ids)) != len(document_ids):
            raise ValueError("duplicate_validation_document_id")
        if len(set(fixture_urls)) != len(fixture_urls):
            raise ValueError("duplicate_validation_fixture_url")
        if len(set(canonical_urls)) != len(canonical_urls):
            raise ValueError("duplicate_validation_canonical_url")
        return self


@dataclass(frozen=True)
class RankedCorpusDocument:
    document: CorpusDocument
    score: float
    matched_terms: tuple[str, ...]


def corpus_hash_for_payload(payload: dict[str, Any]) -> str:
    hashable = dict(payload)
    hashable.pop("corpus_hash", None)
    return sha256_text(canonical_json(hashable))


def _normalized_tokens(value: str) -> set[str]:
    return set(_TOKEN_RE.findall(value.casefold()))


def _normalized_phrase(value: str) -> str:
    return " ".join(_TOKEN_RE.findall(value.casefold()))


def _document_path(document: CorpusDocument) -> Path:
    candidate = (CORPUS_DIR / document.content_file).resolve()
    try:
        candidate.relative_to(CORPUS_DIR.resolve())
    except ValueError as exc:
        raise CutoffConsistentMockError(
            f"validation_content_path_outside_corpus:{document.document_id}"
        ) from exc
    return candidate


def _document_text(document: CorpusDocument) -> str:
    path = _document_path(document)
    if not path.is_file():
        raise CutoffConsistentMockError(
            f"validation_content_file_missing:{document.document_id}"
        )
    raw = path.read_text(encoding="utf-8")
    text, _title = _extract_html(raw, document.fixture_url)
    published_at = _published_from_html(raw)
    if published_at is None or as_utc(published_at) != as_utc(document.published_at):
        raise CutoffConsistentMockError(
            f"validation_published_at_mismatch:{document.document_id}"
        )
    if content_hash(text) != document.content_hash:
        raise CutoffConsistentMockError(
            f"validation_content_hash_mismatch:{document.document_id}"
        )
    if "SYNTHETIC WORKFLOW EVIDENCE" not in text:
        raise CutoffConsistentMockError(
            f"validation_synthetic_notice_missing:{document.document_id}"
        )
    return text


def load_cutoff_consistent_manifest() -> CorpusManifest:
    if not MANIFEST_PATH.is_file():
        raise CutoffConsistentMockError("validation_manifest_missing")
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise CutoffConsistentMockError("validation_manifest_unreadable") from exc
    if not isinstance(payload, dict):
        raise CutoffConsistentMockError("validation_manifest_object_required")
    expected_hash = str(payload.get("corpus_hash") or "")
    actual_hash = corpus_hash_for_payload(payload)
    if expected_hash != actual_hash:
        raise CutoffConsistentMockError("validation_corpus_hash_mismatch")
    manifest = CorpusManifest.model_validate(payload)
    for document in manifest.documents:
        _document_text(document)
    return manifest


def _rank_document(query: str, document: CorpusDocument) -> RankedCorpusDocument:
    normalized_query = _normalized_phrase(query)
    query_tokens = _normalized_tokens(query)
    searchable = " ".join(
        [
            document.title,
            document.domain,
            document.category,
            document.publisher,
            *document.query_terms,
        ]
    )
    searchable_tokens = _normalized_tokens(searchable)
    indexed_matches = [
        (index, term)
        for index, term in enumerate(document.query_terms)
        if _normalized_phrase(term) and _normalized_phrase(term) in normalized_query
    ]
    matched_terms = tuple(term for _index, term in indexed_matches)
    # The first four frozen terms are the document's question/domain anchors. Later
    # terms describe generic node roles (for example, "base rate") and must not let
    # an unrelated domain outrank a document that matches the actual subject.
    phrase_score = sum(
        (2.0 + 0.25 * len(_normalized_tokens(term)))
        if index < 4
        else (0.05 * len(_normalized_tokens(term)))
        for index, term in indexed_matches
    )
    overlap_score = 0.2 * len(query_tokens & searchable_tokens)
    title_score = 0.1 * len(query_tokens & _normalized_tokens(document.title))
    return RankedCorpusDocument(
        document=document,
        score=round(phrase_score + overlap_score + title_score, 12),
        matched_terms=matched_terms,
    )


def rank_corpus_documents(
    manifest: CorpusManifest,
    query: str,
    *,
    question_hash: str | None = None,
) -> list[RankedCorpusDocument]:
    candidates = [
        document
        for document in manifest.documents
        if question_hash is None or document.question_hash == question_hash
    ]
    return sorted(
        (_rank_document(query, document) for document in candidates),
        key=lambda item: (-item.score, item.document.document_id),
    )


def _candidate_reasons(document: CorpusDocument, cutoff: datetime) -> list[str]:
    reasons: list[str] = []
    if as_utc(document.published_at) > as_utc(cutoff):
        reasons.append("published_after_cutoff")
    if as_utc(document.snapshot_at) > as_utc(cutoff):
        reasons.append("snapshot_after_cutoff")
    return reasons


class CutoffConsistentMockSearchProvider:
    """Fail-closed search, snapshot, and fetch adapter for one validation question."""

    name = "mock"
    synthetic_workflow_only = True

    def __init__(
        self,
        *,
        corpus_id: str,
        corpus_version: int,
        question_hash: str,
        forecast_cutoff: datetime,
        synthetic_fixture_execution: bool,
        workflow_validation: bool,
        execution_mode: str,
        live_provider_involved: bool,
        real_historical_benchmark: bool,
        ledger: UsageLedger | None = None,
        run_id: str | None = None,
        run_attempt_id: str | None = None,
    ) -> None:
        if corpus_id != CORPUS_ID:
            raise CutoffConsistentMockError("unknown_validation_corpus_id")
        if corpus_version != CORPUS_VERSION:
            raise CutoffConsistentMockError("unknown_validation_corpus_version")
        if not synthetic_fixture_execution:
            raise CutoffConsistentMockError("synthetic_fixture_execution_required")
        if not workflow_validation:
            raise CutoffConsistentMockError("workflow_validation_marker_required")
        if execution_mode != "backtest":
            raise CutoffConsistentMockError("validation_adapter_backtest_mode_required")
        if live_provider_involved:
            raise CutoffConsistentMockError("validation_adapter_live_provider_forbidden")
        if real_historical_benchmark:
            raise CutoffConsistentMockError("validation_adapter_real_benchmark_forbidden")

        self.manifest = load_cutoff_consistent_manifest()
        self.question_hash = question_hash
        self.forecast_cutoff = as_utc(forecast_cutoff)
        self.ledger = ledger
        self.run_id = run_id
        self.run_attempt_id = run_attempt_id
        self._lock = threading.Lock()
        self._search_calls: list[dict[str, Any]] = []
        self._fetch_calls: list[dict[str, Any]] = []
        self._documents = [
            document
            for document in self.manifest.documents
            if document.question_hash == question_hash
        ]
        if not self._documents:
            raise CutoffConsistentMockError("validation_question_hash_not_in_corpus")
        self._eligible = [
            document
            for document in self._documents
            if not _candidate_reasons(document, self.forecast_cutoff)
        ]
        self._excluded = [
            {
                "document_id": document.document_id,
                "fixture_url": document.fixture_url,
                "canonical_source_url": document.canonical_source_url,
                "reasons": _candidate_reasons(document, self.forecast_cutoff),
            }
            for document in self._documents
            if _candidate_reasons(document, self.forecast_cutoff)
        ]
        self._by_fixture_url = {
            document.fixture_url: document for document in self._eligible
        }
        self._register_eligible_fixtures()

    def _register_eligible_fixtures(self) -> None:
        with _FIXTURE_REGISTRATION_LOCK:
            for document in self._eligible:
                path = _document_path(document)
                relative = Path("..") / path.relative_to(FIXTURES_DIR.parent)
                mapping = relative.as_posix()
                existing = FIXTURE_PAGES.get(document.fixture_url)
                if existing is not None and existing != mapping:
                    raise CutoffConsistentMockError(
                        f"validation_fixture_registration_conflict:{document.document_id}"
                    )
                FIXTURE_PAGES[document.fixture_url] = mapping

    def _assert_cutoff(self, as_of: datetime) -> None:
        if as_utc(as_of) != self.forecast_cutoff:
            raise CutoffConsistentMockError("validation_adapter_cutoff_drift")

    @property
    def calls(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "query": str(item["query"]),
                    "max_results": int(item["max_results"]),
                    "urls": list(item["urls"]),
                }
                for item in self._search_calls
            ]

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        ranked = rank_corpus_documents(
            self.manifest,
            query,
            question_hash=self.question_hash,
        )
        eligible_ids = {document.document_id for document in self._eligible}
        eligible_ranked = [
            item for item in ranked if item.document.document_id in eligible_ids
        ]
        returned = eligible_ranked[: max(0, max_results)]
        hits = [
            SearchHit(
                title=item.document.title,
                url=item.document.fixture_url,
                snippet=_document_text(item.document)[:400],
                published_at=item.document.published_at,
                score=item.score,
                source_class=item.document.source_class,
            )
            for item in returned
        ]
        audit = {
            "query": query,
            "max_results": max_results,
            "urls": [hit.url for hit in hits],
            "ranked_documents": [
                {
                    "rank": index,
                    "document_id": item.document.document_id,
                    "fixture_url": item.document.fixture_url,
                    "canonical_source_url": item.document.canonical_source_url,
                    "score": item.score,
                    "matched_terms": list(item.matched_terms),
                    "returned": item in returned,
                }
                for index, item in enumerate(eligible_ranked, start=1)
            ],
            "validation_corpus_exclusions": [dict(item) for item in self._excluded],
        }
        with self._lock:
            self._search_calls.append(audit)

        if self.ledger is not None and self.run_id:
            usage = ModelUsage(
                provider="mock",
                model="cutoff-consistent-mock-search",
                cost_usd=0.0,
                cost_source="estimated",
            )
            entry = self.ledger.reserve(
                run_id=self.run_id,
                run_attempt_id=self.run_attempt_id,
                logical_call_id=str(uuid.uuid4()),
                physical_attempt_number=1,
                stage="search",
                provider_type="search",
                provider=self.name,
                model=usage.model,
                reserved_input_tokens=0,
                reserved_output_tokens=0,
                reserved_cost_usd=0.0,
            )
            self.ledger.reconcile(entry.id, usage)
        return hits

    def discover_fixture_snapshots(
        self,
        url: str,
        *,
        as_of: datetime,
    ) -> list[WaybackSnapshot]:
        self._assert_cutoff(as_of)
        document = self._by_fixture_url.get(url)
        if document is None:
            return []
        if _candidate_reasons(document, as_of):
            return []
        return [
            WaybackSnapshot(
                url=document.canonical_source_url,
                timestamp=document.snapshot_at,
                snapshot_url=document.fixture_url,
                status="200",
                discovery="cutoff_consistent_mock_manifest",
            )
        ]

    def fetch_fixture_document(
        self,
        url: str,
        *,
        as_of: datetime | None,
        allow_local_fixtures: bool,
        snapshot_url: str | None,
        snapshot_at: datetime | None,
        mode: str,
    ) -> FetchedDocument:
        if mode != "backtest" or as_of is None:
            raise CutoffConsistentMockError("validation_adapter_backtest_fetch_required")
        if not allow_local_fixtures:
            raise CutoffConsistentMockError("validation_adapter_local_fixture_permission_required")
        self._assert_cutoff(as_of)
        document = self._by_fixture_url.get(url)
        if document is None:
            raise CutoffConsistentMockError("validation_fixture_not_eligible_or_unknown")
        if snapshot_url != document.fixture_url:
            raise CutoffConsistentMockError("validation_fixture_snapshot_url_mismatch")
        if snapshot_at is None or as_utc(snapshot_at) != as_utc(document.snapshot_at):
            raise CutoffConsistentMockError("validation_fixture_snapshot_at_mismatch")

        fetched = fetch_document(
            document.fixture_url,
            as_of=as_of,
            allow_local_fixtures=True,
            snapshot_url=snapshot_url,
            snapshot_at=snapshot_at,
            mode=mode,
        )
        if fetched.rejected or not fetched.as_of_eligible:
            with self._lock:
                self._fetch_calls.append(
                    {
                        "document_id": document.document_id,
                        "outcome": "rejected_by_normal_cutoff_validation",
                        "reason": fetched.rejection_reason,
                    }
                )
            return fetched
        if fetched.published_at is None or as_utc(fetched.published_at) != as_utc(document.published_at):
            raise CutoffConsistentMockError("validation_fetched_published_at_mismatch")
        if fetched.snapshot_at is None or as_utc(fetched.snapshot_at) != as_utc(document.snapshot_at):
            raise CutoffConsistentMockError("validation_fetched_snapshot_at_mismatch")
        if fetched.content_hash != document.content_hash:
            raise CutoffConsistentMockError("validation_fetched_content_hash_mismatch")

        verified = fetched.model_copy(
            update={
                "url": document.canonical_source_url,
                "title": document.title,
                "publisher": document.publisher,
                "snapshot_url": document.fixture_url,
                "snapshot_at": document.snapshot_at,
                "requested_snapshot_url": document.fixture_url,
                "requested_snapshot_at": document.snapshot_at,
                "final_snapshot_url": document.fixture_url,
                "final_snapshot_at": document.snapshot_at,
                "archived_original_url": document.canonical_source_url,
                "snapshot_verification_status": "cutoff_consistent_mock_manifest_verified",
            }
        )
        with self._lock:
            self._fetch_calls.append(
                {
                    "document_id": document.document_id,
                    "outcome": "accepted",
                    "fixture_url": document.fixture_url,
                    "canonical_source_url": document.canonical_source_url,
                    "published_at": document.published_at.isoformat(),
                    "snapshot_at": document.snapshot_at.isoformat(),
                    "content_hash": document.content_hash,
                }
            )
        return verified

    def audit_snapshot(self) -> dict[str, Any]:
        with self._lock:
            searches = sorted(
                (json.loads(json.dumps(item)) for item in self._search_calls),
                key=lambda item: (str(item["query"]), int(item["max_results"])),
            )
            fetches = sorted(
                (json.loads(json.dumps(item)) for item in self._fetch_calls),
                key=lambda item: (str(item["document_id"]), str(item["outcome"])),
            )
        return {
            "corpus_id": self.manifest.corpus_id,
            "corpus_version": self.manifest.corpus_version,
            "corpus_hash": self.manifest.corpus_hash,
            "manifest_path": str(MANIFEST_PATH.relative_to(ROOT)),
            "question_hash": self.question_hash,
            "forecast_cutoff": self.forecast_cutoff.isoformat(),
            "eligible_documents": [
                {
                    "document_id": document.document_id,
                    "fixture_url": document.fixture_url,
                    "canonical_source_url": document.canonical_source_url,
                    "published_at": document.published_at.isoformat(),
                    "snapshot_at": document.snapshot_at.isoformat(),
                    "content_hash": document.content_hash,
                }
                for document in sorted(self._eligible, key=lambda item: item.document_id)
            ],
            "excluded_documents": sorted(
                (dict(item) for item in self._excluded),
                key=lambda item: str(item["document_id"]),
            ),
            "query_rankings": searches,
            "fetches": fetches,
        }


class ShadowPricedResearchPlanner(ResearchPlanner):
    """Use frozen OpenAI/Tavily estimates for selection while execution stays mock-only."""

    def __init__(self, *, pricing_catalog: dict[str, Any]) -> None:
        super().__init__()
        self.pricing_catalog = pricing_catalog

    def plan(
        self,
        graph: ForecastGraph,
        *,
        forecast_run_id: str,
        budget: Budget,
        created_at: datetime | None = None,
    ) -> ResearchPlan:
        if abs(float(budget.profile.max_estimated_cost_usd) - SHADOW_COST_CEILING_USD) > 1e-12:
            raise CutoffConsistentMockError("shadow_pricing_cost_ceiling_mismatch")
        actual_snapshot = budget.snapshot()
        shadow = Budget(
            budget.profile,
            provider=SHADOW_MODEL_PROVIDER,
            model=SHADOW_MODEL,
            search_provider=SHADOW_SEARCH_PROVIDER,
            pricing_catalog=self.pricing_catalog,
            prior_elapsed_seconds=float(actual_snapshot["elapsed_seconds"]),
        )
        shadow.state.model_calls = budget.state.model_calls
        shadow.state.search_calls = budget.state.search_calls
        shadow.state.fetches = budget.state.fetches
        shadow.state.tokens = budget.state.tokens
        shadow.state.prompt_tokens = budget.state.prompt_tokens
        shadow.state.completion_tokens = budget.state.completion_tokens
        plan = super().plan(
            graph,
            forecast_run_id=forecast_run_id,
            budget=shadow,
            created_at=created_at,
        )
        per_node = plan.budget_allocation.get("per_node") or {}
        shadow_model_cost = round(
            sum(float(item.get("estimated_model_cost_usd") or 0.0) for item in per_node.values()),
            12,
        )
        shadow_search_cost = round(
            sum(float(item.get("estimated_search_cost_usd") or 0.0) for item in per_node.values()),
            12,
        )
        plan.budget_allocation["shadow_pricing"] = {
            "model_provider": SHADOW_MODEL_PROVIDER,
            "model": SHADOW_MODEL,
            "search_provider": SHADOW_SEARCH_PROVIDER,
            "pricing_catalog_hash": pricing_hash(catalog=self.pricing_catalog),
            "cost_ceiling_usd": SHADOW_COST_CEILING_USD,
            "estimated_model_cost_usd": shadow_model_cost,
            "estimated_search_cost_usd": shadow_search_cost,
            "estimated_total_cost_usd": round(
                shadow_model_cost + shadow_search_cost,
                12,
            ),
            "actual_provider_instances_created": False,
            "api_keys_loaded": False,
        }
        return plan
