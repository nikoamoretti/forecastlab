#!/usr/bin/env python3
"""Acquire a private native-provenance candidate corpus from public APIs only.

The tool creates an external, content-addressed workspace. It never calls a
model or search provider, creates review artifacts, opens an evaluation release,
or runs a forecast. Its only supported source is Kalshi's unauthenticated
historical-market API, whose native creation and final-settlement timestamps are
recorded explicitly instead of inferred from the local retrieval time.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.native_provenance_corpus import (
    NATIVE_CORPUS_POLICY,
    NATIVE_SOURCE_ID,
    NativeCorpusError,
    attach_split,
    native_candidate_from_market,
    native_document_from_packet,
    native_failure_packet,
    provisional_split,
    validate_native_v2_readiness,
)
from forecastlab.private_internal_evaluation_v2 import (
    build_private_internal_evaluation_v2_preflight,
)

WORKSPACE_LABEL = "private-v1-native-provenance-corpus-v3"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_NATIVE_SERIES = (
    ("Economics", "KXJOBLESSCLAIMS"),
    ("Economics", "KXJPMOMINF"),
    ("Economics", "KXRETAIL"),
    ("Financials", "KXCBDECISIONEU"),
    ("Companies", "KXIPHONERELEASE"),
    ("Science and Technology", "KXOAIGOOGLE"),
    ("Health", "KXFDAAPPROVE"),
    ("Entertainment", "KXAWARDSCMAMEOTY"),
)
SOURCE_AUDIT_CATALOG = (
    {
        "source_id": NATIVE_SOURCE_ID,
        "status": "active_native_adapter",
        "accepted_proof": "native timestamped historical binary-market record",
        "outcome_separation": "sealed provisional settlement record",
    },
    {
        "source_id": "metaculus_public_metadata",
        "status": "not_used_without_an_accessible_durable_origin_record",
        "accepted_proof": None,
        "outcome_separation": "not_applicable",
    },
    {
        "source_id": "forecastbench_dataset",
        "status": "not_used_without_independent_pre_cutoff_origin_proof",
        "accepted_proof": None,
        "outcome_separation": "not_applicable",
    },
)
REQUIRED_LOCKS = ("uv.lock", "pyproject.toml", "apps/web/package-lock.json")
SENSITIVE_ENV_KEYS = (
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "FORECASTLAB_MODEL_API_KEY",
    "FORECASTLAB_SEARCH_API_KEY",
)
LIVE_PROVIDER_NAMES = {"openai", "tavily", "xai", "openai_compatible"}
MAX_BYTES = 4_000_000
MARKET_PAGE_LIMIT = 200


class NativeAcquisitionError(RuntimeError):
    """A safe public-acquisition failure."""


class PublicHttpClient:
    """Bounded unauthenticated HTTP GET client; no credentials are accepted."""

    def __init__(self, *, timeout: float, delay: float) -> None:
        self.timeout = timeout
        self.delay = delay
        self.request_count = 0

    def get_json(self, url: str) -> tuple[dict[str, Any], str, bytes]:
        _assert_public_url(url)
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "ForecastLab-native-provenance-v3/1.0 (public research; no auth)",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - guarded URL
                content = response.read(MAX_BYTES + 1)
                if len(content) > MAX_BYTES:
                    raise NativeAcquisitionError("native_public_response_oversized")
                if not str(response.headers.get("Content-Type", "")).casefold().startswith(
                    "application/json"
                ):
                    raise NativeAcquisitionError("native_public_response_not_json")
                final_url = response.geturl()
        except HTTPError as exc:
            raise NativeAcquisitionError(f"native_public_http_{exc.code}") from exc
        except (TimeoutError, URLError) as exc:
            raise NativeAcquisitionError(
                f"native_public_transport_failure:{type(exc).__name__}"
            ) from exc
        finally:
            self.request_count += 1
            if self.delay:
                time.sleep(self.delay)
        try:
            result = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NativeAcquisitionError("native_public_json_invalid") from exc
        if not isinstance(result, dict):
            raise NativeAcquisitionError("native_public_json_object_required")
        return result, final_url, content


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_write(path, (canonical_json(value) + "\n").encode("utf-8"))


def _atomic_jsonl(path: Path, values: Sequence[Mapping[str, Any]]) -> None:
    payload = "\n".join(canonical_json(value) for value in values)
    _atomic_write(path, ((payload + "\n") if payload else "").encode("utf-8"))


def _load_json(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise NativeAcquisitionError(f"native_record_invalid:{path.name}")
    return result


def _records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [_load_json(item) for item in sorted(path.glob("*.json"))]


def _git(source: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=source, check=True, text=True, capture_output=True
    ).stdout.strip()


def _within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _assert_public_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise NativeAcquisitionError("native_source_url_invalid")
    host = parsed.hostname.casefold().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise NativeAcquisitionError("native_source_private_host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise NativeAcquisitionError("native_source_private_ip")


def _protected_hashes(source: Path) -> dict[str, str]:
    paths = {
        "pyproject.toml",
        "uv.lock",
        "apps/web/package-lock.json",
        "packages/forecasting/forecastlab/version.py",
        "artifacts/real_corpus_acquisition_phase1/summary.json",
    }
    paths.update(_git(source, "ls-files", "configs/forecast_profiles", "prompts").splitlines())
    return {
        relative: _sha256_file(source / relative)
        for relative in sorted(paths)
        if (source / relative).is_file()
    }


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
    if _within(workspace, source):
        raise NativeAcquisitionError("native_workspace_must_be_outside_repository")
    if _git(source, "rev-parse", "HEAD") != source_sha:
        raise NativeAcquisitionError("native_source_sha_mismatch")
    if _git(source, "status", "--porcelain=v1"):
        raise NativeAcquisitionError("native_source_worktree_dirty")
    missing = [name for name in REQUIRED_LOCKS if not (source / name).is_file()]
    if missing:
        raise NativeAcquisitionError("native_required_lock_missing:" + ",".join(missing))
    if any(environment.get(key) for key in SENSITIVE_ENV_KEYS):
        raise NativeAcquisitionError("native_provider_credential_environment_present")
    if environment.get("FORECASTLAB_DATABASE_URL"):
        raise NativeAcquisitionError("native_database_access_refused")
    providers = {
        environment.get("FORECASTLAB_MODEL_PROVIDER", "").casefold(),
        environment.get("FORECASTLAB_SEARCH_PROVIDER", "").casefold(),
    }
    if providers & LIVE_PROVIDER_NAMES:
        raise NativeAcquisitionError("native_live_provider_configuration_refused")
    return {
        "source_sha": source_sha,
        "source_tree": _git(source, "rev-parse", "HEAD^{tree}"),
        "protected_hashes": _protected_hashes(source),
        "provider_calls": 0,
        "provider_spend_usd": 0.0,
    }


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def content(self, *, folder: str, payload: bytes) -> tuple[str, str]:
        digest = _sha256_bytes(payload)
        relative = f"{folder}/{digest}"
        path = self.root / relative
        if path.exists() and _sha256_file(path) != digest:
            raise NativeAcquisitionError("native_content_address_collision")
        if not path.exists():
            _atomic_write(path, payload)
        return relative, digest

    def record(self, folder: str, identity: str, value: Mapping[str, Any]) -> None:
        _atomic_json(self.root / folder / f"{identity}.json", value)

    def records(self, folder: str) -> list[dict[str, Any]]:
        return _records(self.root / folder)


def _get_json(
    *, client: PublicHttpClient, workspace: Workspace, url: str, folder: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    value, final_url, raw = client.get_json(url)
    locator, digest = workspace.content(folder=folder, payload=raw)
    return value, {
        "request_url": url,
        "final_url": final_url,
        "raw_locator": locator,
        "raw_record_sha256": digest,
        "retrieved_at": datetime.now(UTC).isoformat(),
    }


def _market_rows(
    *, client: PublicHttpClient, workspace: Workspace, ticker: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    value, receipt = _get_json(
        client=client,
        workspace=workspace,
        url=(
            f"{KALSHI_API}/historical/markets?"
            f"{urlencode({'limit': str(MARKET_PAGE_LIMIT), 'series_ticker': ticker})}"
        ),
        folder="sealed/raw_market_lists",
    )
    markets = value.get("markets")
    if not isinstance(markets, list):
        raise NativeAcquisitionError("native_historical_market_list_invalid")
    return [row for row in markets if isinstance(row, dict)], receipt


def _market_detail(
    *, client: PublicHttpClient, workspace: Workspace, ticker: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    value, receipt = _get_json(
        client=client,
        workspace=workspace,
        url=f"{KALSHI_API}/historical/markets/{ticker}",
        folder="sealed/raw_market_records",
    )
    market = value.get("market")
    if not isinstance(market, dict):
        raise NativeAcquisitionError("native_historical_market_detail_invalid")
    return market, receipt


def _record_failure(
    *, workspace: Workspace, source_record_id: str, reason: str, retrieved_at: datetime
) -> None:
    workspace.record(
        "state/provenance_failures",
        sha256_text(source_record_id)[:24],
        native_failure_packet(
            source_record_id=source_record_id, reason=reason, retrieved_at=retrieved_at
        ),
    )


def _prepare_workspace(
    *, workspace: Workspace, source_sha: str, source_tree: str, protected_hashes: Mapping[str, str]
) -> dict[str, Any]:
    manifest_path = workspace.root / "manifest.json"
    if manifest_path.exists():
        manifest = _load_json(manifest_path)
        if manifest.get("source_sha") != source_sha or manifest.get("policy_version") != NATIVE_CORPUS_POLICY:
            raise NativeAcquisitionError("native_workspace_identity_mismatch")
        return manifest
    workspace.root.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "label": WORKSPACE_LABEL,
        "policy_version": NATIVE_CORPUS_POLICY,
        "source_family": NATIVE_SOURCE_ID,
        "source_sha": source_sha,
        "source_tree": source_tree,
        "protected_hashes": dict(protected_hashes),
        "retrieved_at": datetime.now(UTC).isoformat(),
        "outcome_blinding": "separate sealed outcome records",
        "review_artifacts_created": False,
        "release_frozen": False,
        "source_audit_catalog": list(SOURCE_AUDIT_CATALOG),
    }
    _atomic_json(manifest_path, manifest)
    return manifest


def _write_final_records(
    *, workspace: Workspace, candidates: Sequence[Mapping[str, Any]], outcomes: Sequence[Mapping[str, Any]], packets: Sequence[Mapping[str, Any]], documents: Sequence[Mapping[str, Any]], failures: Sequence[Mapping[str, Any]], split: Mapping[str, Any]
) -> None:
    _atomic_jsonl(workspace.root / "records/blinded_candidates.jsonl", list(candidates))
    _atomic_jsonl(workspace.root / "sealed/provisional_outcomes.jsonl", list(outcomes))
    _atomic_jsonl(workspace.root / "records/evidence_packets.jsonl", list(packets))
    _atomic_jsonl(workspace.root / "records/evidence_documents.jsonl", list(documents))
    _atomic_jsonl(workspace.root / "records/provenance_failures.jsonl", list(failures))
    _atomic_json(workspace.root / "records/provisional_split.json", dict(split))


def _summary(
    *, manifest: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]], packets: Sequence[Mapping[str, Any]], documents: Sequence[Mapping[str, Any]], failures: Sequence[Mapping[str, Any]], split: Mapping[str, Any], v2_preflight: Mapping[str, Any], native_preflight: Mapping[str, Any], request_count: int
) -> dict[str, Any]:
    category_counts = Counter(str(row["category"]) for row in candidates)
    reasons = Counter(str(row.get("reason") or "unknown") for row in failures)
    result: dict[str, Any] = {
        "schema_version": 1,
        "policy_version": NATIVE_CORPUS_POLICY,
        "source_sha": manifest["source_sha"],
        "source_tree": manifest["source_tree"],
        "candidate_count": len(candidates),
        "ready_candidate_count": len(candidates),
        "native_ready_threshold_met": len(candidates) >= 200,
        "source_family_counts": {NATIVE_SOURCE_ID: len(candidates)},
        "source_audit_catalog": manifest["source_audit_catalog"],
        "category_distribution": dict(sorted(category_counts.items())),
        "packet_counts": dict(sorted(Counter(str(row["status"]) for row in packets).items())),
        "accepted_document_count": len(documents),
        "provenance_failure_count": len(failures),
        "provenance_failure_reasons": dict(sorted(reasons.items())),
        "split": dict(split),
        "v2_preflight_hash": v2_preflight["preflight_hash"],
        "native_preflight_hash": native_preflight["preflight_hash"],
        "sealed_input_ready": native_preflight["ready"],
        "review_artifacts_created": False,
        "release_frozen": False,
        "forecast_runs": 0,
        "experiments": 0,
        "scores": 0,
        "calibration": None,
        "openai_calls": 0,
        "tavily_calls": 0,
        "provider_spend_usd": 0.0,
        "public_http_request_count": request_count,
    }
    result["summary_hash"] = sha256_text(canonical_json(result))
    return result


def acquire(
    *, source: Path, source_sha: str, workspace_path: Path, target: int, client: PublicHttpClient, max_series_pages: int, max_series_requests: int, environment: Mapping[str, str] | None = None
) -> dict[str, Any]:
    if target < 200:
        raise NativeAcquisitionError("native_target_must_preserve_v2_minimum")
    preflight_result = preflight(
        source=source, source_sha=source_sha, workspace=workspace_path, environment=environment
    )
    workspace = Workspace(workspace_path)
    summary_path = workspace.root / "summary.json"
    if summary_path.exists():
        return verify_workspace(
            source=source, source_sha=source_sha, workspace_path=workspace_path, environment=environment
        )["summary"]
    manifest = _prepare_workspace(
        workspace=workspace,
        source_sha=source_sha,
        source_tree=str(preflight_result["source_tree"]),
        protected_hashes=preflight_result["protected_hashes"],
    )
    existing_candidates = workspace.records("state/candidates")
    existing_ids = {str(row["candidate_id"]) for row in existing_candidates}
    existing_events = {str(row["grouping_features"]["event_ticker"]) for row in existing_candidates}
    completed_series = {str(row["ticker"]) for row in workspace.records("state/series") if row.get("status") == "completed"}
    del max_series_pages  # Native allowlist avoids the upstream unbounded series catalogue.
    for category, series_ticker in KALSHI_NATIVE_SERIES:
        if len(existing_candidates) >= target or len(completed_series) >= max_series_requests:
            break
        if series_ticker in completed_series:
            continue
        try:
            markets, receipt = _market_rows(client=client, workspace=workspace, ticker=series_ticker)
        except NativeAcquisitionError as exc:
            _record_failure(workspace=workspace, source_record_id=series_ticker, reason=str(exc), retrieved_at=datetime.now(UTC))
            workspace.record("state/series", series_ticker, {"ticker": series_ticker, "category": category, "status": "failed", "reason": str(exc)})
            completed_series.add(series_ticker)
            continue
        accepted = 0
        for listed in sorted(markets, key=lambda row: str(row.get("ticker") or "")):
            if len(existing_candidates) >= target:
                break
            ticker = str(listed.get("ticker") or "")
            event = str(listed.get("event_ticker") or "")
            if not ticker or not event or event in existing_events:
                continue
            try:
                detail, detail_receipt = _market_detail(client=client, workspace=workspace, ticker=ticker)
                candidate, outcome, packet = native_candidate_from_market(
                    market=detail,
                    category=category,
                    retrieved_at=datetime.fromisoformat(str(detail_receipt["retrieved_at"])),
                    raw_record_hash=str(detail_receipt["raw_record_sha256"]),
                )
            except (NativeAcquisitionError, NativeCorpusError) as exc:
                _record_failure(workspace=workspace, source_record_id=ticker or series_ticker, reason=str(exc), retrieved_at=datetime.now(UTC))
                continue
            if candidate["candidate_id"] in existing_ids or event in existing_events:
                continue
            workspace.record("state/candidates", candidate["candidate_id"], candidate)
            workspace.record("sealed/outcomes", candidate["candidate_id"], outcome)
            workspace.record("state/packets", candidate["candidate_id"], packet)
            workspace.record("state/documents", candidate["candidate_id"], native_document_from_packet(packet))
            workspace.record("state/native_records", candidate["candidate_id"], {**detail_receipt, "ticker": ticker, "event_ticker": event, "category": category})
            existing_candidates.append(candidate)
            existing_ids.add(str(candidate["candidate_id"]))
            existing_events.add(event)
            accepted += 1
        workspace.record("state/series", series_ticker, {"ticker": series_ticker, "category": category, "status": "completed", "listing_receipt": receipt, "accepted_candidate_count": accepted})
        completed_series.add(series_ticker)
    candidates = attach_split(existing_candidates, provisional_split(existing_candidates))
    candidate_ids = {str(row["candidate_id"]) for row in candidates}
    outcomes = [row for row in workspace.records("sealed/outcomes") if str(row["candidate_id"]) in candidate_ids]
    packets = [row for row in workspace.records("state/packets") if str(row["candidate_id"]) in candidate_ids]
    documents = [row for row in workspace.records("state/documents") if str(row["candidate_id"]) in candidate_ids]
    failures = workspace.records("state/provenance_failures")
    split = provisional_split(candidates)
    _write_final_records(workspace=workspace, candidates=candidates, outcomes=outcomes, packets=packets, documents=documents, failures=failures, split=split)
    v2 = build_private_internal_evaluation_v2_preflight(summary={}, candidates=candidates, packets=packets, documents=documents)
    native = validate_native_v2_readiness(candidates=candidates, sealed_outcomes=outcomes, packets=packets, documents=documents)
    _atomic_json(workspace.root / "records/v2_preflight.json", v2)
    _atomic_json(workspace.root / "records/native_sealed_input_preflight.json", native)
    summary = _summary(manifest=manifest, candidates=candidates, packets=packets, documents=documents, failures=failures, split=split, v2_preflight=v2, native_preflight=native, request_count=client.request_count)
    _atomic_json(summary_path, summary)
    return summary


def verify_workspace(
    *, source: Path, source_sha: str, workspace_path: Path, environment: Mapping[str, str] | None = None
) -> dict[str, Any]:
    preflight(source=source, source_sha=source_sha, workspace=workspace_path, environment=environment)
    workspace = Workspace(workspace_path)
    manifest = _load_json(workspace.root / "manifest.json")
    summary = _load_json(workspace.root / "summary.json")
    if manifest.get("source_sha") != source_sha or summary.get("source_sha") != source_sha:
        raise NativeAcquisitionError("native_workspace_source_identity_mismatch")
    expected_summary_hash = summary.get("summary_hash")
    if expected_summary_hash != sha256_text(canonical_json({k: v for k, v in summary.items() if k != "summary_hash"})):
        raise NativeAcquisitionError("native_workspace_summary_hash_mismatch")
    for relative in ("records/blinded_candidates.jsonl", "sealed/provisional_outcomes.jsonl", "records/evidence_packets.jsonl", "records/evidence_documents.jsonl", "records/v2_preflight.json", "records/native_sealed_input_preflight.json"):
        if not (workspace.root / relative).is_file():
            raise NativeAcquisitionError(f"native_workspace_record_missing:{relative}")
    for path in (workspace.root / "sealed/raw_market_records").glob("*"):
        if path.is_file() and path.name != _sha256_file(path):
            raise NativeAcquisitionError("native_workspace_raw_hash_mismatch")
    return {"verified": True, "summary": summary, "network_requests": 0, "provider_calls": 0, "provider_spend_usd": 0.0}


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--target", type=int, default=260)
    parser.add_argument("--max-series-requests", type=int, default=600)
    parser.add_argument("--http-timeout", type=float, default=20.0)
    parser.add_argument("--request-delay", type=float, default=0.03)
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    source = Path(__file__).resolve().parents[1]
    try:
        if args.verify_only:
            result = verify_workspace(source=source, source_sha=args.source_sha, workspace_path=args.workspace)
        else:
            result = acquire(
                source=source,
                source_sha=args.source_sha,
                workspace_path=args.workspace,
                target=args.target,
                max_series_pages=0,
                max_series_requests=args.max_series_requests,
                client=PublicHttpClient(timeout=args.http_timeout, delay=args.request_delay),
            )
    except NativeAcquisitionError as exc:
        print(canonical_json({"status": "failed", "error": str(exc).split(":", 1)[0]}))
        return 2
    summary = result["summary"] if args.verify_only else result
    print(canonical_json({"summary_hash": summary["summary_hash"], "ready_candidate_count": summary["ready_candidate_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
