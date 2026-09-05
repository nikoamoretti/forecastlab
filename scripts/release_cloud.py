"""Reviewed release entry point. A failed step leaves dispatch paused.

Run only after CI passes, with the owner's approval of the exact release commit.
Provider and database secrets are pulled into a temporary private file and are
never logged. No migrations run inside request handlers or application startup.
"""
from __future__ import annotations

import gzip
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
SCOPE = "yard-logix"


def cli(arguments, *, cwd=ROOT):
    token_name = "FORECASTLAB_WEB_VERCEL_TOKEN" if cwd == ROOT / "apps/web" else "FORECASTLAB_API_VERCEL_TOKEN"
    token = os.environ.get(token_name) or os.environ.get("VERCEL_TOKEN")
    command = ["vercel", "--scope", SCOPE]
    if token:
        command += ["--token", token]
    command += arguments
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode:
        # Build diagnostics remain available in Vercel; avoid echoing arguments,
        # environment values or errors containing secret-bearing URLs.
        raise RuntimeError("vercel_command_failed:" + arguments[0])
    return result.stdout.strip()


def request(path, *, method="POST"):
    response = httpx.request(method, os.environ["FORECASTLAB_RELEASE_API"] + path,
        headers={"authorization": "Bearer " + os.environ["FORECASTLAB_RELEASE_SECRET"]}, timeout=40)
    if response.status_code != 200:
        raise RuntimeError(f"release_endpoint_failed:{path}:{response.status_code}")
    return response.json()


def link(cwd, project_id):
    directory = cwd / ".vercel"
    directory.mkdir(exist_ok=True)
    (directory / "project.json").write_text(json.dumps({"orgId": os.environ["VERCEL_ORG_ID"], "projectId": project_id}))


def stage(cwd):
    output = cli(["deploy", "--prod", "--skip-domain", "--yes", "--format", "json"], cwd=cwd)
    receipt = json.loads(output)
    url = receipt.get("deployment", {}).get("url", "")
    if receipt.get("deployment", {}).get("readyState") != "READY" or not url.startswith("https://") or not url.endswith(".vercel.app"):
        raise RuntimeError("deployment_receipt_missing")
    return url


def smoke(url, path, expected, cwd):
    output = cli(["curl", path, "--deployment", url, "--", "--silent", "--output", "/dev/null", "--write-out", "%{http_code}"], cwd=cwd)
    if output.strip() != str(expected):
        raise RuntimeError(f"staged_smoke_failed:{path}")


def configure_release_environment(values):
    # Vercel exports sensitive provider settings as [SENSITIVE]. A release
    # needs only the integration's database and Blob credentials; importing
    # every value would feed redacted booleans/numbers into application config.
    for key in ("DATABASE_URL_UNPOOLED", "BLOB_READ_WRITE_TOKEN"):
        if not values.get(key) or values[key] == "[SENSITIVE]":
            raise RuntimeError("release_credential_unavailable:" + key)
    url = make_url(values["DATABASE_URL_UNPOOLED"]).set(drivername="postgresql+psycopg")
    os.environ.update({
        "FORECASTLAB_ENV": "production", "FORECASTLAB_ALLOW_LOCAL_FIXTURES": "false",
        "FORECASTLAB_EMBEDDED_WORKER": "false", "FORECASTLAB_BLOB_TOKEN": values["BLOB_READ_WRITE_TOKEN"],
        "FORECASTLAB_DATABASE_URL": url.render_as_string(hide_password=False),
    })
    return url


def main():
    if not os.environ.get("FORECASTLAB_RELEASE_SECRET"):
        raise RuntimeError("release_secret_required")
    print("Pausing dispatch and draining active work.", flush=True)
    deadline = time.monotonic() + 720
    while not request("/internal/release/pause")["drained"]:
        if time.monotonic() >= deadline:
            raise RuntimeError("release_drain_timeout")
        time.sleep(15)
    link(ROOT, os.environ["FORECASTLAB_API_PROJECT_ID"])
    link(ROOT / "apps/web", os.environ["FORECASTLAB_WEB_PROJECT_ID"])
    with tempfile.TemporaryDirectory(prefix="forecastlab-release-") as temp:
        environment = Path(temp) / "production.env"
        cli(["env", "pull", str(environment), "--environment", "production", "--yes"])
        environment.chmod(0o600)
        values = dotenv_values(environment)
        url = configure_release_environment(values)
        # pg_dump captures the OLD schema before importing the new application's
        # metadata, so a migration adding tables cannot break the backup.
        pg_env = {**os.environ, "PGHOST": url.host or "", "PGPORT": str(url.port or 5432),
            "PGUSER": url.username or "", "PGPASSWORD": url.password or "", "PGDATABASE": url.database or "", "PGSSLMODE": "require"}
        dumped = subprocess.run(["pg_dump", "--no-owner", "--no-privileges"], env=pg_env, capture_output=True)
        if dumped.returncode:
            raise RuntimeError("pre_release_snapshot_failed")
        from forecastlab_api.artifact_store import get_bytes, put_bytes
        from forecastlab_api.migrate import apply_migrations
        archive = put_bytes(gzip.compress(dumped.stdout), prefix="release-backups", content_type="application/gzip")
        if gzip.decompress(get_bytes(archive)) != dumped.stdout:
            raise RuntimeError("pre_release_backup_verification_failed")
        print("Private snapshot verified; applying additive migrations.", flush=True)
        apply_migrations(url.render_as_string(hide_password=False))
        engine = create_engine(url)
        from sqlalchemy import text
        with engine.connect() as connection:
            if connection.execute(text("SELECT enabled FROM autopilot_state WHERE id='personal'")).scalar():
                raise RuntimeError("release_pause_lost")
        engine.dispose()
        api = stage(ROOT)
        web = stage(ROOT / "apps/web")
        smoke(api, "/health", 200, ROOT)
        smoke(api, "/api/settings", 401, ROOT)
        smoke(web, "/login", 200, ROOT / "apps/web")
        smoke(web, "/api/settings", 401, ROOT / "apps/web")
        print("Staged checks passed; promoting API and web.", flush=True)
        cli(["promote", api, "--yes"])
        cli(["promote", web, "--yes"], cwd=ROOT / "apps/web")
        result = request("/internal/release/complete")
        print(json.dumps({"released": True, "api": api, "web": web, **result}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"released": False, "dispatch": "left paused if pause succeeded", "category": type(exc).__name__,
            "reason": str(exc) if isinstance(exc, RuntimeError) else "Review release diagnostics without exposing secrets"}))
        raise SystemExit(1) from None
