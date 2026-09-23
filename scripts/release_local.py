"""Owner-operated release fallback when hosted CI minutes are exhausted.

Run from a clean checkout of the exact default-branch commit after the full
local CI suite passes. This reuses the normal backup/stage/smoke/promote path;
only the release-control transport differs from the hosted workflow.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

from scripts.release_cloud import ROOT, configure_release_environment, release_with_clients
from scripts.vercel_release import VercelProject

DEFAULT_BRANCH = "grok/forecastlab-mvp"
REPOSITORY = "https://github.com/nikoamoretti/forecastlab.git"
OWNER_ID = "146488758"
TEAM_ID = "team_bpaUtKwQ9fN7jXrJZlx3V2dN"
API_PROJECT_ID = "prj_m4XcIxSgbxrQ1tpZ5f95DUi5Zbqf"
WEB_PROJECT_ID = "prj_ZVYlk5taMBR9jp9vC68kpCj0Vdad"


def output(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def preflight(approved_commit: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", approved_commit):
        raise RuntimeError("full_approved_commit_required")
    if output("git", "status", "--porcelain"):
        raise RuntimeError("release_tree_not_clean")
    if output("git", "remote", "get-url", "origin") != REPOSITORY:
        raise RuntimeError("release_repository_mismatch")
    if output("git", "rev-parse", "HEAD") != approved_commit:
        raise RuntimeError("release_commit_mismatch")
    remote = output("git", "ls-remote", "origin", "refs/heads/" + DEFAULT_BRANCH).split()[0]
    if remote != approved_commit:
        raise RuntimeError("release_commit_not_default_head")
    if output("gh", "api", "user", "--jq", ".id") != OWNER_ID:
        raise RuntimeError("release_owner_mismatch")
    version = output("pg_dump", "--version")
    match = re.search(r"(\d+)(?:\.\d+)?$", version)
    if not match or int(match.group(1)) < 18:
        raise RuntimeError("postgresql_18_backup_client_required")


def owner_token() -> str:
    path = Path.home() / "Library/Application Support/com.vercel.cli/auth.json"
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise RuntimeError("vercel_owner_token_file_not_private")
    token = json.loads(path.read_text()).get("token")
    if not token:
        raise RuntimeError("vercel_owner_token_missing")
    return token


def database_control(path: str) -> dict:
    from forecastlab_api.autopilot_routes import release_complete, release_pause
    from forecastlab_api.db import SessionLocal

    commands = {"/internal/release/pause": release_pause,
                "/internal/release/complete": release_complete}
    if path not in commands:
        raise RuntimeError("unknown_release_control_command")
    with SessionLocal.begin() as session:
        return commands[path](session)


def validate_cloud_targets(api: VercelProject, web: VercelProject) -> None:
    for client, expected_id, expected_name in (
        (api, API_PROJECT_ID, "forecastlab-api"),
        (web, WEB_PROJECT_ID, "forecastlab-web"),
    ):
        project = client.project()
        if project.get("id") != expected_id or project.get("name") != expected_name:
            raise RuntimeError("release_project_mismatch")
        if not project.get("targets", {}).get("production", {}).get("id"):
            raise RuntimeError("production_target_missing")
        if not any(value.get("scope") == "automation-bypass"
                   for value in project.get("protectionBypass", {}).values()):
            raise RuntimeError("staged_smoke_bypass_missing")


def main() -> None:
    approved_commit = os.environ.get("FORECASTLAB_APPROVED_COMMIT", "")
    preflight(approved_commit)
    token = owner_token()
    api = VercelProject(API_PROJECT_ID, token, TEAM_ID)
    web = VercelProject(WEB_PROJECT_ID, token, TEAM_ID)
    try:
        validate_cloud_targets(api, web)
        # Bind the release-control session to the same production database that
        # the normal release runner backs up and migrates. No credential or
        # application access setting is modified.
        values = api.environment()
        configure_release_environment(values)
        os.environ["FORECASTLAB_DEPLOYMENT_REVISION"] = approved_commit
        release_with_clients(api, web, database_control)
    except Exception:
        api.client.close()
        web.client.close()
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"released": False, "dispatch": "left paused if pause succeeded",
            "category": type(exc).__name__,
            "reason": str(exc) if isinstance(exc, RuntimeError) else "Review local diagnostics without exposing secrets"}),
            flush=True)
        raise SystemExit(1) from None
