#!/usr/bin/env python3
"""Run the authorized private-V1 V3 procedural review through Cursor CLI.

The controller is deliberately outside the repository and has the only write
access to immutable receipts. Each Cursor invocation starts a fresh read-only
``ask`` context in an otherwise empty per-role workspace containing exactly one
typed manifest. The model returns a strict review-output object; the controller
constructs, validates, hashes, and appends the complete artifact. It never runs
a forecast, creates an EvaluationRelease, or exposes one role's input to the
other role.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.native_provenance_corpus import native_review_manifests
from forecastlab.procedural_ai_review import (
    PROCEDURAL_AI_REVIEW_POLICY_V2,
    CodexReviewRunIdentity,
    OutcomeAdjudicationArtifact,
    OutcomeAdjudicationManifest,
    OutcomeAdjudicationOutput,
    QuestionReviewArtifact,
    QuestionReviewManifest,
    QuestionReviewOutput,
    artifact_integrity_reasons,
    parse_review_artifact,
    rubric_for,
    rubric_hash,
)
from forecastlab.sealed_review_runner import materialize_review_campaign

ReviewRole = Literal["question_review", "outcome_adjudication"]
POLICY_VERSION = PROCEDURAL_AI_REVIEW_POLICY_V2
PRIMARY_MODEL = "gpt-5.3-codex-low-fast"
ESCALATION_MODEL = "gpt-5.3-codex-low"
CURSOR_COMMAND = "agent"
REQUIRED_LOCKS = ("uv.lock", "pyproject.toml", "apps/web/package-lock.json")
PROTECTED_IDENTITY_FILES = (
    "configs/forecast_profiles/graph_forecaster_v1.yaml",
    "configs/forecast_profiles/graph_live_smoke_v1.yaml",
    "packages/forecasting/forecastlab/prompts.py",
    "uv.lock",
    "apps/web/package-lock.json",
)
SENSITIVE_FORECAST_ENV = (
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "FORECASTLAB_MODEL_API_KEY",
    "FORECASTLAB_SEARCH_API_KEY",
    "FORECASTLAB_DATABASE_URL",
)


class NativeReviewExecutionError(RuntimeError):
    """A deterministic review-controller failure."""


def _now() -> datetime:
    return datetime.now(UTC)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _load_json(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise NativeReviewExecutionError(f"review_json_object_required:{path.name}")
    return result


def _atomic_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(canonical_json(value) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise NativeReviewExecutionError("review_immutable_path_exists") from exc


def _git(source: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=source, check=True, capture_output=True, text=True
    ).stdout.strip()


def _within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _hash_file(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))


def _protected_hashes(source: Path) -> dict[str, str]:
    """Freeze the protected identities without loading a ForecastLab runtime."""

    return {
        relative: _hash_file(source / relative)
        for relative in PROTECTED_IDENTITY_FILES
    }


def _cursor_models() -> set[str]:
    """Read the local Cursor catalog; this is metadata, never a model request."""

    result = subprocess.run(
        [CURSOR_COMMAND, "models"], check=True, capture_output=True, text=True
    )
    return {
        line.split(maxsplit=1)[0]
        for line in result.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def preflight(
    *,
    source: Path,
    source_sha: str,
    corpus_root: Path,
    review_root: Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate the exact source/corpus boundary before any Cursor call."""

    environment = environment if environment is not None else os.environ
    source = source.resolve()
    corpus_root = corpus_root.expanduser().resolve()
    review_root = review_root.expanduser().resolve()
    if _within(review_root, source) or _within(review_root, corpus_root):
        raise NativeReviewExecutionError("review_root_must_be_external_and_separate")
    if _git(source, "rev-parse", "HEAD") != source_sha:
        raise NativeReviewExecutionError("review_source_sha_mismatch")
    if _git(source, "status", "--porcelain=v1"):
        raise NativeReviewExecutionError("review_source_worktree_dirty")
    if any(not (source / item).is_file() for item in REQUIRED_LOCKS):
        raise NativeReviewExecutionError("review_required_lock_missing")
    if any(environment.get(item) for item in SENSITIVE_FORECAST_ENV):
        raise NativeReviewExecutionError("review_forecast_provider_or_database_environment_present")
    if shutil.which(CURSOR_COMMAND) is None:
        raise NativeReviewExecutionError("review_cursor_cli_unavailable")
    try:
        models = _cursor_models()
    except subprocess.CalledProcessError as exc:
        raise NativeReviewExecutionError("review_cursor_model_catalog_unavailable") from exc
    if PRIMARY_MODEL not in models:
        raise NativeReviewExecutionError("review_primary_cursor_model_unavailable")
    for relative in (
        "manifest.json",
        "summary.json",
        "records/blinded_candidates.jsonl",
        "sealed/provisional_outcomes.jsonl",
        "records/evidence_packets.jsonl",
        "records/evidence_documents.jsonl",
    ):
        if not (corpus_root / relative).is_file():
            raise NativeReviewExecutionError(f"review_corpus_record_missing:{relative}")
    summary = _load_json(corpus_root / "summary.json")
    if summary.get("candidate_count") != 260 or summary.get("ready_candidate_count") != 260:
        raise NativeReviewExecutionError("review_native_corpus_count_invalid")
    if not summary.get("sealed_input_ready"):
        raise NativeReviewExecutionError("review_native_sealed_input_not_ready")
    return {
        "source_sha": source_sha,
        "source_tree": _git(source, "rev-parse", "HEAD^{tree}"),
        "corpus_manifest_hash": _hash_file(corpus_root / "manifest.json"),
        "corpus_summary_hash": summary.get("summary_hash"),
        "corpus_summary_file_hash": _hash_file(corpus_root / "summary.json"),
        "protected_hashes": _protected_hashes(source),
        "candidate_count": 260,
    }


