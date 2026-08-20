from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

from forecastlab.gitinfo import ROOT, current_git_commit
from forecastlab.hashing import canonical_json, sha256_text
from forecastlab.pricing import load_pricing, pricing_hash
from forecastlab.version import __version__

SOURCE_GLOBS = (
    "packages/forecasting/forecastlab/**/*.py",
    "apps/api/forecastlab_api/**/*.py",
)
IDENTITY_FILES = (
    "pyproject.toml",
    "apps/web/package-lock.json",
)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tracked_source_hash(*, root: Path | None = None) -> str:
    directory = root or ROOT
    paths: list[Path] = []
    for pattern in SOURCE_GLOBS:
        paths.extend(sorted(directory.glob(pattern)))
    digest = hashlib.sha256()
    for path in paths:
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        digest.update(str(path.relative_to(directory)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def python_dependency_hash(*, root: Path | None = None) -> str | None:
    return file_hash("uv.lock", root=root) or file_hash("poetry.lock", root=root)


def require_python_lock(*, root: Path | None = None, synthetic: bool = False) -> str | None:
    digest = python_dependency_hash(root=root)
    if digest is None and not synthetic:
        raise ValueError("python_lockfile_required")
    return digest


def file_hash(relative: str, *, root: Path | None = None) -> str | None:
    path = (root or ROOT) / relative
    if not path.exists():
        return None
    return _hash_file(path)


def working_tree_dirty(*, root: Path | None = None) -> bool:
    directory = root or ROOT
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=directory,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def build_environment_identity(
    *,
    root: Path | None = None,
    prompt_bundle_hash: str | None = None,
    profile_hashes: dict[str, str] | None = None,
    pricing_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog = pricing_catalog if pricing_catalog is not None else load_pricing()
    return {
        "git_commit": current_git_commit(root=root),
        "working_tree_dirty": working_tree_dirty(root=root),
        "tracked_source_hash": tracked_source_hash(root=root),
        "pyproject_hash": file_hash("pyproject.toml", root=root),
        "dependency_hash": python_dependency_hash(root=root),
        "package_lock_hash": file_hash("apps/web/package-lock.json", root=root),
        "prompt_bundle_hash": prompt_bundle_hash,
        "profile_hashes": profile_hashes or {},
        "pricing_hash": pricing_hash(catalog=catalog),
        "application_version": __version__,
        "container_image_digest": os.environ.get("FORECASTLAB_IMAGE_DIGEST"),
    }


def environment_identity_hash(identity: dict[str, Any]) -> str:
    payload = {key: value for key, value in identity.items() if key != "working_tree_dirty"}
    return sha256_text(canonical_json(payload))


def compare_environment(frozen: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Compare code and dependency identity. Pricing is frozen separately and is not fail-closed here."""
    mismatches: list[str] = []
    keys = (
        "git_commit",
        "tracked_source_hash",
        "pyproject_hash",
        "dependency_hash",
        "package_lock_hash",
        "application_version",
    )
    for key in keys:
        if frozen.get(key) != current.get(key):
            mismatches.append(key)
    frozen_digest = frozen.get("container_image_digest")
    current_digest = current.get("container_image_digest")
    if frozen_digest and current_digest and frozen_digest != current_digest:
        mismatches.append("container_image_digest")
    return mismatches
