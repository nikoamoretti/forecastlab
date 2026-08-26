#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

CANDIDATE_LABEL = "private-v1-verification-v1"
FIXTURE_LABEL = "synthetic_release_verification_only"
REQUIRED_PROFILE = "graph_forecaster_v1"
REQUIRED_PROFILE_VERSION = 9
REQUIRED_FILES = (
    "uv.lock",
    "pyproject.toml",
    "apps/web/package-lock.json",
    "configs/forecast_profiles/graph_forecaster_v1.yaml",
    "configs/forecast_profiles/graph_live_smoke_v1.yaml",
    "fixtures/release/private_v1_verification_v1.json",
    "prompts/scenario_synthesis.txt",
    "alembic.ini",
    "alembic/env.py",
)
SENSITIVE_ENV_KEYS = {
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "FORECASTLAB_MODEL_API_KEY",
    "FORECASTLAB_SEARCH_API_KEY",
}
TERMINAL_LEDGER_STATUSES = {"succeeded", "failed", "released"}
TERMINAL_ATTEMPT_STATUSES = {"completed", "failed"}
UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)
TIMESTAMP_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\btvly-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
KNOWN_TRACKED_FIXTURE_SECRET_MATCHES = {
    # These are SHA-256 digests of deliberate synthetic/redaction test tokens,
    # never the token values. Counts pin the accepted occurrences so another
    # matching token in the same tracked file still fails closed.
    (
        "INTEGRITY_REPAIR_3_REPORT.md",
        1,
        "5bfe2fcb866dba4b0eaea7173dfdfbaea58874001c6f909745fd729a830365f0",
    ): 1,
    (
        "tests/test_api.py",
        1,
        "022fe48097fdf30d97f47195276fc9302849c9b36e55e92c23fba88e955a86fb",
    ): 2,
    (
        "tests/test_experiment_reproducibility.py",
        1,
        "3e559cf837856c003108c50260e70cb002bcb7b37e22c6cfbf9824f93895aedf",
    ): 1,
    (
        "tests/test_paid_smoke.py",
        1,
        "693f5072f49dad8d5fac77242b0ef1cad8c3b74d7b93cd9f4a54b64937efe0a3",
    ): 1,
    (
        "tests/test_paid_smoke.py",
        2,
        "0f39924710543010cff5cab570b737b2ea3e74bbddf3cfc8c87bfff597a0b320",
    ): 1,
    (
        "tests/test_provenance.py",
        1,
        "5bfe2fcb866dba4b0eaea7173dfdfbaea58874001c6f909745fd729a830365f0",
    ): 2,
    (
        "tests/test_ssrf_wayback_watchers.py",
        1,
        "5bfe2fcb866dba4b0eaea7173dfdfbaea58874001c6f909745fd729a830365f0",
    ): 2,
    (
        "tests/test_ssrf_wayback_watchers.py",
        3,
        "bdd1beec5d1347ddf55b8a6babdf720b81ddbc39d3299bfb8613cf934bdb0aba",
    ): 1,
}
RAW_OUTPUT_KEYS = {
    "raw_provider_response",
    "provider_response_body",
    "raw_response_body",
    "authorization",
    "api_key",
    "model_api_key",
    "search_api_key",
}
KNOWN_FRONTEND_ADVISORIES = (
    {
        "package": "next",
        "severity": "high",
        "via": ["postcss", "sharp"],
        "fix": "Next 16.3.3 (semver-major; not authorized in this task)",
    },
    {
        "package": "postcss",
        "severity": "high",
        "advisories": [
            "GHSA-qx2v-qp2m-jg93",
            "GHSA-6g55-p6wh-862q",
            "GHSA-fxqj-rqcc-2cmp",
            "GHSA-r28c-9q8g-f849",
        ],
    },
    {
        "package": "sharp",
        "severity": "high",
        "advisories": ["GHSA-f88m-g3jw-g9cj"],
    },
)