def _instruction(
    *,
    role: ReviewRole,
    manifest: QuestionReviewManifest | OutcomeAdjudicationManifest,
) -> str:
    rubric = rubric_for(POLICY_VERSION, role)
    expected = (
        "question_review_output"
        if role == "question_review"
        else "outcome_adjudication_output"
    )
    return "\n".join(
        (
            "You are a procedural private-V1 evaluation reviewer.",
            "Use only the single typed manifest below. Do not use tools, files, external knowledge,",
            "or any information not present in the manifest. Do not explain your reasoning.",
            "Return exactly one JSON object and no Markdown, prose wrapper, or code fence.",
            f"The object must have output_type={expected!r}, a permitted decision, findings,",
            "uncertainties, and conflicts. Cite only manifest source_ids. Each required finding",
            "must have a unique code and source_ids. If the manifest cannot support a required",
            "finding, use status 'uncertain' or 'fail' and a terminal decision; do not invent facts.",
            f"Required finding codes: {','.join(rubric.required_finding_codes)}.",
            "Do not include outcome, score, probability, calibration, profile, prompt, or peer-review data",
            "unless the typed manifest itself defines it for this role.",
            "MANIFEST:",
            canonical_json(manifest.model_dump(mode="json")),
        )
    )


def _terminal_failure(
    *,
    role: ReviewRole,
    manifest_hash: str,
    attempt_id: str,
    error_code: str,
    started_at: datetime,
    completed_at: datetime,
    model_id: str,
    cli_version: str,
    stdout_length: int = 0,
    stderr_hash: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_type": "procedural_review_terminal_failure",
        "role": role,
        "policy_version": POLICY_VERSION,
        "input_manifest_hash": manifest_hash,
        "attempt_id": attempt_id,
        "model_provider": "cursor_hosted_codex",
        "model_id": model_id,
        "cursor_cli_version": cli_version,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "error_code": error_code,
        "stdout_length": stdout_length,
        "stderr_hash": stderr_hash,
        "raw_response_persisted": False,
    }


def _parse_model_output(
    *, role: ReviewRole, text: str
) -> QuestionReviewOutput | OutcomeAdjudicationOutput:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise NativeReviewExecutionError("review_cursor_output_invalid_json") from exc
    if not isinstance(payload, dict):
        raise NativeReviewExecutionError("review_cursor_output_object_required")
    if role == "question_review":
        return QuestionReviewOutput.model_validate(payload)
    return OutcomeAdjudicationOutput.model_validate(payload)


