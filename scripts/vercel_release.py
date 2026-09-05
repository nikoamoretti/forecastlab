"""Project-scoped Vercel REST operations, avoiding the CLI's account lookup."""
from __future__ import annotations

import base64
import io
import json
import subprocess
import tarfile
import time
from pathlib import Path, PurePosixPath

import httpx


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
            files.append({"file": member.name.removeprefix("apps/web/") if web else member.name,
                "data": base64.b64encode(stream.read()).decode(), "encoding": "base64"})
    if not files or sum(len(f["data"]) for f in files) > 20_000_000:
        raise RuntimeError("release_bundle_size_requires_review")
    return files


class VercelProject:
    def __init__(self, project_id: str, token: str, team_id: str):
        self.project_id, self.team_id = project_id, team_id
        self.client = httpx.Client(base_url="https://api.vercel.com", timeout=90,
            headers={"Authorization": "Bearer " + token})

    def request(self, path, *, method="GET", body=None, params=None):
        response = self.client.request(method, path, params={"teamId": self.team_id, **(params or {})}, json=body)
        if response.status_code >= 300:
            raise RuntimeError(f"vercel_api_failed:{method}:{path}:{response.status_code}")
        return response.json() if response.content else {}

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

    def stage(self, root: Path, *, web: bool):
        project = self.project()
        previous = project.get("targets", {}).get("production", {}).get("id")
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        files = source_files(root, web=web)
        config = json.loads(base64.b64decode(next(f["data"] for f in files if f["file"] == "vercel.json")))
        deployment = self.request("/v13/deployments", method="POST", body={
            "name": project["name"], "project": self.project_id, "target": "production",
            "autoAssignCustomDomains": False, "files": files,
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
