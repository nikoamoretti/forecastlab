"""Local, role-separated input bundles for procedural evaluation review.

This module deliberately does not create review artifacts or invoke a model.  It
materializes validated role-specific manifests, then runs an explicitly named
program under the macOS sandbox.  The outer controller is the only component
that can append a receipt; sandboxed review programs cannot read either prior
receipts or the opposite role's inputs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.procedural_ai_review import (
    OutcomeAdjudicationManifest,
    QuestionReviewManifest,
)

ReviewRole = Literal["question_review", "outcome_adjudication"]


class SealedReviewRunnerError(RuntimeError):
    """Raised when an input bundle or receipt boundary cannot be verified."""


@dataclass(frozen=True)
class SealedRoleBundle:
    role: ReviewRole
    root: Path
    manifest_paths: tuple[Path, ...]
    manifest_hash: str
    profile_path: Path


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _write_new(path: Path, content: str) -> None:
    """Append-only write: a path can be created once but never overwritten."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise SealedReviewRunnerError("sealed_path_already_exists") from exc


def _profile(*, campaign_root: Path, role_root: Path) -> str:
    """Allow the selected input root and no user-home paths outside it.

    ``sandbox-exec`` requires ordinary macOS runtime access.  The later allow
    for the selected root is the sole exception to the user-home read deny.
    Network access is denied.  Receipt persistence happens outside this sandbox
    after the program exits successfully.
    """

    campaign_escaped = (
        str(campaign_root.resolve()).replace("\\", "\\\\").replace('"', '\\"')
    )
    role_escaped = str(role_root.resolve()).replace("\\", "\\\\").replace('"', '\\"')
    return "\n".join(
        (
            "(version 1)",
            "(allow default)",
            '(deny file-read* (subpath "/Users/nico-yardlogix"))',
            f'(deny file-read* (subpath "{campaign_escaped}"))',
            f'(allow file-read* (subpath "{role_escaped}"))',
            "(deny file-write*)",
            "(deny network*)",
            "",
        )
    )


def _manifest_payload(
    role: ReviewRole,
    manifest: QuestionReviewManifest | OutcomeAdjudicationManifest,
) -> dict[str, object]:
    if role == "question_review" and not isinstance(manifest, QuestionReviewManifest):
        raise SealedReviewRunnerError("question_review_manifest_required")
    if role == "outcome_adjudication" and not isinstance(
        manifest, OutcomeAdjudicationManifest
    ):
        raise SealedReviewRunnerError("outcome_adjudication_manifest_required")
    return manifest.model_dump(mode="json")


def materialize_review_campaign(
    *,
    root: Path,
    question_manifests: list[QuestionReviewManifest],
    outcome_manifests: list[OutcomeAdjudicationManifest],
    repository_root: Path,
) -> dict[ReviewRole, SealedRoleBundle]:
    """Create both read-only role input directories outside the repository."""

    root = root.expanduser().resolve()
    repository_root = repository_root.resolve()
    if _is_within(root, repository_root):
        raise SealedReviewRunnerError("sealed_bundle_root_must_be_outside_repository")
    if root.exists():
        raise SealedReviewRunnerError("sealed_bundle_root_already_exists")
    if shutil.which("sandbox-exec") is None:
        raise SealedReviewRunnerError("macos_sandbox_exec_required")

    by_role: dict[ReviewRole, list[QuestionReviewManifest | OutcomeAdjudicationManifest]] = {
        "question_review": list(question_manifests),
        "outcome_adjudication": list(outcome_manifests),
    }
    bundles: dict[ReviewRole, SealedRoleBundle] = {}
    for role, manifests in by_role.items():
        role_root = root / "inputs" / role
        manifest_dir = role_root / "manifests"
        payloads = [_manifest_payload(role, item) for item in manifests]
        payloads.sort(key=lambda item: str(item["evaluation_question_id"]))
        manifest_paths: list[Path] = []
        for payload in payloads:
            question_id = str(payload["evaluation_question_id"])
            path = manifest_dir / f"{question_id}.json"
            _write_new(path, canonical_json(payload) + "\n")
            path.chmod(0o400)
            manifest_paths.append(path)
        profile_path = root / "profiles" / f"{role}.sb"
        _write_new(
            profile_path,
            _profile(campaign_root=root, role_root=role_root),
        )
        profile_path.chmod(0o400)
        manifest_hash = sha256_text(canonical_json(payloads))
        bundles[role] = SealedRoleBundle(
            role=role,
            root=role_root,
            manifest_paths=tuple(manifest_paths),
            manifest_hash=manifest_hash,
            profile_path=profile_path,
        )
    _write_new(
        root / "bundle.json",
        canonical_json(
            {
                "schema_version": 1,
                "roles": {
                    role: {
                        "manifest_count": len(bundle.manifest_paths),
                        "manifest_hash": bundle.manifest_hash,
                    }
                    for role, bundle in bundles.items()
                },
                "receipt_handoff": "outer_controller_append_only",
            }
        )
        + "\n",
    )
    return bundles


def sandbox_read_probe(
    bundle: SealedRoleBundle,
    target: Path,
) -> subprocess.CompletedProcess[str]:
    """Run a read-only probe under the generated profile for negative tests."""

    return subprocess.run(
        ["sandbox-exec", "-f", str(bundle.profile_path), "/bin/cat", str(target)],
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C"},
    )


def sandbox_network_probe(bundle: SealedRoleBundle) -> subprocess.CompletedProcess[str]:
    """Verify the profile denies outbound network access without exposing data."""

    return subprocess.run(
        [
            "sandbox-exec",
            "-f",
            str(bundle.profile_path),
            "/usr/bin/curl",
            "--connect-timeout",
            "1",
            "--max-time",
            "2",
            "-sS",
            "https://example.com",
        ],
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C"},
    )


def run_sealed_role(
    bundle: SealedRoleBundle,
    command: list[str],
) -> subprocess.CompletedProcess[str]:
    """Run one supplied review program with only its typed role inputs.

    Process output returns to the outer controller and is deliberately not
    persisted here. A controller must validate any later result and append a
    receipt only after sandbox exit. This function cannot create a review
    artifact and is not invoked during provenance restoration.
    """

    if not command:
        raise SealedReviewRunnerError("sealed_role_command_required")
    return subprocess.run(
        ["sandbox-exec", "-f", str(bundle.profile_path), *command],
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C"},
    )


def append_outer_receipt(
    *,
    bundle_root: Path,
    role: ReviewRole,
    manifest_hash: str,
    receipt_json: dict[str, object],
) -> Path:
    """Persist one controller-captured receipt without exposing prior receipts.

    The receipt must name the exact role and input hash.  The UUID filename is
    generated by the controller and opened with exclusive creation, preventing
    a sandboxed program from rewriting an earlier receipt.
    """

    if receipt_json.get("role") != role:
        raise SealedReviewRunnerError("receipt_role_mismatch")
    if receipt_json.get("input_manifest_hash") != manifest_hash:
        raise SealedReviewRunnerError("receipt_manifest_hash_mismatch")
    receipt_id = str(uuid.uuid4())
    path = bundle_root.resolve() / "receipts" / role / f"{receipt_id}.json"
    _write_new(path, canonical_json(receipt_json) + "\n")
    path.chmod(0o400)
    return path
