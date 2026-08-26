#!/usr/bin/env python3
"""Acquire and machine-screen real resolved binary questions without model calls.

Raw source payloads, sealed provisional outcomes, and historical document bytes
remain in a private workspace outside the repository.  The repository receives
only this tool, tests, templates, and a sanitized aggregate receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

ACQUISITION_POLICY = "private_v1_candidate_acquisition_v1"
SOURCE_ID = "manifold_public_api_v0"
SOURCE_API = "https://api.manifold.markets/v0"
SOURCE_TERMS_URL = "https://docs.manifold.markets/api"
WORKSPACE_LABEL = "private-v1-candidate-acquisition-v1"
FIXED_SPLIT_SEED = "forecastlab-private-v1-provisional-split-v1"
DEFAULT_TARGET = 260
SPLIT_TARGETS = {"development": 60, "validation": 40, "test": 100}
MIN_RESERVE_TARGET = 20
MAX_DOWNLOAD_BYTES = 2_000_000
REQUIRED_LOCKS = ("uv.lock", "pyproject.toml", "apps/web/package-lock.json")
SENSITIVE_ENV_KEYS = {
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "FORECASTLAB_MODEL_API_KEY",
    "FORECASTLAB_SEARCH_API_KEY",
}
LIVE_PROVIDER_NAMES = {"openai", "tavily", "xai", "openai_compatible"}
QUESTION_PREFIXES = (
    "will ",
    "is ",
    "are ",
    "does ",
    "do ",
    "did ",
    "has ",
    "have ",
    "can ",
    "was ",
    "were ",
)
PERSONAL_RE = re.compile(
    r"\b(i|i'm|im|me|my|mine|we|our|ours|my friend|my family|my account)\b",
    re.IGNORECASE,
)
SUBJECTIVE_RE = re.compile(
    r"\b(good|bad|better|best|worse|worst|beautiful|interesting|cool|successful|"
    r"impressive|important|popular|happy|funny|feel|think|opinion|deserve|should)\b",
    re.IGNORECASE,
)
OBJECTIVE_RE = re.compile(
    r"\b(by|before|after|at least|more than|less than|above|below|win|elect|vote|"
    r"announce|release|launch|approve|reject|publish|report|close|price|rate|percent|"
    r"court|law|regulation|meeting|election|revenue|users|landfall|temperature|"
    r"20\d{2}|january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\b",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"[a-z0-9]+")
URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
HTML_TAG_RE = re.compile(r"<[^>]+>")
YEAR_RE = re.compile(r"\b(20\d{2})\b")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "be",
    "before",
    "by",
    "do",
    "does",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "will",
    "with",
    "yes",
    "no",
}
PRIMARY_SOURCE_DOMAINS = (
    "gov",
    "gov.uk",
    "europa.eu",
    "bls.gov",
    "bea.gov",
    "census.gov",
    "federalreserve.gov",
    "imf.org",
    "oecd.org",
    "worldbank.org",
    "who.int",
    "un.org",
    "sec.gov",
    "cbo.gov",
    "ons.gov.uk",
    "statcan.gc.ca",
    "ecb.europa.eu",
    "bis.org",
    "stlouisfed.org",
)


class AcquisitionError(RuntimeError):
    """Fail-closed acquisition or verification error."""


@dataclass(frozen=True)
class HttpPayload:
    body: bytes
    final_url: str
    status: int
    content_type: str


class PublicHttpClient:
    """Small unauthenticated HTTP client with bounded public GET requests only."""

    def __init__(self, *, timeout: float = 20.0, delay: float = 0.05) -> None:
        self.timeout = timeout
        self.delay = delay
        self.request_count = 0

    def get(self, url: str, *, accept: str = "application/json") -> HttpPayload:
        _require_public_http_url(url)
        request = Request(
            url,
            headers={
                "Accept": accept,
                "User-Agent": "ForecastLab-corpus-acquisition/1.0 (public research; no auth)",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - guarded public URL
                body = response.read(MAX_DOWNLOAD_BYTES + 1)
                if len(body) > MAX_DOWNLOAD_BYTES:
                    raise AcquisitionError("public_response_exceeds_size_limit")
                payload = HttpPayload(
                    body=body,
                    final_url=response.geturl(),
                    status=int(getattr(response, "status", 200)),
                    content_type=str(response.headers.get("Content-Type", "")),
                )
        except HTTPError as exc:
            raise AcquisitionError(f"public_http_status_{exc.code}") from exc
        except (TimeoutError, URLError) as exc:
            raise AcquisitionError(f"public_http_transport_failure:{type(exc).__name__}") from exc
        finally:
            self.request_count += 1
            if self.delay:
                time.sleep(self.delay)
        return payload


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, (canonical_json(value) + "\n").encode("utf-8"))


def atomic_write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    lines = [canonical_json(value) for value in values]
    atomic_write_bytes(path, (("\n".join(lines) + "\n") if lines else "").encode("utf-8"))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(source: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=source,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def preflight(
    *,
    source: Path,
    source_sha: str,
    workspace: Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source = source.resolve()
    workspace = workspace.expanduser().resolve()
    environment = environment if environment is not None else os.environ
    if _is_relative_to(workspace, source):
        raise AcquisitionError("private_workspace_must_be_outside_repository")
    if _git(source, "rev-parse", "HEAD") != source_sha:
        raise AcquisitionError("source_sha_mismatch")
    if _git(source, "status", "--porcelain=v1"):
        raise AcquisitionError("tracked_source_worktree_is_dirty")
    missing = [name for name in REQUIRED_LOCKS if not (source / name).is_file()]
    if missing:
        raise AcquisitionError("required_lock_missing:" + ",".join(sorted(missing)))
    if any(environment.get(key) for key in SENSITIVE_ENV_KEYS):
        raise AcquisitionError("credential_environment_present")
    model_provider = environment.get("FORECASTLAB_MODEL_PROVIDER", "").strip().casefold()
    search_provider = environment.get("FORECASTLAB_SEARCH_PROVIDER", "").strip().casefold()
    if model_provider in LIVE_PROVIDER_NAMES or search_provider in LIVE_PROVIDER_NAMES:
        raise AcquisitionError("live_provider_configuration_refused")
    if environment.get("FORECASTLAB_DATABASE_URL"):
        raise AcquisitionError("database_access_refused")
    protected = _protected_hashes(source)
    return {
        "source_sha": source_sha,
        "source_tree": _git(source, "rev-parse", "HEAD^{tree}"),
        "locks_present": True,
        "live_provider_configuration": False,
        "credential_environment_present": False,
        "database_access": False,
        "protected_hashes": protected,
    }


def _protected_hashes(source: Path) -> dict[str, Any]:
    paths = [
        "pyproject.toml",
        "uv.lock",
        "apps/web/package-lock.json",
        "packages/forecasting/forecastlab/version.py",
        "artifacts/private_v1_release_verification/manifest.json",
    ]
    tracked = _git(source, "ls-files", "configs/forecast_profiles", "prompts").splitlines()
    hashes = {
        relative: sha256_file(source / relative)
        for relative in sorted({*paths, *tracked})
        if (source / relative).is_file()
    }
    return {
        "files": hashes,
        "aggregate_sha256": sha256_json(hashes),
    }


def normalize_question(value: str) -> str:
    return " ".join(value.strip().split()).casefold().rstrip(" ?.!:")


def _tokens(value: str) -> list[str]:
    return [token for token in TOKEN_RE.findall(value.casefold()) if token not in STOPWORDS]


def _iso_millis(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _canonical_url(value: str) -> str:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunparse(((parsed.scheme or "https").casefold(), host, path, "", query, ""))


def _require_public_http_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AcquisitionError("non_http_or_missing_host")
    host = parsed.hostname.casefold().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise AcquisitionError("non_public_host_refused")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise AcquisitionError("non_public_ip_refused")


def _domain_category(question: str) -> tuple[str, str]:
    text = question.casefold()
    groups = (
        ("economics", "macroeconomics", ("inflation", "unemployment", "gdp", "fed ", "interest rate", "cpi")),
        ("business", "companies", ("company", "revenue", "earnings", "stock", "acquire", "bankrupt")),
        ("technology", "technology", ("ai ", "software", "iphone", "bitcoin", "crypto", "launch", "openai")),
        ("regulation", "policy", ("court", "law", "regulation", "approve", "ban", "sec ", "fda ")),
        ("politics", "elections", ("election", "president", "senate", "house", "vote", "party")),
        ("sports", "sports", ("match", "game", "tournament", "league", "cup", "championship", "team", "score")),
        ("science", "science", ("temperature", "earthquake", "hurricane", "space", "nasa", "study")),
    )
    for domain, category, terms in groups:
        if any(term in text for term in terms):
            return domain, category
    return "other", "general"


def _source_class(url: str) -> str:
    host = (urlparse(url).hostname or "").casefold().removeprefix("www.")
    if any(host == domain or host.endswith(f".{domain}") for domain in PRIMARY_SOURCE_DOMAINS):
        return "primary"
    return "secondary"


def _event_signatures(question: str, resolution_date: str) -> tuple[str, str, dict[str, Any]]:
    tokens = _tokens(question)
    years = YEAR_RE.findall(question)
    period = years[0] if years else resolution_date[:7]
    distinctive = tokens[:8] or ["unspecified"]
    family_material = {"period": period, "tokens": distinctive[:6]}
    leakage_material = {"period": period, "tokens": distinctive[:4]}
    family_id = "ef-" + sha256_json(family_material)[:16]
    leakage_id = "lg-" + sha256_json(leakage_material)[:16]
    return family_id, leakage_id, {
        "shared_entity_period_metric": "|".join([period, *distinctive[:6]]),
        "tokens": distinctive,
        "period": period,
    }


def _forecast_date(created_at: datetime, close_at: datetime, outcome_known_at: datetime) -> datetime:
    deadline = min(close_at, outcome_known_at)
    candidate = max(created_at, deadline - timedelta(days=90))
    if candidate >= deadline:
        candidate = created_at
    return candidate


def _basic_listing_reasons(market: Mapping[str, Any], *, as_of: datetime) -> list[str]:
    reasons: list[str] = []
    question = str(market.get("question") or "").strip()
    if market.get("outcomeType") != "BINARY":
        reasons.append("not_binary")
    if market.get("resolution") not in {"YES", "NO"}:
        reasons.append("non_binary_resolution")
    created = _iso_millis(market.get("createdTime"))
    resolved = _iso_millis(market.get("resolutionTime"))
    if created is None or resolved is None:
        reasons.append("missing_temporal_identity")
    elif not (_dt(created) < _dt(resolved) <= as_of):
        reasons.append("invalid_or_future_temporal_order")
    elif _dt(resolved) - _dt(created) < timedelta(days=7):
        reasons.append("forecast_window_under_seven_days")
    if len(question) < 20 or len(question) > 320 or "?" not in question:
        reasons.append("question_not_well_formed")
    if question and not question.casefold().startswith(QUESTION_PREFIXES):
        reasons.append("question_not_binary_form")
    if PERSONAL_RE.search(question):
        reasons.append("personal_or_self_referential")
    if SUBJECTIVE_RE.search(question):
        reasons.append("subjective_resolution_risk")
    if not OBJECTIVE_RE.search(question):
        reasons.append("objective_resolution_signal_missing")
    return sorted(set(reasons))


def _detail_reasons(market: Mapping[str, Any]) -> list[str]:
    description = str(market.get("textDescription") or "").strip()
    reasons: list[str] = []
    if len(description) < 20:
        reasons.append("published_resolution_terms_missing")
    if PERSONAL_RE.search(description[:2000]):
        reasons.append("personal_resolution_terms")
    if SUBJECTIVE_RE.search(description[:2000]):
        reasons.append("subjective_resolution_terms")
    return reasons


def _extract_text(value: bytes, content_type: str) -> str:
    decoded = value.decode("utf-8", errors="replace")
    if "html" in content_type.casefold() or "<html" in decoded[:500].casefold():
        decoded = HTML_TAG_RE.sub(" ", decoded)
        decoded = html.unescape(decoded)
    return " ".join(decoded.split())[:200_000]


def _wayback_stamp(value: str) -> datetime | None:
    match = re.search(r"/web/(\d{8,14})[a-z_]*?/", value)
    if match is None:
        return None
    raw = match.group(1).ljust(14, "0")
    try:
        return datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _wayback_original(value: str) -> str | None:
    match = re.search(r"/web/\d{8,14}[a-z_]*?/(.+)$", value)
    return unquote(match.group(1)) if match is not None else None


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def content(self, folder: str, payload: bytes) -> tuple[str, str]:
        digest = sha256_bytes(payload)
        relative = f"{folder}/{digest}"
        path = self.root / relative
        if path.exists() and sha256_file(path) != digest:
            raise AcquisitionError("content_address_collision")
        if not path.exists():
            atomic_write_bytes(path, payload)
        return relative, digest

    def record(self, folder: str, identity: str, value: Mapping[str, Any]) -> None:
        atomic_write_json(self.root / folder / f"{identity}.json", value)

    def records(self, folder: str) -> list[dict[str, Any]]:
        path = self.root / folder
        if not path.exists():
            return []
        return [load_json(item) for item in sorted(path.glob("*.json"))]


def _fetch_json(client: PublicHttpClient, workspace: Workspace, url: str) -> tuple[Any, dict[str, Any]]:
    payload = client.get(url)
    locator, digest = workspace.content("raw/downloads", payload.body)
    try:
        value = json.loads(payload.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcquisitionError("public_json_invalid") from exc
    return value, {
        "request_url": url,
        "final_url": payload.final_url,
        "http_status": payload.status,
        "content_sha256": digest,
        "private_locator": locator,
    }


def _archive_attempt(
    *,
    client: PublicHttpClient,
    workspace: Workspace,
    candidate_id: str,
    requested_url: str,
    cutoff: datetime,
    rank: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    canonical = _canonical_url(requested_url)
    attempt: dict[str, Any] = {
        "candidate_id": candidate_id,
        "rank": rank,
        "query": canonical,
        "requested_original_url": canonical,
        "cutoff": cutoff.isoformat(),
        "provider": "wayback_availability_public",
        "status": "rejected",
        "rejection_reason": None,
    }
    query = urlencode({"url": canonical, "timestamp": cutoff.strftime("%Y%m%d%H%M%S")})
    try:
        availability, receipt = _fetch_json(
            client,
            workspace,
            f"https://archive.org/wayback/available?{query}",
        )
    except AcquisitionError as exc:
        attempt["rejection_reason"] = str(exc).split(":", 1)[0]
        return attempt, None
    attempt["availability_receipt_sha256"] = receipt["content_sha256"]
    if not isinstance(availability, dict):
        attempt["rejection_reason"] = "wayback_availability_record_invalid"
        return attempt, None
    archived = availability.get("archived_snapshots")
    closest = archived.get("closest") if isinstance(archived, dict) else None
    if not isinstance(closest, dict) or not closest.get("available"):
        attempt["rejection_reason"] = "no_pre_cutoff_capture"
        return attempt, None
    stamp = str(closest.get("timestamp") or "")
    snapshot_url = str(closest.get("url") or "")
    archived_original = _wayback_original(snapshot_url)
    status = str(closest.get("status") or "")
    if not stamp or not snapshot_url or archived_original is None:
        attempt["rejection_reason"] = "wayback_availability_record_invalid"
        return attempt, None
    try:
        snapshot_at = datetime.strptime(stamp.ljust(14, "0"), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        attempt["rejection_reason"] = "snapshot_timestamp_invalid"
        return attempt, None
    attempt.update(
        {
            "requested_snapshot_url": snapshot_url,
            "snapshot_timestamp": snapshot_at.isoformat(),
            "archived_original_url": archived_original,
            "archive_status": status,
        }
    )
    if snapshot_at > cutoff:
        attempt["rejection_reason"] = "snapshot_after_cutoff"
        return attempt, None
    if _canonical_url(archived_original) != canonical:
        attempt["rejection_reason"] = "archived_original_mismatch"
        return attempt, None
    try:
        payload = client.get(attempt["requested_snapshot_url"], accept="text/html,application/pdf,text/plain")
    except AcquisitionError as exc:
        attempt["rejection_reason"] = str(exc).split(":", 1)[0]
        return attempt, None
    final_host = (urlparse(payload.final_url).hostname or "").casefold()
    final_stamp = _wayback_stamp(payload.final_url)
    final_original = _wayback_original(payload.final_url)
    if final_host not in {"web.archive.org", "archive.org"}:
        attempt["rejection_reason"] = "archive_redirected_to_live_content"
        return attempt, None
    if final_stamp is None or final_stamp > cutoff:
        attempt["rejection_reason"] = "final_archive_timestamp_invalid"
        return attempt, None
    if final_original is None or _canonical_url(final_original) != canonical:
        attempt["rejection_reason"] = "final_archive_original_mismatch"
        return attempt, None
    text = _extract_text(payload.body, payload.content_type)
    if len(text) < 40:
        attempt["rejection_reason"] = "archived_document_empty"
        return attempt, None
    blob_locator, blob_hash = workspace.content("blobs", payload.body)
    text_bytes = text.encode("utf-8")
    text_locator, text_hash = workspace.content("text", text_bytes)
    document_id = "doc-" + sha256_json(
        {
            "candidate_id": candidate_id,
            "url": canonical,
            "snapshot": final_stamp.isoformat(),
            "content": blob_hash,
        }
    )[:20]
    document = {
        "document_id": document_id,
        "candidate_id": candidate_id,
        "canonical_url": canonical,
        "source_host": (urlparse(canonical).hostname or "").removeprefix("www.").casefold(),
        "source_class": _source_class(canonical),
        "temporal_basis": "snapshot_date",
        "source_available_at": final_stamp.isoformat(),
        "cutoff_verified": True,
        "archive_original_verified": True,
        "blob_locator": blob_locator,
        "content_sha256": blob_hash,
        "byte_length": len(payload.body),
        "text_locator": text_locator,
        "extracted_text_sha256": text_hash,
        "content_type": payload.content_type,
        "source_license_status": "unknown_pending_human_review",
        "source_use_basis": "Archived public page; use and redistribution require human review.",
        "redistribution_allowed": False,
    }
    attempt.update(
        {
            "status": "accepted",
            "rejection_reason": None,
            "final_snapshot_url": payload.final_url,
            "final_snapshot_timestamp": final_stamp.isoformat(),
            "document_id": document_id,
        }
    )
    return attempt, document


def _candidate_from_market(
    market: Mapping[str, Any],
    *,
    source_receipt: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_market_id = str(market["id"])
    candidate_id = "flc1-" + sha256_bytes(f"{SOURCE_ID}:{source_market_id}".encode())[:20]
    question = " ".join(str(market["question"]).split())
    created_at = _dt(_iso_millis(market["createdTime"]) or "")
    outcome_known_at = _dt(_iso_millis(market["resolutionTime"]) or "")
    close_raw = _iso_millis(market.get("closeTime"))
    close_at = _dt(close_raw) if close_raw else outcome_known_at
    forecast_at = _forecast_date(created_at, close_at, outcome_known_at)
    resolution_at = min(close_at, outcome_known_at)
    if not (created_at <= forecast_at < resolution_at <= outcome_known_at):
        raise AcquisitionError("candidate_temporal_order_invalid")
    normalized = normalize_question(question)
    origin_url = _canonical_url(str(market["url"]))
    description = " ".join(str(market.get("textDescription") or "").split())
    criteria_hash = sha256_bytes(description.encode("utf-8"))
    yes_condition = (
        f"The answer is YES under the resolution criteria published for: {question.rstrip('?')}."
    )
    no_condition = (
        f"The answer is NO under the resolution criteria published for: {question.rstrip('?')}."
    )
    contract_hash = sha256_json(
        {
            "question": normalized,
            "yes_condition": yes_condition,
            "no_condition": no_condition,
            "resolution_criteria_hash": criteria_hash,
            "resolver": "Manifold market resolution",
        }
    )
    domain, category = _domain_category(question)
    family_id, leakage_id, grouping = _event_signatures(question, resolution_at.isoformat())
    source_record_hash = str(source_receipt["content_sha256"])
    blinded = {
        "candidate_id": candidate_id,
        "source_id": SOURCE_ID,
        "source_market_id": source_market_id,
        "original_question": question,
        "normalized_question": normalized,
        "normalized_question_hash": sha256_bytes(normalized.encode()),
        "yes_condition": yes_condition,
        "no_condition": no_condition,
        "contract_hash": contract_hash,
        "resolution_criteria_hash": criteria_hash,
        "forecast_date": forecast_at.isoformat(),
        "resolution_date": resolution_at.isoformat(),
        "authoritative_resolver": "Manifold market resolution pending independent adjudication",
        "origin_url": origin_url,
        "origin_timestamp": created_at.isoformat(),
        "origin_proof": {
            "type": "platform_timestamped_market_record",
            "source_record_sha256": source_record_hash,
            "source_record_locator": str(source_receipt["private_locator"]),
            "independently_reviewed": False,
        },
        "domain": domain,
        "category": category,
        "event_family_id": family_id,
        "leakage_group_id": leakage_id,
        "grouping_features": grouping,
        "source_use": {
            "status": "unknown_pending_human_review",
            "basis": "Public API metadata acquired under documented terms; use-specific review remains required.",
            "terms_url": SOURCE_TERMS_URL,
            "redistribution_allowed": False,
            "bytes_must_stay_private": True,
        },
        "machine_screening_state": "pending_evidence_packet",
        "review_status": "awaiting_human_review",
        "reviewer_id": None,
        "adjudication_status": "awaiting_independent_adjudication",
        "outcome_adjudicator_id": None,
        "provisional_split_status": "unassigned",
    }
    sealed = {
        "candidate_id": candidate_id,
        "provisional_observed_outcome": 1 if market["resolution"] == "YES" else 0,
        "outcome_known_at": outcome_known_at.isoformat(),
        "resolution_url": origin_url,
        "resolution_record_sha256": source_record_hash,
        "resolution_provider_value": str(market["resolution"]),
        "independently_adjudicated": False,
        "adjudication_status": "awaiting_independent_adjudication",
    }
    return blinded, sealed


def _candidate_evidence_urls(market: Mapping[str, Any], *, maximum: int) -> list[str]:
    values = [str(market.get("url") or "")]
    values.extend(URL_RE.findall(str(market.get("textDescription") or "")))
    output: list[str] = []
    for value in values:
        try:
            canonical = _canonical_url(value.rstrip(".,;"))
            _require_public_http_url(canonical)
        except AcquisitionError:
            continue
        if canonical not in output:
            output.append(canonical)
        if len(output) >= maximum:
            break
    return output


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _apply_near_duplicate_groups(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parent = {item["candidate_id"]: item["candidate_id"] for item in candidates}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for index, left in enumerate(candidates):
        left_tokens = _tokens(left["normalized_question"])
        for right in candidates[index + 1 :]:
            score = _jaccard(left_tokens, _tokens(right["normalized_question"]))
            if score >= 0.82:
                union(left["candidate_id"], right["candidate_id"])
    groups: dict[str, list[str]] = defaultdict(list)
    for candidate_id in parent:
        groups[find(candidate_id)].append(candidate_id)
    by_id = {item["candidate_id"]: item for item in candidates}
    for members in groups.values():
        if len(members) < 2:
            continue
        group_material = sorted(members)
        family = "ef-near-" + sha256_json(group_material)[:12]
        leakage = "lg-near-" + sha256_json(group_material)[:12]
        for candidate_id in members:
            item = by_id[candidate_id]
            item["event_family_id"] = family
            item["leakage_group_id"] = leakage
            item["near_duplicate_candidate_ids"] = [member for member in group_material if member != candidate_id]
            item["near_duplicate_review_required"] = True
    return candidates


def provisional_split(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        groups[str(candidate["leakage_group_id"])].append(candidate)
    ordered_groups = sorted(
        groups.items(),
        key=lambda item: (
            sha256_bytes(f"{FIXED_SPLIT_SEED}:{item[0]}".encode()),
            item[0],
        ),
    )
    remaining = dict(SPLIT_TARGETS)
    assignments: list[dict[str, str]] = []
    reserve: list[str] = []
    for group_id, members in ordered_groups:
        size = len(members)
        eligible_splits = [name for name, capacity in remaining.items() if capacity >= size]
        if eligible_splits:
            chosen = max(
                eligible_splits,
                key=lambda name: (remaining[name] / SPLIT_TARGETS[name], remaining[name], name),
            )
            remaining[chosen] -= size
            assignments.extend(
                {
                    "candidate_id": str(member["candidate_id"]),
                    "split": chosen,
                    "leakage_group_id": group_id,
                    "status": "provisional_pending_human_review",
                }
                for member in sorted(members, key=lambda value: str(value["candidate_id"]))
            )
        else:
            reserve.extend(str(member["candidate_id"]) for member in members)
    counts = Counter(item["split"] for item in assignments)
    complete = all(counts[name] == target for name, target in SPLIT_TARGETS.items())
    return {
        "seed": FIXED_SPLIT_SEED,
        "assignments": sorted(assignments, key=lambda item: (item["split"], item["candidate_id"])),
        "reserve_candidate_ids": sorted(reserve),
        "counts": {name: counts[name] for name in SPLIT_TARGETS},
        "target_counts": SPLIT_TARGETS,
        "complete": complete,
        "shortfall": {name: SPLIT_TARGETS[name] - counts[name] for name in SPLIT_TARGETS},
        "outcome_used": False,
    }


def _queues(
    candidates: Sequence[Mapping[str, Any]],
    sealed_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    question_review: list[dict[str, Any]] = []
    adjudication: list[dict[str, Any]] = []
    licensing: list[dict[str, Any]] = []
    event_family: list[dict[str, Any]] = []
    leakage: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: str(item["candidate_id"])):
        candidate_id = str(candidate["candidate_id"])
        base = {"candidate_id": candidate_id, "reviewer_id": None, "status": "pending_human_review"}
        question_review.append(
            {
                **base,
                "question_hash": candidate["normalized_question_hash"],
                "contract_hash": candidate["contract_hash"],
            }
        )
        adjudication.append(
            {
                "candidate_id": candidate_id,
                "outcome_adjudicator_id": None,
                "status": "awaiting_independent_adjudication",
                "sealed_record_hash": sha256_json(sealed_by_id[candidate_id]),
            }
        )
        licensing.append(
            {
                **base,
                "source_use_status": candidate["source_use"]["status"],
                "terms_url": candidate["source_use"]["terms_url"],
                "redistribution_allowed": False,
            }
        )
        event_family.append(
            {
                **base,
                "proposed_event_family_id": candidate["event_family_id"],
                "grouping_features": candidate["grouping_features"],
            }
        )
        leakage.append(
            {
                **base,
                "proposed_leakage_group_id": candidate["leakage_group_id"],
                "near_duplicate_candidate_ids": candidate.get("near_duplicate_candidate_ids", []),
            }
        )
    return {
        "question_review": question_review,
        "independent_outcome_adjudication": adjudication,
        "licensing": licensing,
        "event_family": event_family,
        "leakage": leakage,
    }


def _finalize(workspace: Workspace, manifest: dict[str, Any]) -> dict[str, Any]:
    candidates = workspace.records("state/candidates")
    sealed = workspace.records("sealed/provisional_outcomes")
    packets = workspace.records("state/evidence_packets")
    attempts = workspace.records("state/archive_attempts")
    documents = workspace.records("state/evidence_documents")
    rejections = workspace.records("state/rejections")
    candidates = _apply_near_duplicate_groups(candidates)
    exact_seen: set[str] = set()
    machine_complete: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: str(item["candidate_id"])):
        packet = next((item for item in packets if item["candidate_id"] == candidate["candidate_id"]), None)
        reasons: list[str] = []
        if candidate["normalized_question_hash"] in exact_seen:
            reasons.append("exact_duplicate")
        exact_seen.add(candidate["normalized_question_hash"])
        if packet is None:
            reasons.append("evidence_packet_missing")
        if candidate["reviewer_id"] is not None or candidate["outcome_adjudicator_id"] is not None:
            reasons.append("human_identity_must_remain_blank")
        if _dt(candidate["forecast_date"]) >= _dt(candidate["resolution_date"]):
            reasons.append("forecast_not_before_resolution")
        candidate["machine_screening_reasons"] = sorted(reasons)
        candidate["machine_screening_state"] = (
            "machine_eligible_pending_human_review" if not reasons else "machine_follow_up_required"
        )
        if not reasons:
            machine_complete.append(candidate)
        workspace.record("records/blinded_candidates", candidate["candidate_id"], candidate)
    sealed_by_id = {item["candidate_id"]: item for item in sealed}
    split = provisional_split(machine_complete)
    assignment_by_id = {item["candidate_id"]: item for item in split["assignments"]}
    for candidate in machine_complete:
        assignment = assignment_by_id.get(candidate["candidate_id"])
        candidate["provisional_split_status"] = (
            assignment["status"] if assignment else "reserve_pending_human_review"
        )
        candidate["provisional_split"] = assignment["split"] if assignment else "reserve"
        workspace.record("records/blinded_candidates", candidate["candidate_id"], candidate)
    queues = _queues(machine_complete, sealed_by_id)
    atomic_write_jsonl(workspace.root / "records/blinded_candidates.jsonl", machine_complete)
    atomic_write_jsonl(workspace.root / "sealed/provisional_outcomes.jsonl", sealed)
    atomic_write_jsonl(workspace.root / "records/evidence_packets.jsonl", packets)
    atomic_write_jsonl(workspace.root / "records/archive_attempts.jsonl", attempts)
    atomic_write_jsonl(workspace.root / "records/evidence_documents.jsonl", documents)
    atomic_write_jsonl(workspace.root / "records/rejections.jsonl", rejections)
    atomic_write_jsonl(workspace.root / "splits/provisional.jsonl", split["assignments"])
    for name, values in queues.items():
        atomic_write_jsonl(workspace.root / "queues" / f"{name}.jsonl", values)
    queue_hashes = {
        name: sha256_file(workspace.root / "queues" / f"{name}.jsonl") for name in sorted(queues)
    }
    domains = Counter(item["domain"] for item in machine_complete)
    years = Counter(item["resolution_date"][:4] for item in machine_complete)
    months = Counter(item["resolution_date"][:7] for item in machine_complete)
    accepted_documents = [item for item in documents if item.get("cutoff_verified")]
    no_evidence = [item for item in packets if item["status"] == "no_eligible_evidence"]
    rejection_reasons = Counter(
        reason for item in rejections for reason in item.get("reasons", [])
    )
    archive_rejection_reasons = Counter(
        str(item.get("rejection_reason"))
        for item in attempts
        if item.get("status") != "accepted"
    )
    near_duplicate_candidates = [
        item for item in machine_complete if item.get("near_duplicate_review_required")
    ]
    assigned_by_id = {item["candidate_id"]: item["split"] for item in split["assignments"]}
    event_splits: dict[str, set[str]] = defaultdict(set)
    leakage_splits: dict[str, set[str]] = defaultdict(set)
    for item in machine_complete:
        assigned_split = assigned_by_id.get(item["candidate_id"], "reserve")
        event_splits[item["event_family_id"]].add(assigned_split)
        leakage_splits[item["leakage_group_id"]].add(assigned_split)
    summary = {
        "policy_version": ACQUISITION_POLICY,
        "workspace_label": WORKSPACE_LABEL,
        "source": {
            "id": SOURCE_ID,
            "terms_review_status": "pending_human_review",
            "authenticated": False,
        },
        "source_sha": manifest["source_sha"],
        "source_tree": manifest["source_tree"],
        "manifest_hash": manifest["manifest_hash"],
        "counts": {
            "acquired_candidates": len(candidates),
            "machine_complete": len(machine_complete),
            "machine_follow_up": len(candidates) - len(machine_complete),
            "rejected_before_acquisition": len(rejections),
            "provisional_assignments": len(split["assignments"]),
            "reserves": len(split["reserve_candidate_ids"]),
            "evidence_ready_packets": len(packets) - len(no_evidence),
            "no_eligible_evidence_packets": len(no_evidence),
            "archive_attempts": len(attempts),
            "accepted_documents": len(accepted_documents),
            "rejected_document_attempts": len([item for item in attempts if item["status"] != "accepted"]),
            "distinct_document_blobs": len({item["content_sha256"] for item in documents}),
        },
        "targets": {
            "acquired": DEFAULT_TARGET,
            "machine_complete": 220,
            "split": SPLIT_TARGETS,
            "reserve": MIN_RESERVE_TARGET,
        },
        "split": {key: value for key, value in split.items() if key != "assignments"},
        "domain_distribution": dict(sorted(domains.items())),
        "resolution_year_distribution": dict(sorted(years.items())),
        "resolution_month_distribution": dict(sorted(months.items())),
        "date_range": (
            {
                "forecast_date_min": min(item["forecast_date"] for item in machine_complete),
                "forecast_date_max": max(item["forecast_date"] for item in machine_complete),
                "resolution_date_min": min(item["resolution_date"] for item in machine_complete),
                "resolution_date_max": max(item["resolution_date"] for item in machine_complete),
            }
            if machine_complete
            else {
                "forecast_date_min": None,
                "forecast_date_max": None,
                "resolution_date_min": None,
                "resolution_date_max": None,
            }
        ),
        "origin_proof": {
            "platform_timestamped_records": len(machine_complete),
            "independently_human_reviewed": 0,
        },
        "evidence_coverage": {
            "candidates_with_one_or_more_documents": len({item["candidate_id"] for item in documents}),
            "candidates_with_three_or_more_documents": len(
                [
                    candidate_id
                    for candidate_id, items in _group_by(documents, "candidate_id").items()
                    if len(items) >= 3
                ]
            ),
            "candidates_with_two_or_more_hosts": len(
                [
                    candidate_id
                    for candidate_id, items in _group_by(documents, "candidate_id").items()
                    if len({item["source_host"] for item in items}) >= 2
                ]
            ),
            "primary_document_count": len([item for item in documents if item["source_class"] == "primary"]),
        },
        "screening_audit": {
            "rejection_reason_counts": dict(sorted(rejection_reasons.items())),
            "archive_rejection_reason_counts": dict(sorted(archive_rejection_reasons.items())),
            "near_duplicate_candidates_queued": len(near_duplicate_candidates),
            "distinct_event_families": len(event_splits),
            "distinct_leakage_groups": len(leakage_splits),
            "cross_split_event_family_count": len(
                [splits for splits in event_splits.values() if len(splits - {"reserve"}) > 1]
            ),
            "cross_split_leakage_group_count": len(
                [splits for splits in leakage_splits.values() if len(splits - {"reserve"}) > 1]
            ),
            "licensing_pending_human_review": len(machine_complete),
        },
        "queues": {name: {"count": len(values), "sha256": queue_hashes[name]} for name, values in queues.items()},
        "human_review": {
            "reviewer_assigned": False,
            "independent_adjudicator_assigned": False,
            "release_reviewed": False,
            "release_frozen": False,
        },
        "execution": {
            "forecast_runs": 0,
            "experiments": 0,
            "scores": 0,
            "calibration": None,
            "openai_calls": 0,
            "tavily_calls": 0,
            "provider_spend_usd": 0.0,
        },
        "protected_hashes": manifest["protected_hashes"],
    }
    summary["summary_hash"] = sha256_json(summary)
    atomic_write_json(workspace.root / "summary.json", summary)
    output_manifest = {
        **manifest,
        "summary_hash": summary["summary_hash"],
        "queue_hashes": queue_hashes,
        "record_hashes": {
            relative: sha256_file(workspace.root / relative)
            for relative in (
                "records/blinded_candidates.jsonl",
                "sealed/provisional_outcomes.jsonl",
                "records/evidence_packets.jsonl",
                "records/archive_attempts.jsonl",
                "records/evidence_documents.jsonl",
                "splits/provisional.jsonl",
            )
        },
    }
    output_manifest["workspace_manifest_hash"] = sha256_json(output_manifest)
    atomic_write_json(workspace.root / "manifest.json", output_manifest)
    return summary


def _group_by(values: Sequence[Mapping[str, Any]], key: str) -> dict[str, list[Mapping[str, Any]]]:
    output: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for value in values:
        output[str(value[key])].append(value)
    return output


def acquire(
    *,
    source: Path,
    source_sha: str,
    workspace_path: Path,
    target: int = DEFAULT_TARGET,
    max_pages: int = 12,
    max_evidence_urls: int = 3,
    client: PublicHttpClient | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if target < 1 or max_pages < 1 or max_evidence_urls < 1:
        raise AcquisitionError("positive_acquisition_limits_required")
    receipt = preflight(
        source=source,
        source_sha=source_sha,
        workspace=workspace_path,
        environment=environment,
    )
    workspace = Workspace(workspace_path)
    workspace.root.mkdir(parents=True, exist_ok=True)
    manifest_path = workspace.root / "manifest.json"
    if manifest_path.exists():
        existing = load_json(manifest_path)
        if existing.get("source_sha") != source_sha or existing.get("target") != target:
            raise AcquisitionError("resume_manifest_identity_mismatch")
        manifest = {key: existing[key] for key in existing if key not in {"summary_hash", "queue_hashes", "record_hashes", "workspace_manifest_hash"}}
    else:
        started_at = datetime.now(tz=UTC).replace(microsecond=0)
        manifest = {
            **receipt,
            "policy_version": ACQUISITION_POLICY,
            "workspace_label": WORKSPACE_LABEL,
            "source_id": SOURCE_ID,
            "target": target,
            "max_pages": max_pages,
            "max_evidence_urls": max_evidence_urls,
            "screening_as_of": started_at.isoformat(),
            "split_seed": FIXED_SPLIT_SEED,
            "release_review_authorized": False,
            "release_freeze_authorized": False,
            "forecast_execution_authorized": False,
        }
        manifest["manifest_hash"] = sha256_json(manifest)
        atomic_write_json(manifest_path, manifest)
    client = client or PublicHttpClient()
    screening_as_of = _dt(manifest["screening_as_of"])
    existing_candidates = workspace.records("state/candidates")
    completed_candidate_ids = {
        item["candidate_id"] for item in workspace.records("state/evidence_packets")
    }
    completed_candidates = [
        item for item in existing_candidates if item["candidate_id"] in completed_candidate_ids
    ]
    acquired_ids = {item["candidate_id"] for item in completed_candidates}
    source_market_ids = {item["source_market_id"] for item in completed_candidates}
    normalized_hashes = {item["normalized_question_hash"] for item in completed_candidates}
    before_time: int | None = None
    page = 0
    while len(acquired_ids) < target and page < max_pages:
        params: dict[str, str] = {
            "term": "",
            "sort": "newest",
            "filter": "resolved",
            "contractType": "BINARY",
            "limit": "1000",
        }
        if before_time is not None:
            params["beforeTime"] = str(before_time)
        listings, page_receipt = _fetch_json(
            client,
            workspace,
            f"{SOURCE_API}/search-markets?{urlencode(params)}",
        )
        if not isinstance(listings, list) or not listings:
            break
        page_id = f"page-{page:03d}-{sha256_json(page_receipt)[:12]}"
        workspace.record("state/source_pages", page_id, {"page": page, "receipt": page_receipt, "count": len(listings)})
        for listing in listings:
            if len(acquired_ids) >= target:
                break
            if not isinstance(listing, dict):
                continue
            source_market_id = str(listing.get("id") or "")
            if not source_market_id or source_market_id in source_market_ids:
                continue
            reasons = _basic_listing_reasons(listing, as_of=screening_as_of)
            if reasons:
                rejection_id = "rej-" + sha256_bytes(f"{SOURCE_ID}:{source_market_id}".encode())[:20]
                workspace.record(
                    "state/rejections",
                    rejection_id,
                    {
                        "rejection_id": rejection_id,
                        "source_market_id": source_market_id,
                        "source_page_receipt_sha256": page_receipt["content_sha256"],
                        "reasons": reasons,
                    },
                )
                continue
            try:
                detail, detail_receipt = _fetch_json(
                    client,
                    workspace,
                    f"{SOURCE_API}/market/{source_market_id}",
                )
            except AcquisitionError as exc:
                rejection_id = "rej-" + sha256_bytes(f"{SOURCE_ID}:{source_market_id}".encode())[:20]
                workspace.record(
                    "state/rejections",
                    rejection_id,
                    {
                        "rejection_id": rejection_id,
                        "source_market_id": source_market_id,
                        "source_page_receipt_sha256": page_receipt["content_sha256"],
                        "reasons": [str(exc).split(":", 1)[0]],
                    },
                )
                continue
            if not isinstance(detail, dict):
                continue
            reasons = _detail_reasons(detail)
            if reasons:
                rejection_id = "rej-" + sha256_bytes(f"{SOURCE_ID}:{source_market_id}".encode())[:20]
                workspace.record(
                    "state/rejections",
                    rejection_id,
                    {
                        "rejection_id": rejection_id,
                        "source_market_id": source_market_id,
                        "source_page_receipt_sha256": page_receipt["content_sha256"],
                        "source_detail_receipt_sha256": detail_receipt["content_sha256"],
                        "reasons": sorted(reasons),
                    },
                )
                continue
            try:
                blinded, sealed = _candidate_from_market(detail, source_receipt=detail_receipt)
            except AcquisitionError as exc:
                rejection_id = "rej-" + sha256_bytes(f"{SOURCE_ID}:{source_market_id}".encode())[:20]
                workspace.record(
                    "state/rejections",
                    rejection_id,
                    {
                        "rejection_id": rejection_id,
                        "source_market_id": source_market_id,
                        "source_page_receipt_sha256": page_receipt["content_sha256"],
                        "source_detail_receipt_sha256": detail_receipt["content_sha256"],
                        "reasons": [str(exc)],
                    },
                )
                continue
            if blinded["normalized_question_hash"] in normalized_hashes:
                rejection_id = "rej-duplicate-" + blinded["candidate_id"]
                workspace.record(
                    "state/rejections",
                    rejection_id,
                    {
                        "rejection_id": rejection_id,
                        "source_market_id": source_market_id,
                        "source_page_receipt_sha256": page_receipt["content_sha256"],
                        "source_detail_receipt_sha256": detail_receipt["content_sha256"],
                        "reasons": ["exact_duplicate"],
                    },
                )
                continue
            workspace.record("state/candidates", blinded["candidate_id"], blinded)
            workspace.record("sealed/provisional_outcomes", blinded["candidate_id"], sealed)
            attempts: list[dict[str, Any]] = []
            documents: list[dict[str, Any]] = []
            for rank, evidence_url in enumerate(
                _candidate_evidence_urls(detail, maximum=max_evidence_urls), start=1
            ):
                attempt, document = _archive_attempt(
                    client=client,
                    workspace=workspace,
                    candidate_id=blinded["candidate_id"],
                    requested_url=evidence_url,
                    cutoff=_dt(blinded["forecast_date"]),
                    rank=rank,
                )
                attempts.append(attempt)
                workspace.record(
                    "state/archive_attempts",
                    f"{blinded['candidate_id']}-{rank:02d}",
                    attempt,
                )
                if document is not None:
                    documents.append(document)
                    workspace.record("state/evidence_documents", document["document_id"], document)
            packet = {
                "packet_id": "packet-" + blinded["candidate_id"],
                "candidate_id": blinded["candidate_id"],
                "cutoff": blinded["forecast_date"],
                "status": "ready" if documents else "no_eligible_evidence",
                "accepted_document_ids": sorted(item["document_id"] for item in documents),
                "attempt_ids": [f"{blinded['candidate_id']}-{index:02d}" for index in range(1, len(attempts) + 1)],
                "no_evidence_reason": None if documents else "no_verified_pre_cutoff_archive_document",
                "review_status": "awaiting_human_review",
                "reviewer_id": None,
            }
            workspace.record("state/evidence_packets", blinded["candidate_id"], packet)
            acquired_ids.add(blinded["candidate_id"])
            source_market_ids.add(source_market_id)
            normalized_hashes.add(blinded["normalized_question_hash"])
        page += 1
        valid_times = [item.get("createdTime") for item in listings if isinstance(item, dict)]
        valid_times = [int(item) for item in valid_times if isinstance(item, (int, float))]
        if not valid_times:
            break
        next_before = min(valid_times)
        if before_time is not None and next_before >= before_time:
            break
        before_time = next_before
    return _finalize(workspace, manifest)


def verify_workspace(
    *,
    source: Path,
    source_sha: str,
    workspace_path: Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    preflight(
        source=source,
        source_sha=source_sha,
        workspace=workspace_path,
        environment=environment,
    )
    workspace = Workspace(workspace_path)
    manifest_path = workspace.root / "manifest.json"
    summary_path = workspace.root / "summary.json"
    if not manifest_path.is_file() or not summary_path.is_file():
        raise AcquisitionError("workspace_manifest_or_summary_missing")
    manifest = load_json(manifest_path)
    summary = load_json(summary_path)
    if manifest.get("source_sha") != source_sha:
        raise AcquisitionError("workspace_source_sha_mismatch")
    expected_summary_hash = summary.get("summary_hash")
    summary_payload = {key: value for key, value in summary.items() if key != "summary_hash"}
    if expected_summary_hash != sha256_json(summary_payload):
        raise AcquisitionError("workspace_summary_hash_mismatch")
    expected_manifest_hash = manifest.get("workspace_manifest_hash")
    manifest_payload = {
        key: value for key, value in manifest.items() if key != "workspace_manifest_hash"
    }
    if expected_manifest_hash != sha256_json(manifest_payload):
        raise AcquisitionError("workspace_manifest_hash_mismatch")
    for relative, expected in manifest.get("record_hashes", {}).items():
        path = workspace.root / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise AcquisitionError(f"workspace_record_hash_mismatch:{relative}")
    for name, expected in manifest.get("queue_hashes", {}).items():
        path = workspace.root / "queues" / f"{name}.jsonl"
        if not path.is_file() or sha256_file(path) != expected:
            raise AcquisitionError(f"workspace_queue_hash_mismatch:{name}")
    for folder in ("raw/downloads", "blobs", "text"):
        for path in sorted((workspace.root / folder).glob("*")) if (workspace.root / folder).exists() else []:
            if not path.is_file() or path.name != sha256_file(path):
                raise AcquisitionError(f"content_address_verification_failed:{folder}")
    sealed_text = (workspace.root / "sealed/provisional_outcomes.jsonl").read_text(encoding="utf-8")
    blinded_text = (workspace.root / "records/blinded_candidates.jsonl").read_text(encoding="utf-8")
    if "provisional_observed_outcome" in blinded_text or "outcome_known_at" in blinded_text:
        raise AcquisitionError("sealed_outcome_leaked_into_blinded_records")
    if "provisional_observed_outcome" not in sealed_text and summary["counts"]["machine_complete"]:
        raise AcquisitionError("sealed_outcome_records_missing")
    secret_patterns = (
        re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
        re.compile(rb"\btvly-[A-Za-z0-9_-]{20,}\b"),
        re.compile(rb"\bBearer\s+[A-Za-z0-9._~+/-]{20,}", re.IGNORECASE),
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    suspected_secret_count = 0
    for path in sorted(workspace.root.rglob("*")):
        if not path.is_file():
            continue
        payload = path.read_bytes()
        suspected_secret_count += sum(
            len(pattern.findall(payload)) for pattern in secret_patterns
        )
    if suspected_secret_count:
        raise AcquisitionError("suspected_secret_material_in_workspace")
    return {
        "verified": True,
        "source_sha": source_sha,
        "workspace_manifest_hash": manifest["workspace_manifest_hash"],
        "summary_hash": summary["summary_hash"],
        "record_count": summary["counts"]["machine_complete"],
        "network_requests": 0,
        "provider_calls": 0,
        "suspected_secret_count": suspected_secret_count,
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET)
    parser.add_argument("--max-pages", type=int, default=12)
    parser.add_argument("--max-evidence-urls", type=int, default=3)
    parser.add_argument("--request-delay", type=float, default=0.05)
    parser.add_argument("--http-timeout", type=float, default=8.0)
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    source = Path(__file__).resolve().parents[1]
    try:
        if args.verify_only:
            result = verify_workspace(
                source=source,
                source_sha=args.source_sha,
                workspace_path=args.workspace,
            )
        else:
            result = acquire(
                source=source,
                source_sha=args.source_sha,
                workspace_path=args.workspace,
                target=args.target,
                max_pages=args.max_pages,
                max_evidence_urls=args.max_evidence_urls,
                client=PublicHttpClient(timeout=args.http_timeout, delay=args.request_delay),
            )
    except AcquisitionError as exc:
        print(canonical_json({"status": "failed", "error": str(exc).split(":", 1)[0]}))
        return 2
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