class VerificationError(RuntimeError):
    """Fail-closed private-V1 release-verification error."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _safe_failure_summary(output: str, *, cwd: Path) -> str:
    if _scan_text("command", output):
        return "diagnostic_redacted_secret_pattern"
    sanitized = re.sub(r"\x1b\[[0-9;]*m", "", output)
    sanitized = sanitized.replace(str(cwd), "<checkout>")
    sanitized = re.sub(
        r"/(?:private/)?var/folders/\S+",
        "<temporary-path>",
        sanitized,
    )
    sanitized = UUID_PATTERN.sub("<uuid>", sanitized)
    sanitized = TIMESTAMP_PATTERN.sub("<timestamp>", sanitized)
    interesting = [
        line.strip()
        for line in sanitized.splitlines()
        if any(
            marker in line.casefold()
            for marker in (
                "error:",
                "expected:",
                "received:",
                "locator:",
                "strict mode violation",
                "failed",
                "timed out",
            )
        )
    ]
    return " | ".join(interesting[:12])[:1200] or "no_sanitized_detail_available"


def _run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = 1200,
    log_path: Path | None = None,
    accepted_returncodes: set[int] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(completed.stdout, encoding="utf-8")
    accepted = accepted_returncodes or {0}
    if completed.returncode not in accepted:
        command = Path(args[0]).name
        stage = log_path.stem if log_path is not None else command
        summary = _safe_failure_summary(completed.stdout, cwd=cwd)
        raise VerificationError(
            f"command_failed:{stage}:{command}:exit_{completed.returncode}:{summary}"
        )
    return completed


def _git(source: Path, *args: str) -> str:
    return _run(["git", *args], cwd=source, timeout=120).stdout.strip()


def _tracked_files(source: Path) -> list[str]:
    raw = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=source,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    return sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)


def _tracked_source_hash(source: Path, tracked_files: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in tracked_files:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update((source / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _aggregate_hash(source: Path, paths: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(paths):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update((source / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _file_hashes(source: Path, paths: list[str]) -> dict[str, str]:
    return {relative: _sha256_file(source / relative) for relative in sorted(paths)}


def _ensure_output_outside_source(source: Path, output_dir: Path) -> None:
    source_real = source.resolve()
    output_real = output_dir.resolve()
    try:
        output_real.relative_to(source_real)
    except ValueError:
        return
    raise VerificationError("output_directory_must_be_outside_source_repository")


def _preflight(source: Path, source_sha: str, output_dir: Path) -> dict[str, Any]:
    _ensure_output_outside_source(source, output_dir)
    actual_sha = _git(source, "rev-parse", "HEAD")
    if actual_sha != source_sha:
        raise VerificationError(f"source_sha_mismatch:{actual_sha}")
    if _git(source, "status", "--porcelain", "--untracked-files=no"):
        raise VerificationError("tracked_source_worktree_is_dirty")
    tracked = _tracked_files(source)
    missing = [relative for relative in REQUIRED_FILES if relative not in tracked]
    prompt_files = [relative for relative in tracked if relative.startswith("prompts/")]
    migration_files = [
        relative
        for relative in tracked
        if relative.startswith("alembic/versions/") and relative.endswith(".py")
    ]
    profile_files = [
        relative
        for relative in tracked
        if relative.startswith("configs/forecast_profiles/")
        and relative.endswith(".yaml")
    ]
    if missing:
        raise VerificationError(f"required_tracked_files_missing:{','.join(missing)}")
    if not prompt_files:
        raise VerificationError("tracked_prompts_missing")
    if not migration_files:
        raise VerificationError("tracked_migrations_missing")
    if not profile_files:
        raise VerificationError("tracked_profiles_missing")
    forbidden_tracked = [
        relative
        for relative in tracked
        if (
            Path(relative).name == ".env"
            or relative.endswith((".db", ".sqlite", ".sqlite3"))
            or "credentials" in Path(relative).name.casefold()
        )
    ]
    if forbidden_tracked:
        raise VerificationError(
            f"forbidden_release_artifacts_tracked:{','.join(forbidden_tracked)}"
        )
    present_secret_env = sorted(key for key in SENSITIVE_ENV_KEYS if key in os.environ)
    if present_secret_env:
        raise VerificationError(
            "credential_environment_present:" + ",".join(present_secret_env)
        )
    model_provider = os.environ.get("FORECASTLAB_MODEL_PROVIDER", "mock")
    search_provider = os.environ.get("FORECASTLAB_SEARCH_PROVIDER", "mock")
    if model_provider != "mock" or search_provider != "mock":
        raise VerificationError("live_provider_configuration_refused")
    if "FORECASTLAB_CREDENTIALS_PATH" in os.environ:
        raise VerificationError("external_credentials_path_refused")
    if "FORECASTLAB_DATABASE_URL" in os.environ or "FORECASTLAB_DATA_DIR" in os.environ:
        raise VerificationError("external_database_configuration_refused")
    return {
        "branch": _git(source, "branch", "--show-current"),
        "commit_sha": actual_sha,
        "tree_sha": _git(source, "rev-parse", "HEAD^{tree}"),
        "commit_timestamp": _git(source, "show", "-s", "--format=%cI", "HEAD"),
        "tracked_files": tracked,
        "prompt_files": prompt_files,
        "migration_files": migration_files,
        "profile_files": profile_files,
    }


def _source_identities(source: Path, preflight: dict[str, Any]) -> dict[str, Any]:
    tracked = preflight["tracked_files"]
    version_file = source / "packages" / "forecasting" / "forecastlab" / "version.py"
    version_match = re.search(
        r"__version__\s*=\s*[\"']([^\"']+)",
        version_file.read_text(encoding="utf-8"),
    )
    if version_match is None:
        raise VerificationError("application_version_unavailable")
    lock_paths = ["uv.lock", "apps/web/package-lock.json", "pyproject.toml"]
    profile_paths = list(preflight["profile_files"])
    prompt_paths = list(preflight["prompt_files"])
    migration_paths = list(preflight["migration_files"])
    return {
        "application_version": version_match.group(1),
        "tracked_source_sha256": _tracked_source_hash(source, tracked),
        "locks": {
            "aggregate_sha256": _aggregate_hash(source, lock_paths),
            "files": _file_hashes(source, lock_paths),
        },
        "profiles": {
            "aggregate_sha256": _aggregate_hash(source, profile_paths),
            "files": _file_hashes(source, profile_paths),
        },
        "prompts": {
            "aggregate_sha256": _aggregate_hash(source, prompt_paths),
            "files": _file_hashes(source, prompt_paths),
        },
        "migrations": {
            "aggregate_sha256": _aggregate_hash(source, migration_paths),
            "files": _file_hashes(source, migration_paths),
        },
    }


def normalize_release_result(value: Any) -> Any:
    uuid_map: dict[str, str] = {}

    def normalize(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(key): normalize(item[key]) for key in sorted(item)}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if not isinstance(item, str):
            return item

        def replace_uuid(match: re.Match[str]) -> str:
            raw = match.group(0).lower()
            if raw not in uuid_map:
                uuid_map[raw] = f"<uuid:{len(uuid_map) + 1:04d}>"
            return uuid_map[raw]

        normalized = UUID_PATTERN.sub(replace_uuid, item)
        normalized = TIMESTAMP_PATTERN.sub("<timestamp>", normalized)
        return normalized

    return normalize(value)


def normalized_result_hash(value: Any) -> str:
    return _sha256_bytes(_canonical_json(normalize_release_result(value)).encode("utf-8"))


def _substantive_checkout_result(result: dict[str, Any]) -> dict[str, Any]:
    # checkout_index identifies the isolated harness instance; it is retained
    # in the manifest receipt but is not an application or workflow result.
    return {key: value for key, value in result.items() if key != "checkout_index"}


def substantive_differences(left: Any, right: Any, path: str = "$") -> list[str]:
    if type(left) is not type(right):
        return [f"{path}:type"]
    if isinstance(left, dict):
        differences: list[str] = []
        if set(left) != set(right):
            differences.append(f"{path}:keys")
        for key in sorted(set(left) & set(right)):
            differences.extend(substantive_differences(left[key], right[key], f"{path}.{key}"))
        return differences
    if isinstance(left, list):
        if len(left) != len(right):
            return [f"{path}:length"]
        differences = []
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            differences.extend(
                substantive_differences(left_item, right_item, f"{path}[{index}]")
            )
        return differences
    return [] if left == right else [path]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def _wait_url(url: str, *, timeout_seconds: float = 60.0) -> bytes:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310 - loopback URL is constructed internally
                if 200 <= response.status < 300:
                    return response.read()
        except (OSError, URLError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise VerificationError(f"service_readiness_timeout:{type(last_error).__name__}")


def _wait_worker_fresh(url: str, *, timeout_seconds: float = 90.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_state = "unavailable"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310 - loopback URL is constructed internally
                if 200 <= response.status < 300:
                    payload = json.loads(response.read().decode("utf-8"))
                    if isinstance(payload, dict) and payload.get("fresh") is True:
                        return payload
                    last_state = "not_fresh"
        except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError):
            last_state = "unavailable"
        time.sleep(0.25)
    raise VerificationError(f"isolated_worker_not_fresh:{last_state}")


def _url_is_available(url: str) -> bool:
    try:
        with urlopen(url, timeout=0.5) as response:  # noqa: S310 - loopback URL is constructed internally
            return 200 <= response.status < 500
    except (OSError, URLError):
        return False


def _start_process(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> tuple[subprocess.Popen[str], Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return process, log_handle


def _stop_processes(processes: list[tuple[subprocess.Popen[str], Any]]) -> None:
    for process, _handle in reversed(processes):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 10
    for process, _handle in reversed(processes):
        remaining = max(0.1, deadline - time.monotonic())
        if process.poll() is None:
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
    for _process, handle in processes:
        handle.close()


def _runtime_database_state(database_path: Path) -> dict[str, Any]:
    with sqlite3.connect(database_path) as connection:
        active_jobs = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('pending', 'running')"
        ).fetchone()[0]
        nonterminal_ledgers = connection.execute(
            "SELECT COUNT(*) FROM provider_call_ledger "
            "WHERE status NOT IN ('succeeded', 'failed', 'released')"
        ).fetchone()[0]
        nonterminal_attempts = connection.execute(
            "SELECT COUNT(*) FROM forecast_run_attempts "
            "WHERE status NOT IN ('completed', 'failed')"
        ).fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    return {
        "active_job_count": active_jobs,
        "nonterminal_ledger_count": nonterminal_ledgers,
        "nonterminal_attempt_count": nonterminal_attempts,
        "integrity_check": integrity,
        "foreign_key_violation_count": len(foreign_keys),
    }


def _scan_text(label: str, text: str) -> list[str]:
    matches: list[str] = []
    for index, pattern in enumerate(SECRET_PATTERNS, start=1):
        if pattern.search(text):
            matches.append(f"{label}:secret_pattern_{index}")
    return matches


def _scan_generated_json(label: str, value: Any, path: str = "$") -> list[str]:
    matches: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).casefold()
            if key_text in RAW_OUTPUT_KEYS:
                matches.append(f"{label}:{path}.{key}:forbidden_key")
            matches.extend(_scan_generated_json(label, child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            matches.extend(_scan_generated_json(label, child, f"{path}[{index}]"))
    elif isinstance(value, str):
        matches.extend(_scan_text(f"{label}:{path}", value))
    return matches


def _scan_tracked_source(source: Path, tracked: list[str]) -> dict[str, Any]:
    matches: list[str] = []
    known_fixture_matches: list[str] = []
    fixture_match_counts: dict[tuple[str, int, str], int] = {}
    scanned = 0
    for relative in tracked:
        content = (source / relative).read_bytes()
        if b"\0" in content:
            continue
        scanned += 1
        text = content.decode("utf-8", errors="ignore")
        for pattern_index, pattern in enumerate(SECRET_PATTERNS, start=1):
            for match in pattern.finditer(text):
                match_digest = _sha256_bytes(match.group(0).encode("utf-8"))
                fixture_key = (relative, pattern_index, match_digest)
                observed = fixture_match_counts.get(fixture_key, 0) + 1
                fixture_match_counts[fixture_key] = observed
                allowed = KNOWN_TRACKED_FIXTURE_SECRET_MATCHES.get(fixture_key, 0)
                location = f"{relative}:secret_pattern_{pattern_index}"
                if observed <= allowed:
                    known_fixture_matches.append(location)
                else:
                    matches.append(location)
    return {
        "tracked_text_file_count": scanned,
        "suspected_secret_count": len(matches),
        "suspected_secret_locations": sorted(matches),
        "known_synthetic_fixture_match_count": len(known_fixture_matches),
        "known_synthetic_fixture_locations": sorted(known_fixture_matches),
    }


def _parse_audit(audit_output: str) -> dict[str, Any]:
    try:
        payload = json.loads(audit_output)
    except json.JSONDecodeError as exc:
        raise VerificationError("npm_audit_output_invalid") from exc
    metadata = (payload.get("metadata") or {}).get("vulnerabilities") or {}
    return {
        "total": int(metadata.get("total") or 0),
        "high": int(metadata.get("high") or 0),
        "critical": int(metadata.get("critical") or 0),
        "package_names": sorted((payload.get("vulnerabilities") or {}).keys()),
    }


def _checkout_environment(runtime: Path, database_path: Path, api_port: int, web_port: int) -> dict[str, str]:
    home = runtime / "home"
    for path in (
        home,
        runtime / "xdg-cache",
        runtime / "uv-cache",
        runtime / "npm-cache",
        runtime / "data" / "local",
    ):
        path.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "HOME": str(home),
        "XDG_CACHE_HOME": str(runtime / "xdg-cache"),
        "UV_CACHE_DIR": str(runtime / "uv-cache"),
        "npm_config_cache": str(runtime / "npm-cache"),
        "PYTHONUNBUFFERED": "1",
        "FORECASTLAB_ENV": "release-verification",
        "FORECASTLAB_DATABASE_URL": f"sqlite:///{database_path}",
        "FORECASTLAB_DATA_DIR": str(runtime / "data"),
        "FORECASTLAB_CREDENTIALS_PATH": str(runtime / "data" / "local" / "credentials.json"),
        "FORECASTLAB_MODEL_PROVIDER": "mock",
        "FORECASTLAB_MODEL_NAME": "mock-forecast-v1",
        "FORECASTLAB_SEARCH_PROVIDER": "mock",
        "FORECASTLAB_ALLOW_LOCAL_FIXTURES": "true",
        "FORECASTLAB_EMBEDDED_WORKER": "false",
        "FORECASTLAB_API_PORT": str(api_port),
        "FORECASTLAB_WEB_ORIGIN": f"http://127.0.0.1:{web_port}",
        "FORECASTLAB_API_ORIGIN": f"http://127.0.0.1:{api_port}",
        "PLAYWRIGHT_BASE_URL": f"http://127.0.0.1:{web_port}",
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    return env


def _run_checkout(
    *,
    source: Path,
    source_sha: str,
    checkout_index: int,
    temp_root: Path,
) -> dict[str, Any]:
    checkout = temp_root / f"checkout-{checkout_index}"
    runtime = temp_root / f"runtime-{checkout_index}"
    logs = runtime / "logs"
    journey_database = runtime / "databases" / "journeys.db"
    fresh_database = runtime / "databases" / "fresh.db"
    legacy_database = runtime / "databases" / "legacy.db"
    database_output = runtime / "database-gate.json"
    journey_output = runtime / "journeys.json"
    api_port = _free_port()
    web_port = _free_port()
    env = _checkout_environment(runtime, journey_database, api_port, web_port)
    processes: list[tuple[subprocess.Popen[str], Any]] = []
    try:
        _run(
            ["git", "clone", "--no-local", "--quiet", str(source), str(checkout)],
            cwd=temp_root,
            timeout=300,
            log_path=logs / "clone.log",
        )
        _run(
            ["git", "checkout", "--detach", source_sha],
            cwd=checkout,
            timeout=120,
            log_path=logs / "checkout.log",
        )
        if _git(checkout, "rev-parse", "HEAD") != source_sha:
            raise VerificationError("isolated_checkout_sha_mismatch")
        if _git(checkout, "status", "--porcelain", "--untracked-files=no"):
            raise VerificationError("isolated_checkout_started_dirty")

        _run(
            ["uv", "sync", "--extra", "dev", "--frozen"],
            cwd=checkout,
            env=env,
            timeout=1200,
            log_path=logs / "uv-sync.log",
        )
        _run(
            ["npm", "ci"],
            cwd=checkout / "apps" / "web",
            env=env,
            timeout=1200,
            log_path=logs / "npm-ci.log",
        )
        npm_audit = _run(
            ["npm", "audit", "--json"],
            cwd=checkout / "apps" / "web",
            env=env,
            timeout=300,
            accepted_returncodes={0, 1},
        )
        frontend_advisories = _parse_audit(npm_audit.stdout)
        if frontend_advisories != {
            "total": 3,
            "high": 3,
            "critical": 0,
            "package_names": ["next", "postcss", "sharp"],
        }:
            raise VerificationError("frontend_advisory_set_drifted")

        _run(
            [
                "uv",
                "run",
                "--frozen",
                "--no-sync",
                "python",
                "-m",
                "forecastlab_api.private_v1_release_verification",
                "--source-sha",
                source_sha,
                "--output",
                str(database_output),
                "--mode",
                "database-gate",
                "--fresh-database",
                str(fresh_database),
                "--legacy-database",
                str(legacy_database),
            ],
            cwd=checkout,
            env=env,
            timeout=600,
            log_path=logs / "database-gate.log",
        )
        database_result = json.loads(database_output.read_text(encoding="utf-8"))

        _run(
            [
                "uv",
                "run",
                "--frozen",
                "--no-sync",
                "python",
                "-c",
                "from forecastlab_api.migrate import apply_schema; apply_schema()",
            ],
            cwd=checkout,
            env=env,
            timeout=600,
            log_path=logs / "journey-migrate.log",
        )
        _run(
            [
                "uv",
                "run",
                "--frozen",
                "--no-sync",
                "python",
                "-m",
                "forecastlab_api.private_v1_release_verification",
                "--source-sha",
                source_sha,
                "--output",
                str(journey_output),
            ],
            cwd=checkout,
            env=env,
            timeout=600,
            log_path=logs / "journeys.log",
        )
        journey_result = json.loads(journey_output.read_text(encoding="utf-8"))

        _run(
            ["npm", "run", "typecheck"],
            cwd=checkout / "apps" / "web",
            env=env,
            timeout=600,
            log_path=logs / "frontend-typecheck.log",
        )
        _run(
            ["npm", "run", "build"],
            cwd=checkout / "apps" / "web",
            env=env,
            timeout=1200,
            log_path=logs / "frontend-build.log",
        )
        _run(
            ["npx", "playwright", "install", "chromium"],
            cwd=checkout / "apps" / "web",
            env=env,
            timeout=1200,
            log_path=logs / "playwright-install.log",
        )

        api_process = _start_process(
            [
                "uv",
                "run",
                "--frozen",
                "--no-sync",
                "uvicorn",
                "forecastlab_api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            cwd=checkout,
            env=env,
            log_path=logs / "api.log",
        )
        processes.append(api_process)
        _wait_url(f"http://127.0.0.1:{api_port}/health")
        worker_process = _start_process(
            [
                "uv",
                "run",
                "--frozen",
                "--no-sync",
                "python",
                "-m",
                "forecastlab_api.worker",
            ],
            cwd=checkout,
            env=env,
            log_path=logs / "worker.log",
        )
        processes.append(worker_process)
        _wait_worker_fresh(f"http://127.0.0.1:{api_port}/health/worker")
        web_process = _start_process(
            [
                str(checkout / "apps" / "web" / "node_modules" / ".bin" / "next"),
                "start",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(web_port),
            ],
            cwd=checkout / "apps" / "web",
            env=env,
            log_path=logs / "web.log",
        )
        processes.append(web_process)
        _wait_url(f"http://127.0.0.1:{web_port}")

        positive_id = journey_result["positive_journey"]["question_id"]
        negative_id = journey_result["negative_journey"]["question_id"]
        positive_api = json.loads(
            _wait_url(
                f"http://127.0.0.1:{api_port}/api/forecasts/{positive_id}/graph-report"
            ).decode("utf-8")
        )
        negative_api = json.loads(
            _wait_url(
                f"http://127.0.0.1:{api_port}/api/forecasts/{negative_id}/graph-report"
            ).decode("utf-8")
        )
        browser_env = {
            **env,
            "PRIVATE_V1_POSITIVE_QUESTION_ID": positive_id,
            "PRIVATE_V1_NEGATIVE_QUESTION_ID": negative_id,
        }
        _run(
            [
                "npx",
                "playwright",
                "test",
                "e2e/private-v1-release-verification.spec.ts",
                "--workers=1",
                "--reporter=line",
            ],
            cwd=checkout / "apps" / "web",
            env=browser_env,
            timeout=600,
            log_path=logs / "playwright.log",
        )

        database_terminal = _runtime_database_state(journey_database)
        expected_terminal = {
            "active_job_count": 0,
            "nonterminal_ledger_count": 0,
            "nonterminal_attempt_count": 0,
            "integrity_check": "ok",
            "foreign_key_violation_count": 0,
        }
        if database_terminal != expected_terminal:
            raise VerificationError("isolated_runtime_not_terminal")
        if positive_api.get("status") != "completed":
            raise VerificationError("positive_report_api_not_completed")
        if positive_api.get("final_probability") is None:
            raise VerificationError("positive_report_api_probability_missing")
        if negative_api.get("status") != "failed":
            raise VerificationError("negative_report_api_not_failed")
        if negative_api.get("final_probability") is not None:
            raise VerificationError("negative_report_api_probability_present")

        generated_scan = [
            *_scan_generated_json("journeys", journey_result),
            *_scan_generated_json("database", database_result),
            *_scan_generated_json("positive_api", positive_api),
            *_scan_generated_json("negative_api", negative_api),
        ]
        for log_path in sorted(logs.glob("*.log")):
            generated_scan.extend(
                _scan_text(
                    f"log:{log_path.name}",
                    log_path.read_text(encoding="utf-8", errors="ignore"),
                )
            )
        if generated_scan:
            raise VerificationError(
                f"isolated_output_security_scan_failed:{len(generated_scan)}"
            )
        if _git(checkout, "status", "--porcelain", "--untracked-files=no"):
            raise VerificationError("isolated_checkout_tracked_mutation")

        result = {
            "checkout_index": checkout_index,
            "source_sha": source_sha,
            "tree_sha": _git(checkout, "rev-parse", "HEAD^{tree}"),
            "database": database_result,
            "journeys": journey_result,
            "frontend": {
                "frozen_install": "passed",
                "typecheck": "passed",
                "production_build": "passed",
                "browser": "passed",
                "browser_workers": 1,
                "positive_surface_status": positive_api.get("status"),
                "positive_surface_probability_present": (
                    positive_api.get("final_probability") is not None
                ),
                "negative_surface_status": negative_api.get("status"),
                "negative_surface_probability": negative_api.get("final_probability"),
                "known_advisories": frontend_advisories,
            },
            "security": {
                "generated_secret_match_count": 0,
                "forbidden_generated_key_count": 0,
                "live_provider_ledger_count": 0,
                "raw_provider_content_persisted": False,
            },
            "cleanup": database_terminal,
        }
        return result
    finally:
        _stop_processes(processes)
        if _url_is_available(f"http://127.0.0.1:{api_port}/health"):
            raise VerificationError("api_process_cleanup_failed")
        if _url_is_available(f"http://127.0.0.1:{web_port}"):
            raise VerificationError("web_process_cleanup_failed")


def _manifest(
    *,
    preflight: dict[str, Any],
    identities: dict[str, Any],
    checkout_results: list[dict[str, Any]],
    normalized_hash: str,
    security: dict[str, Any],
) -> dict[str, Any]:
    representative = checkout_results[0]
    positive = representative["journeys"]["positive_journey"]
    negative = representative["journeys"]["negative_journey"]
    database = representative["database"]
    frontend = representative["frontend"]
    return {
        "schema_version": 1,
        "candidate_label": CANDIDATE_LABEL,
        "verification_scope": "offline_private_v1_release_candidate",
        "source": {
            "commit_sha": preflight["commit_sha"],
            "tree_sha": preflight["tree_sha"],
            "commit_timestamp": preflight["commit_timestamp"],
            "application_version": identities["application_version"],
            "tracked_source_sha256": identities["tracked_source_sha256"],
        },
        "identities": {
            "locks": identities["locks"],
            "profiles": identities["profiles"],
            "prompts": identities["prompts"],
            "migrations": identities["migrations"],
        },
        "reproducibility": {
            "isolated_checkout_count": 2,
            "true_local_clones": True,
            "separate_home_database_cache_ports_and_stack": True,
            "normalized_fields": ["UUIDs", "timestamps", "temporary paths", "ports"],
            "substantive_difference_count": 0,
            "normalized_result_sha256": normalized_hash,
            "equivalent": True,
        },
        "database": database,
        "positive_journey": {
            "fixture_label": positive["fixture_label"],
            "profile_id": positive["profile_id"],
            "profile_version": positive["profile_version"],
            "status": positive["status"],
            "graph_node_count": positive["graph"]["node_count"],
            "selected_node_count": len(positive["research_plan"]["selected_node_ids"]),
            "evidence_claim_count": positive["evidence"]["claim_count"],
            "node_run_count": len(positive["node_runs"]),
            "evidence_sufficiency_status": positive["evidence_sufficiency"]["status"],
            "material_node_status": positive["material_node_coverage"]["status"],
            "scenario_status": positive["scenario_synthesis"]["status"],
            "scenario_kinds": sorted(
                item["kind"] for item in positive["scenario_synthesis"]["scenarios"]
            ),
            "aggregation_method": positive["aggregation"]["method"],
            "final_probability": positive["final_probability"],
            "scenario_probability_invariance": positive["invariance"],
            "report_markdown_sha256": positive["report_markdown_sha256"],
            "report_markers": positive["report_markdown_markers"],
        },
        "negative_journey": {
            "fixture_label": negative["fixture_label"],
            "profile_id": negative["profile_id"],
            "profile_version": negative["profile_version"],
            "status": negative["status"],
            "error_stage": negative["error_stage"],
            "failure_codes": sorted(item["error_code"] for item in negative["failures"]),
            "evidence_item_count": negative["evidence"]["item_count"],
            "evidence_claim_count": negative["evidence"]["claim_count"],
            "node_run_count": len(negative["node_runs"]),
            "evidence_sufficiency_status": negative["evidence_sufficiency"]["status"],
            "aggregation_id": negative["aggregation"]["id"],
            "forecast_version_id": negative["forecast_version_id"],
            "final_probability": negative["final_probability"],
            "provider_request_count": len(negative["provider_audit"]["ledger"]),
            "cost_usd": negative["provider_audit"]["cost_usd"],
        },
        "frontend": {
            **frontend,
            "advisory_details": list(KNOWN_FRONTEND_ADVISORIES),
        },
        "security": security,
        "provider_usage": {
            "mode": "mock_only",
            "live_openai_calls": 0,
            "live_tavily_calls": 0,
            "live_provider_ledger_rows": 0,
            "total_cost_usd": 0.0,
        },
        "cleanup": {
            "services_stopped": True,
            "active_jobs": 0,
            "unreconciled_reservations": 0,
            "nonterminal_ledgers": 0,
            "temporary_service_files_preserved": False,
        },
        "preserved_results": {
            "latest_live_operational_acceptance": "FAIL",
            "completed_questions": 3,
            "attempted_questions": 5,
            "forecasting_quality_established": False,
            "calibration_established": False,
        },
        "publication": {
            "tag_created": False,
            "merged": False,
            "release_published": False,
            "deployed": False,
            "authorized": False,
        },
        "limitations": [
            "The latest live operational acceptance remains a hard 3-of-5 failure.",
            "This verifier uses synthetic mock evidence and establishes software reproducibility only.",
            "Forecasting quality and calibration have not been evaluated.",
            "Three known high-severity frontend dependency advisories remain; the available fix is a semver-major Next upgrade.",
            "No tag, publication, merge, or deployment was authorized.",
        ],
    }


def run_verification(*, source: Path, source_sha: str, output_dir: Path) -> dict[str, Any]:
    preflight = _preflight(source, source_sha, output_dir)
    identities = _source_identities(source, preflight)
    tracked_security = _scan_tracked_source(source, preflight["tracked_files"])
    if tracked_security["suspected_secret_count"]:
        raise VerificationError("tracked_source_secret_scan_failed")
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="forecastlab-private-v1-release-"))
    try:
        checkout_results = [
            _run_checkout(
                source=source,
                source_sha=source_sha,
                checkout_index=index,
                temp_root=temp_root,
            )
            for index in (1, 2)
        ]
        normalized = [
            normalize_release_result(_substantive_checkout_result(result))
            for result in checkout_results
        ]
        differences = substantive_differences(normalized[0], normalized[1])
        if differences:
            raise VerificationError(
                f"isolated_checkout_substantive_difference:{differences[0]}"
            )
        normalized_hash = _sha256_bytes(_canonical_json(normalized[0]).encode("utf-8"))
        security = {
            **tracked_security,
            "generated_output_secret_count": 0,
            "forbidden_generated_key_count": 0,
            "committed_environment_file_count": 0,
            "committed_database_file_count": 0,
            "committed_credential_artifact_count": 0,
            "raw_provider_response_count": 0,
        }
        manifest = _manifest(
            preflight=preflight,
            identities=identities,
            checkout_results=checkout_results,
            normalized_hash=normalized_hash,
            security=security,
        )
        manifest_security = _scan_generated_json("manifest", manifest)
        if manifest_security:
            raise VerificationError("release_manifest_security_scan_failed")
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify ForecastLab private V1 from two isolated fresh checkouts."
    )
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    try:
        manifest = run_verification(
            source=source,
            source_sha=args.source_sha,
            output_dir=args.output_dir,
        )
    except (VerificationError, subprocess.TimeoutExpired) as exc:
        print(f"PRIVATE_V1_RELEASE_VERIFICATION_FAILED:{exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "candidate_label": manifest["candidate_label"],
                "source_sha": manifest["source"]["commit_sha"],
                "normalized_result_sha256": manifest["reproducibility"][
                    "normalized_result_sha256"
                ],
                "status": "passed",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
