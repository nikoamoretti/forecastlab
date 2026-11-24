"""Project-scoped Vercel REST operations, avoiding the CLI's account lookup."""
from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tarfile
import time
from pathlib import Path, PurePosixPath

import httpx

# Most committed source one application may ship, in bytes. It is a tripwire for something large
# committed by mistake, far under Vercel's 250 MB function limit. The API bundle carries data/, which
# grows by about 0.3 MB every forecast day (8 MB on 2026-11-24).
BUNDLE_LIMIT = 50_000_000


def included(name: str, *, web: bool) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or any(p.startswith(".env") for p in path.parts):
        return False
    if web:
        return name.startswith("apps/web/") and not name.startswith("apps/web/e2e/")
    if name.startswith(("data/local/", "apps/web/")) or ".db" in path.name or path.suffix == ".sqlite":
        return False
    return name in {"app.py", "vercel.json", "pyproject.toml", "uv.lock", "README.md", "alembic.ini"} or name.startswith(
        ("apps/api/", "packages/", "configs/", "prompts/", "alembic/", "data/"))


def source_files(root: Path, *, web: bool) -> list[dict]:
    """The committed files one application ships, as {"file": its path, "data": its bytes}."""
    # Read the committed tree, never ignored local files or uncommitted changes.
    archive = subprocess.run(["git", "archive", "HEAD"], cwd=root, capture_output=True, check=True).stdout
    files = []
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            if not included(member.name, web=web) or member.isdir():
                continue
            if not member.isfile():
                raise RuntimeError("unsupported_release_file_type")
            stream = tar.extractfile(member)
            if stream is None:
                raise RuntimeError("release_file_missing")
            files.append({"file": member.name.removeprefix("apps/web/") if web else member.name, "data": stream.read()})
    if not files or sum(len(f["data"]) for f in files) > BUNDLE_LIMIT:
        raise RuntimeError("release_bundle_size_requires_review")
    return files


def failure(response: httpx.Response, *, detail: bool = True) -> str:
    """A refused call's status with Vercel's own error code and the start of its message, so the log says why."""
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        error = {}
    if not isinstance(error, dict):
        error = {}
    code = re.sub(r"[^A-Za-z0-9_.-]", "", str(error.get("code", "")))[:60]
    message = re.sub(r"[^A-Za-z0-9 _.,()/-]", "", str(error.get("message", "")))[:160] if detail else ""
    return ":".join(part for part in (str(response.status_code), code, message) if part)