def _artifact_payload(
    *,
    role: ReviewRole,
    manifest: QuestionReviewManifest | OutcomeAdjudicationManifest,
    output: QuestionReviewOutput | OutcomeAdjudicationOutput,
    started_at: datetime,
    completed_at: datetime,
    model_id: str,
    cli_version: str,
    attempt_id: str,
) -> dict[str, Any]:
    rubric = rubric_for(POLICY_VERSION, role)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": role,
        "policy_version": POLICY_VERSION,
        "rubric_version": rubric.version,
        "rubric_hash": rubric_hash(rubric),
        "run_identity": CodexReviewRunIdentity(
            role=role,
            model_provider="cursor_hosted_codex",
            model_id=model_id,
            model_version="cursor_cli_transport_identity_unreported",
            tool_version=cli_version,
            run_id=attempt_id,
        ).model_dump(mode="json"),
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "input_manifest": manifest.model_dump(mode="json"),
        "input_manifest_hash": sha256_text(canonical_json(manifest.model_dump(mode="json"))),
        "output": output.model_dump(mode="json"),
        "output_hash": sha256_text(canonical_json(output.model_dump(mode="json"))),
    }
    return payload


def _transport_workspace(*, root: Path, role: ReviewRole, attempt_id: str, manifest: Mapping[str, Any]) -> Path:
    workspace = root / "transport" / role / attempt_id
    workspace.mkdir(parents=True, exist_ok=False)
    path = workspace / "manifest.json"
    path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    path.chmod(0o400)
    return workspace


