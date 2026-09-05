#!/usr/bin/env python3
"""Restore auditable V2 source receipts for the frozen evidence-ready tranche.

Only URLs and final Wayback captures already recorded by H017 are contacted.
The script never creates a review artifact, forecast, release, score, or model
request.  It writes a new private receipt directory outside the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from forecastlab.fetch import FetchLimits, fetch_document
from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.review_provenance import (
    candidate_terminal_reasons,
    restored_snapshot_source,
)
from forecastlab.timeutil import parse_datetime, utcnow

ALLOWLIST_HASH = "e7e7385ac6eb97e7c5edbf2e337df5687a272d43e86b6f34f21d1da28e8c00f7"
SENSITIVE_ENV_KEYS = (
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "FORECASTLAB_MODEL_API_KEY",
    "FORECASTLAB_SEARCH_API_KEY",
)


class RestorationError(RuntimeError):
    pass


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_new(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise RestorationError("output_path_already_exists") from exc


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _outside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return True
    return False


def restore(*, repository: Path, workspace: Path, output: Path) -> dict[str, Any]:
    repository = repository.resolve()
    workspace = workspace.resolve()
    output = output.resolve()
    if not _outside(output, repository) or not _outside(output, workspace):
        raise RestorationError("output_must_be_outside_repository_and_source_workspace")
    if output.exists():
        raise RestorationError("output_directory_already_exists")
    if any(os.environ.get(key) for key in SENSITIVE_ENV_KEYS):
        raise RestorationError("provider_credentials_must_not_be_loaded")

    candidates = _read_jsonl(workspace / "records/blinded_candidates.jsonl")
    packets = {
        row["candidate_id"]: row
        for row in _read_jsonl(workspace / "records/evidence_packets.jsonl")
    }
    documents = _read_jsonl(workspace / "records/evidence_documents.jsonl")
    attempts = _read_jsonl(workspace / "records/archive_attempts.jsonl")
    ready = sorted(
        row["candidate_id"] for row in candidates if packets[row["candidate_id"]]["status"] == "ready"
    )
    allowlist_hash = sha256_text("\n".join(ready) + "\n")
    if allowlist_hash != ALLOWLIST_HASH:
        raise RestorationError("evidence_ready_allowlist_mismatch")
    candidate_by_id = {row["candidate_id"]: row for row in candidates}
    docs_by_id = {row["document_id"]: row for row in documents}
    attempts_by_document = {
        row.get("document_id"): row for row in attempts if row.get("status") == "accepted"
    }
    attempts_by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        attempts_by_candidate[attempt["candidate_id"]].append(attempt)

    restored: list[dict[str, Any]] = []
    candidates_out: list[dict[str, Any]] = []
    for candidate_id in ready:
        packet = packets[candidate_id]
        source_receipts: list[dict[str, Any]] = []
        for document_id in packet["accepted_document_ids"]:
            document = docs_by_id[document_id]
            attempt = attempts_by_document.get(document_id)
            if attempt is None:
                source_receipts.append(
                    {
                        "candidate_id": candidate_id,
                        "document_id": document_id,
                        "status": "failed",
                        "reasons": ["accepted_document_archive_attempt_missing"],
                    }
                )
                continue
            snapshot_at = parse_datetime(str(document["source_available_at"]))
            cutoff = parse_datetime(str(attempt["cutoff"]))
            if snapshot_at is None or cutoff is None:
                source_receipts.append(
                    {
                        "candidate_id": candidate_id,
                        "document_id": document_id,
                        "status": "failed",
                        "reasons": ["recorded_historical_timestamp_invalid"],
                    }
                )
                continue
            blob_hash_verified = _sha256_file(
                workspace / str(document["blob_locator"])
            ) == document["content_sha256"]
            text_hash_verified = _sha256_file(
                workspace / str(document["text_locator"])
            ) == document["extracted_text_sha256"]
            fetched = fetch_document(
                str(document["canonical_url"]),
                mode="backtest",
                as_of=cutoff,
                snapshot_url=str(attempt["final_snapshot_url"]),
                snapshot_at=snapshot_at,
                allow_local_fixtures=False,
                limits=FetchLimits(max_bytes=2_000_000, timeout=20.0),
            )
            source_receipts.append(
                restored_snapshot_source(
                    candidate_id=candidate_id,
                    document=document,
                    attempt=attempt,
                    fetched=fetched,
                    stored_blob_hash_verified=blob_hash_verified,
                    stored_text_hash_verified=text_hash_verified,
                )
            )
        restored.extend(source_receipts)
        origin_attempts = [
            item
            for item in attempts_by_candidate[candidate_id]
            if item.get("requested_original_url") == candidate_by_id[candidate_id]["origin_url"]
        ]
        terminal_reasons = candidate_terminal_reasons(
            origin_attempts=origin_attempts,
            restored_sources=source_receipts,
        )
        candidates_out.append(
            {
                "candidate_id": candidate_id,
                "status": "terminal_provenance_failure" if terminal_reasons else "review_ready",
                "question_review_manifest_created": False,
                "outcome_adjudication_manifest_created": False,
                "reasons": terminal_reasons,
                "source_failure_reasons": sorted(
                    {
                        reason
                        for item in source_receipts
                        for reason in item.get("reasons", [])
                    }
                ),
                "restored_source_count": len(source_receipts),
                "restored_complete_source_count": sum(
                    item.get("status") == "complete" for item in source_receipts
                ),
            }
        )

    output.mkdir(parents=True, exist_ok=False)
    _write_new(output / "restored_sources.jsonl", "\n".join(canonical_json(row) for row in restored) + "\n")
    _write_new(output / "candidate_status.jsonl", "\n".join(canonical_json(row) for row in candidates_out) + "\n")
    summary = {
        "schema_version": 1,
        "policy_version": "private_v1_procedural_ai_review_v2",
        "allowlist_hash": allowlist_hash,
        "candidate_count": len(candidates_out),
        "review_ready_count": sum(row["status"] == "review_ready" for row in candidates_out),
        "terminal_provenance_failure_count": sum(
            row["status"] == "terminal_provenance_failure" for row in candidates_out
        ),
        "restored_source_count": len(restored),
        "restored_complete_source_count": sum(row.get("status") == "complete" for row in restored),
        "generated_at": utcnow().isoformat(),
        "no_review_artifacts_created": True,
        "no_model_or_paid_provider_requests": True,
    }
    summary["summary_hash"] = sha256_text(canonical_json(summary))
    _write_new(output / "summary.json", canonical_json(summary) + "\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = restore(
            repository=args.repository,
            workspace=args.workspace,
            output=args.output,
        )
    except RestorationError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"summary_hash": summary["summary_hash"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