class VercelProject:
    def __init__(self, project_id: str, token: str, team_id: str):
        self.project_id, self.team_id = project_id, team_id
        self.client = httpx.Client(base_url="https://api.vercel.com", timeout=90,
            headers={"Authorization": "Bearer " + token})

    def request(self, path, *, method="GET", body=None, params=None):
        response = self.client.request(method, path, params={"teamId": self.team_id, **(params or {})}, json=body)
        if response.status_code >= 300:
            # An environment call never repeats Vercel's message: only its status and error code are logged.
            raise RuntimeError(f"vercel_api_failed:{method}:{path}:{failure(response, detail='/env' not in path)}")
        return response.json() if response.content else {}

    def upload(self, data: bytes, digest: str, *, wait: float = 2.0) -> None:
        """Put one file in Vercel's file store under its SHA-1; sending a file it already holds is harmless."""
        reason = "no_response"
        for attempt in range(3):
            try:
                # x-now-digest and x-now-size are the older names of the same two values, which Vercel's own
                # command line still sends; both spellings go out so either one is understood.
                response = self.client.post("/v2/files", params={"teamId": self.team_id}, content=data, headers={
                    "Content-Type": "application/octet-stream", "Content-Length": str(len(data)),
                    "x-vercel-digest": digest, "x-now-digest": digest, "x-now-size": str(len(data))})
            except httpx.TransportError:
                time.sleep(wait * (attempt + 1))
                continue
            if response.status_code < 300:
                return
            reason = failure(response)
            if response.status_code < 500 and response.status_code != 429:
                break  # a refusal, not a hiccup: trying again would get the same answer
            time.sleep(wait * (attempt + 1))
        raise RuntimeError("vercel_api_failed:POST:/v2/files:" + reason)

    def prepare(self, root: Path, *, web: bool) -> dict:
        """Read one application's committed files and upload them. Nothing here touches production.

        A deployment names its files by digest. Their contents used to travel inside the
        create-deployment call, which Vercel answered with a 400 on 2026-11-24, the day the API bundle
        took that call past 10 MB; its documentation allows only small files inline.
        """
        files = source_files(root, web=web)
        config = json.loads(next(f["data"] for f in files if f["file"] == "vercel.json"))
        listed, sent = [], set()
        for entry in files:
            digest = hashlib.sha1(entry["data"], usedforsecurity=False).hexdigest()
            if digest not in sent:
                self.upload(entry["data"], digest)
                sent.add(digest)
            listed.append({"file": entry["file"], "sha": digest, "size": len(entry["data"])})
        print(json.dumps({"bundle": "web" if web else "api", "files": len(listed), "uploads": len(sent),
            "bytes": sum(f["size"] for f in listed)}), flush=True)
        return {"files": listed, "config": config}

    def project(self):
        return self.request("/v9/projects/" + self.project_id)

    def environment(self):
        response = self.request("/v10/projects/" + self.project_id + "/env")
        values = {}
        for variable in response["envs"]:
            if variable["key"] not in {"DATABASE_URL_UNPOOLED", "BLOB_READ_WRITE_TOKEN"} or "production" not in variable.get("target", []):
                continue
            decrypted = self.request(f"/v1/projects/{self.project_id}/env/{variable['id']}")
            if not decrypted.get("decrypted"):
                raise RuntimeError("release_credential_unavailable:" + variable["key"])
            values[variable["key"]] = decrypted["value"]
        return values

    def stage(self, root: Path, bundle: dict, *, web: bool):
        """Build a deployment from a bundle `prepare` uploaded, without moving the production target."""
        project = self.project()
        previous = project.get("targets", {}).get("production", {}).get("id")
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        config = bundle["config"]
        deployment = self.request("/v13/deployments", method="POST", body={
            "name": project["name"], "project": self.project_id, "target": "production",
            "autoAssignCustomDomains": False, "files": bundle["files"],
            "regions": config.get("regions", ["iad1"]), **({"functions": config["functions"]} if "functions" in config else {}),
            "projectSettings": {"framework": "nextjs" if web else "fastapi", "nodeVersion": "24.x"},
            "gitMetadata": {"remoteUrl": "https://github.com/nikoamoretti/forecastlab", "commitSha": commit, "dirty": False},
            "meta": {"githubCommitSha": commit},
        })
        deadline, last = time.monotonic() + 900, None
        while time.monotonic() < deadline:
            status = deployment.get("readyState") or deployment.get("status")
            if status != last:
                print(json.dumps({"project": project["name"], "deployment": deployment["id"], "status": status}), flush=True)
                last = status
            if status == "READY":
                # Staging must not move the public production target.
                if self.project().get("targets", {}).get("production", {}).get("id") != previous:
                    raise RuntimeError("staged_deployment_changed_production_target")
                return deployment
            if status in {"ERROR", "CANCELED"}:
                raise RuntimeError("vercel_build_failed:" + str(deployment.get("errorCode", status)))
            time.sleep(5)
            deployment = self.request("/v13/deployments/" + deployment["id"])
        raise RuntimeError("vercel_build_timeout")

    def smoke(self, deployment, path, expected):
        url = deployment["url"]
        url = url if url.startswith("https://") else "https://" + url
        if not url.endswith(".vercel.app"):
            raise RuntimeError("invalid_deployment_url")
        bypasses = self.project().get("protectionBypass", {})
        key = next((k for k, v in bypasses.items() if v.get("scope") == "automation-bypass"), None)
        if not key:
            raise RuntimeError("deployment_protection_automation_secret_required")
        response = httpx.get(url + path, headers={"x-vercel-protection-bypass": key}, timeout=60)
        if response.status_code != expected:
            raise RuntimeError(f"staged_smoke_failed:{path}:{response.status_code}")

    def promote(self, deployment):
        self.request(f"/v10/projects/{self.project_id}/promote/{deployment['id']}", method="POST", body={})
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.project().get("targets", {}).get("production", {}).get("id") == deployment["id"]:
                return
            time.sleep(3)
        raise RuntimeError("promotion_not_confirmed")