def invoke_cursor_role(
    *,
    role: ReviewRole,
    manifest: QuestionReviewManifest | OutcomeAdjudicationManifest,
    root: Path,
    model_id: str,
    cli_version: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Call one fresh Cursor context and return a validated immutable record."""

    attempt_id = "cursor-" + str(uuid.uuid4())
    manifest_payload = manifest.model_dump(mode="json")
    manifest_hash = sha256_text(canonical_json(manifest_payload))
    workspace = _transport_workspace(
        root=root, role=role, attempt_id=attempt_id, manifest=manifest_payload
    )
    started_at = _now()
    command = [
        CURSOR_COMMAND,
        "--print",
        "--output-format",
        "text",
        "--mode",
        "ask",
        "--sandbox",
        "enabled",
        "--workspace",
        str(workspace),
        "--trust",
        "--model",
        model_id,
        _instruction(role=role, manifest=manifest),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
        )
    except subprocess.TimeoutExpired:
        return _terminal_failure(
            role=role,
            manifest_hash=manifest_hash,
            attempt_id=attempt_id,
            error_code="cursor_transport_timeout",
            started_at=started_at,
            completed_at=_now(),
            model_id=model_id,
            cli_version=cli_version,
        )
    completed_at = _now()
    stderr_hash = sha256_text(result.stderr) if result.stderr else None
    if result.returncode != 0:
        return _terminal_failure(
            role=role,
            manifest_hash=manifest_hash,
            attempt_id=attempt_id,
            error_code=f"cursor_transport_exit_{result.returncode}",
            started_at=started_at,
            completed_at=completed_at,
            model_id=model_id,
            cli_version=cli_version,
            stdout_length=len(result.stdout),
            stderr_hash=stderr_hash,
        )
    try:
        output = _parse_model_output(role=role, text=result.stdout.strip())
        payload = _artifact_payload(
            role=role,
            manifest=manifest,
            output=output,
            started_at=started_at,
            completed_at=completed_at,
            model_id=model_id,
            cli_version=cli_version,
            attempt_id=attempt_id,
        )
        artifact = parse_review_artifact(payload, artifact_type=role)
    except (NativeReviewExecutionError, ValueError) as exc:
        return _terminal_failure(
            role=role,
            manifest_hash=manifest_hash,
            attempt_id=attempt_id,
            error_code=str(exc).split(";", 1)[0].split(":", 1)[0],
            started_at=started_at,
            completed_at=completed_at,
            model_id=model_id,
            cli_version=cli_version,
            stdout_length=len(result.stdout),
            stderr_hash=stderr_hash,
        )
    assert isinstance(artifact, (QuestionReviewArtifact, OutcomeAdjudicationArtifact))
    return {
        "schema_version": 1,
        "record_type": "procedural_review_artifact",
        "role": role,
        "attempt_id": attempt_id,
        "gate_status": "passed" if not artifact_integrity_reasons(artifact) else "failed",
        "gate_reasons": artifact_integrity_reasons(artifact),
        "cursor_cli_version": cli_version,
        "cursor_transport_model": model_id,
        "stdout_length": len(result.stdout),
        "stderr_hash": stderr_hash,
        "raw_response_persisted": False,
        "artifact": artifact.model_dump(mode="json"),
    }


def _records_by_candidate(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        candidate_id = str(row["candidate_id"])
        if candidate_id in result:
            raise NativeReviewExecutionError("review_duplicate_candidate_record")
        result[candidate_id] = dict(row)
    return result


def _eligible(record: Mapping[str, Any]) -> bool:
    return record.get("record_type") == "procedural_review_artifact" and record.get("gate_status") == "passed"


def run_campaign(
    *,
    source: Path,
    source_sha: str,
    corpus_root: Path,
    review_root: Path,
    canary_only: bool,
    timeout_seconds: int,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    receipt = preflight(
        source=source,
        source_sha=source_sha,
        corpus_root=corpus_root,
        review_root=review_root,
        environment=environment,
    )
    if review_root.exists():
        raise NativeReviewExecutionError("review_root_must_be_new_for_immutable_campaign")
    candidates = _records_by_candidate(
        _load_jsonl(corpus_root / "records/blinded_candidates.jsonl")
    )
    outcomes = _records_by_candidate(
        _load_jsonl(corpus_root / "sealed/provisional_outcomes.jsonl")
    )
    if set(candidates) != set(outcomes) or len(candidates) != 260:
        raise NativeReviewExecutionError("review_candidate_outcome_identity_mismatch")
    review_root.mkdir(parents=True)
    _atomic_new(
        review_root / "campaign.json",
        {
            "schema_version": 1,
            "policy_version": POLICY_VERSION,
            "source": receipt,
            "cursor_cli": CURSOR_COMMAND,
            "cursor_cli_version": _cursor_version(),
            "primary_model": PRIMARY_MODEL,
            "escalation_model": ESCALATION_MODEL,
            "candidate_count": len(candidates),
            "canary_candidate_id": sorted(candidates)[0],
            "review_artifacts_are_human_review": False,
            "evaluation_release_frozen": False,
        },
    )
    return _execute_campaign(
        candidates=candidates,
        outcomes=outcomes,
        review_root=review_root,
        canary_only=canary_only,
        timeout_seconds=timeout_seconds,
    )


def resume_campaign(
    *,
    source: Path,
    source_sha: str,
    corpus_root: Path,
    review_root: Path,
    timeout_seconds: int,
    canary_only: bool,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    campaign = _load_json(review_root / "campaign.json")
    if campaign.get("policy_version") != POLICY_VERSION:
        raise NativeReviewExecutionError("review_campaign_policy_mismatch")
    preflight(
        source=source,
        source_sha=source_sha,
        corpus_root=corpus_root,
        review_root=review_root,
        environment=environment,
    )
    if campaign.get("source", {}).get("corpus_manifest_hash") != _hash_file(
        corpus_root / "manifest.json"
    ):
        raise NativeReviewExecutionError("review_campaign_corpus_manifest_mismatch")
    candidates = _records_by_candidate(
        _load_jsonl(corpus_root / "records/blinded_candidates.jsonl")
    )
    outcomes = _records_by_candidate(
        _load_jsonl(corpus_root / "sealed/provisional_outcomes.jsonl")
    )
    return _execute_campaign(
        candidates=candidates,
        outcomes=outcomes,
        review_root=review_root,
        canary_only=canary_only,
        timeout_seconds=timeout_seconds,
    )


def _cursor_version() -> str:
    result = subprocess.run(
        [CURSOR_COMMAND, "--version"], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _execute_campaign(
    *,
    candidates: Mapping[str, Mapping[str, Any]],
    outcomes: Mapping[str, Mapping[str, Any]],
    review_root: Path,
    canary_only: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    cli_version = _cursor_version()
    candidate_ids = [sorted(candidates)[0]] if canary_only else sorted(candidates)
    terminal_ids = {
        path.parents[1].name for path in (review_root / "receipts").glob("*/*/*.json")
    }
    for candidate_id in candidate_ids:
        if candidate_id in terminal_ids:
            continue
        question_manifest, outcome_manifest = native_review_manifests(
            candidate=candidates[candidate_id], sealed_outcome=outcomes[candidate_id]
        )
        bundle_root = review_root / "bundles" / candidate_id
        bundles = materialize_review_campaign(
            root=bundle_root,
            question_manifests=[question_manifest],
            outcome_manifests=[outcome_manifest],
            repository_root=Path(__file__).resolve().parents[1],
        )
        for raw_role, manifest in (
            ("question_review", question_manifest),
            ("outcome_adjudication", outcome_manifest),
        ):
            role = cast(ReviewRole, raw_role)
            record = invoke_cursor_role(
                role=role,
                manifest=manifest,
                root=review_root,
                model_id=PRIMARY_MODEL,
                cli_version=cli_version,
                timeout_seconds=timeout_seconds,
            )
            record["candidate_id"] = candidate_id
            record["sealed_bundle_manifest_hash"] = bundles[role].manifest_hash
            receipt_path = review_root / "receipts" / candidate_id / role / f"{record['attempt_id']}.json"
            _atomic_new(receipt_path, record)
    summary = summarize_review_root(review_root)
    _atomic_new(review_root / "summaries" / f"{summary['summary_hash']}.json", summary)
    return summary


def summarize_review_root(review_root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for path in sorted((review_root / "receipts").glob("*/*/*.json")):
        records.append(_load_json(path))
    role_counts = Counter(str(row["role"]) for row in records)
    role_valid = Counter(
        str(row["role"]) for row in records if _eligible(row)
    )
    by_candidate: dict[str, dict[str, dict[str, Any]]] = {}
    for row in records:
        by_candidate.setdefault(str(row["candidate_id"]), {})[str(row["role"])] = row
    eligible = 0
    excluded = 0
    unresolved = 0
    complete_terminal = 0
    partial_terminal = 0
    agreement = 0
    disagreement = 0
    for roles in by_candidate.values():
        question = roles.get("question_review")
        outcome = roles.get("outcome_adjudication")
        if question is None or outcome is None:
            unresolved += 1
            partial_terminal += 1
            continue
        complete_terminal += 1
        if _eligible(question) and _eligible(outcome):
            eligible += 1
            agreement += 1
        elif question.get("record_type") == "procedural_review_artifact" and outcome.get("record_type") == "procedural_review_artifact":
            excluded += 1
            disagreement += 1
        else:
            unresolved += 1
    result: dict[str, Any] = {
        "schema_version": 1,
        "policy_version": POLICY_VERSION,
        "candidate_terminal_count": len(by_candidate),
        "complete_terminal_candidate_count": complete_terminal,
        "partial_terminal_candidate_count": partial_terminal,
        "question_review": {"total": role_counts["question_review"], "valid": role_valid["question_review"], "failed": role_counts["question_review"] - role_valid["question_review"]},
        "outcome_adjudication": {"total": role_counts["outcome_adjudication"], "valid": role_valid["outcome_adjudication"], "failed": role_counts["outcome_adjudication"] - role_valid["outcome_adjudication"]},
        "eligible_candidate_count": eligible,
        "excluded_candidate_count": excluded,
        "unresolved_candidate_count": unresolved,
        "role_agreement_count": agreement,
        "role_disagreement_or_failure_count": disagreement,
        "review_artifacts_are_human_review": False,
        "evaluation_release_frozen": False,
        "forecast_runs": 0,
        "experiments": 0,
        "scores": 0,
        "calibration": None,
    }
    result["summary_hash"] = sha256_text(canonical_json(result))
    return result


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--canary-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    source = Path(__file__).resolve().parents[1]
    try:
        runner = resume_campaign if args.resume else run_campaign
        summary = runner(
            source=source,
            source_sha=args.source_sha,
            corpus_root=args.corpus_root,
            review_root=args.review_root,
            canary_only=args.canary_only,
            timeout_seconds=args.timeout_seconds,
        )
    except NativeReviewExecutionError as exc:
        print(canonical_json({"status": "failed", "error": str(exc).split(":", 1)[0]}))
        return 2
    print(canonical_json(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
