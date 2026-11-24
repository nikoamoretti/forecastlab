import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest
from scripts.release_cloud import configure_release_environment


def test_release_ignores_redacted_provider_settings(monkeypatch):
    monkeypatch.setattr(os, "environ", {})
    url = configure_release_environment({
        "DATABASE_URL_UNPOOLED": "postgresql://test:password@localhost/release",
        "BLOB_READ_WRITE_TOKEN": "test-blob-token",
        "FORECASTLAB_MODEL_API_KEY": "[SENSITIVE]",
        "FORECASTLAB_ALLOW_LOCAL_FIXTURES": "[SENSITIVE]",
        "FORECASTLAB_MAX_COST_USD": "[SENSITIVE]",
    })
    assert url.drivername == "postgresql+psycopg"
    assert os.environ["FORECASTLAB_ALLOW_LOCAL_FIXTURES"] == "false"
    assert os.environ["FORECASTLAB_EMBEDDED_WORKER"] == "false"
    assert "FORECASTLAB_MODEL_API_KEY" not in os.environ
    assert "FORECASTLAB_MAX_COST_USD" not in os.environ


@pytest.mark.parametrize("missing", ["DATABASE_URL_UNPOOLED", "BLOB_READ_WRITE_TOKEN"])
def test_release_requires_readable_backup_credentials(monkeypatch, missing):
    monkeypatch.setattr(os, "environ", {})
    values = {"DATABASE_URL_UNPOOLED": "postgresql://test:password@localhost/release", "BLOB_READ_WRITE_TOKEN": "test"}
    values[missing] = "[SENSITIVE]"
    with pytest.raises(RuntimeError, match="release_credential_unavailable:" + missing):
        configure_release_environment(values)
    assert not os.environ


@pytest.mark.parametrize("name", [".env", "apps/api/.env.production", "data/local/credentials.json", "data/forecastlab.db", "tests/test_api.py"])
def test_api_release_excludes_private_and_test_files(name):
    from scripts.vercel_release import included
    assert not included(name, web=False)


def test_web_release_isolates_repository_root_and_secrets():
    from scripts.vercel_release import included
    assert included("apps/web/app/page.tsx", web=True)
    assert not included("apps/api/forecastlab_api/main.py", web=True)
    assert not included("apps/web/.env.local", web=True)
    assert not included("apps/web/e2e/autopilot.spec.ts", web=True)


def test_rest_environment_filters_out_preview_values(monkeypatch):
    from scripts.vercel_release import VercelProject
    project = VercelProject("project", "token", "team")
    def request(path, **_):
        if path.endswith("/env"):
            return {"envs": [
                {"id": "prod", "key": "DATABASE_URL_UNPOOLED", "target": ["production"]},
                {"id": "preview", "key": "DATABASE_URL_UNPOOLED", "target": ["preview"]},
            ]}
        assert path.endswith("/env/prod")
        return {"value": "production", "decrypted": True}
    monkeypatch.setattr(project, "request", request)
    assert project.environment() == {"DATABASE_URL_UNPOOLED": "production"}
    project.client.close()


def _vercel(handler):
    from scripts.vercel_release import VercelProject
    project = VercelProject("project", "token", "team")
    project.client.close()
    project.client = httpx.Client(base_url="https://api.vercel.com", transport=httpx.MockTransport(handler))
    return project


def test_release_uploads_each_file_once_and_the_deployment_names_files_by_digest(monkeypatch):
    from scripts import vercel_release

    files = [{"file": "vercel.json", "data": b'{"regions": ["fra1"]}'}, {"file": "app.py", "data": b"x" * 4000},
             {"file": "a/__init__.py", "data": b""}, {"file": "b/__init__.py", "data": b""}]
    monkeypatch.setattr(vercel_release, "source_files", lambda root, web: files)
    monkeypatch.setattr(vercel_release.subprocess, "check_output", lambda *args, **kwargs: "c" * 40 + "\n")
    uploads, created = [], []

    def handler(request):
        if request.url.path == "/v2/files":
            assert request.method == "POST" and request.url.params["teamId"] == "team"
            digest, size = hashlib.sha1(request.content).hexdigest(), str(len(request.content))
            assert request.headers["x-vercel-digest"] == request.headers["x-now-digest"] == digest
            assert request.headers.get_list("content-length") == [size] and request.headers["x-now-size"] == size
            assert request.headers["content-type"] == "application/octet-stream"
            uploads.append(request.content)
            return httpx.Response(200, json={"urls": []})
        if request.url.path == "/v9/projects/project":
            return httpx.Response(200, json={"name": "forecastlab-api", "targets": {"production": {"id": "live"}}})
        assert request.method == "POST" and request.url.path == "/v13/deployments"
        created.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "dpl_new", "readyState": "READY", "url": "new.vercel.app"})

    project = _vercel(handler)
    bundle = project.prepare(Path("."), web=False)
    assert sorted(uploads) == sorted([b"", b'{"regions": ["fra1"]}', b"x" * 4000])  # the two empty files are one upload
    assert not created  # preparing a bundle creates no deployment
    assert project.stage(Path("."), bundle, web=False)["id"] == "dpl_new"
    body = created[0]
    assert body["regions"] == ["fra1"] and body["meta"] == {"githubCommitSha": "c" * 40}
    assert body["files"] == [{"file": f["file"], "sha": hashlib.sha1(f["data"]).hexdigest(), "size": len(f["data"])}
                             for f in files]
    assert len(json.dumps(body)) < 2000  # file contents travel in the uploads, never in this call
    project.client.close()


def test_a_refused_vercel_call_says_why_and_a_refused_upload_is_not_sent_again():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/v2/files":
            return httpx.Response(403, json={"error": {"code": "forbidden", "message": "Not authorized"}})
        return httpx.Response(400, json={"error": {"code": "bad_request", "message": "Request body too large\n<b>"}})

    project = _vercel(handler)
    with pytest.raises(RuntimeError, match=r"^vercel_api_failed:POST:/v2/files:403:forbidden:Not authorized$"):
        project.upload(b"data", "0" * 40)
    assert calls == ["/v2/files"]
    with pytest.raises(RuntimeError, match=r"^vercel_api_failed:POST:/v13/deployments:400:bad_request:Request body too largeb$"):
        project.request("/v13/deployments", method="POST", body={})
    # a call that reads production settings logs the status and code only, never Vercel's message
    with pytest.raises(RuntimeError, match=r"^vercel_api_failed:GET:/v10/projects/project/env:400:bad_request$"):
        project.request("/v10/projects/project/env")
    project.client.close()


def test_an_upload_is_sent_again_after_a_server_error_and_gives_up_after_three():
    answers = [httpx.Response(502), httpx.Response(200, json={})]
    project = _vercel(lambda request: answers.pop(0))
    project.upload(b"data", "0" * 40, wait=0)
    assert not answers
    answers.extend(httpx.Response(503, json={"error": {"code": "unavailable"}}) for _ in range(4))
    with pytest.raises(RuntimeError, match=r"^vercel_api_failed:POST:/v2/files:503:unavailable$"):
        project.upload(b"data", "0" * 40, wait=0)
    assert len(answers) == 1
    project.client.close()


@pytest.mark.parametrize("refused", [False, True])
def test_release_uploads_both_bundles_before_it_pauses_dispatch(refused):
    from scripts.release_cloud import release_with_clients

    order = []

    class Client:
        def __init__(self, name):
            self.name, self.client = name, self

        def prepare(self, root, *, web):
            order.append(("prepare", self.name, web))
            if refused and web:
                raise RuntimeError("vercel_api_failed:POST:/v2/files:403")
            return {}

        def close(self):
            order.append(("close", self.name))

    def control(path):
        order.append(path)
        raise RuntimeError("stop_at_the_pause")

    with pytest.raises(RuntimeError, match="vercel_api_failed" if refused else "stop_at_the_pause"):
        release_with_clients(Client("api"), Client("web"), control)
    # a refused upload ends the release with dispatch untouched
    assert order == [("prepare", "api", False), ("prepare", "web", True),
                     *([] if refused else ["/internal/release/pause"]), ("close", "api"), ("close", "web")]


def test_the_committed_release_bundles_fit_the_reviewed_size():
    # Fails here, before a merge, when a bundle outgrows BUNDLE_LIMIT; the release would refuse it too.
    from scripts.release_cloud import ROOT
    from scripts.vercel_release import BUNDLE_LIMIT, source_files

    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    for web in (False, True):
        files = source_files(ROOT, web=web)
        assert any(f["file"] == "vercel.json" for f in files)
        assert all(isinstance(f["data"], bytes) for f in files)
        assert sum(len(f["data"]) for f in files) <= BUNDLE_LIMIT


def test_release_preserves_disabled_state_and_original_reason_across_polls(client):
    from forecastlab_api.autopilot_routes import release_complete, release_pause
    from forecastlab_api.autopilot_store import state
    from forecastlab_api.db import SessionLocal

    with SessionLocal() as session:
        current = state(session)
        current.enabled = False
        current.pause_reason = "Awaiting live source qualification"
        session.commit()
        for _ in range(2):
            assert release_pause(session)["drained"]
            session.commit()
        assert not release_complete(session)["automatic_spending_enabled"]
        session.commit()
        assert state(session).pause_reason == "Awaiting live source qualification"
        release_complete(session)
        assert state(session).pause_reason == "Awaiting live source qualification"


@pytest.mark.parametrize("gaps", [[], ["Method changed; qualify the new revision"]])
def test_release_resumes_previously_enabled_policy_only_if_still_qualified(client, monkeypatch, gaps):
    from forecastlab_api import autopilot
    from forecastlab_api.autopilot_routes import release_complete, release_pause
    from forecastlab_api.autopilot_store import state
    from forecastlab_api.db import SessionLocal

    monkeypatch.setattr(autopilot, "enable_gaps", lambda _: gaps)
    with SessionLocal() as session:
        state(session).enabled = True
        session.commit()
        release_pause(session)
        session.commit()
        result = release_complete(session)
        assert result["automatic_spending_enabled"] is (not gaps)
        assert state(session).pause_reason == "; ".join(gaps)


def test_release_completion_respects_owner_pause_during_maintenance(client, monkeypatch):
    from forecastlab_api import autopilot
    from forecastlab_api.autopilot_routes import release_complete, release_pause
    from forecastlab_api.autopilot_store import state
    from forecastlab_api.db import SessionLocal

    monkeypatch.setattr(autopilot, "enable_gaps", lambda _: [])
    with SessionLocal() as session:
        state(session).enabled = True
        session.commit()
        release_pause(session)
        session.commit()
        autopilot.set_enabled(session, False)
        release_pause(session)
        session.commit()
        assert not release_complete(session)["automatic_spending_enabled"]
        assert state(session).pause_reason == "Paused by owner"


def test_local_release_preflight_requires_clean_exact_default_head(monkeypatch):
    from scripts import release_local

    commit = "a" * 40
    responses = {
        ("git", "status", "--porcelain"): "",
        ("git", "remote", "get-url", "origin"): release_local.REPOSITORY,
        ("git", "rev-parse", "HEAD"): commit,
        ("git", "ls-remote", "origin", "refs/heads/" + release_local.DEFAULT_BRANCH): commit + "\trefs/heads/" + release_local.DEFAULT_BRANCH,
        ("gh", "api", "user", "--jq", ".id"): release_local.OWNER_ID,
        ("pg_dump", "--version"): "pg_dump (PostgreSQL) 18.6",
    }
    monkeypatch.setattr(release_local, "output", lambda *args: responses[args])
    release_local.preflight(commit)
    responses[("git", "status", "--porcelain")] = " M apps/api/forecastlab_api/main.py"
    with pytest.raises(RuntimeError, match="release_tree_not_clean"):
        release_local.preflight(commit)
    responses[("git", "status", "--porcelain")] = ""
    responses[("git", "ls-remote", "origin", "refs/heads/" + release_local.DEFAULT_BRANCH)] = "b" * 40 + "\trefs/heads/" + release_local.DEFAULT_BRANCH
    with pytest.raises(RuntimeError, match="release_commit_not_default_head"):
        release_local.preflight(commit)


def test_local_release_control_uses_same_transactional_routes(client):
    from scripts.release_local import database_control

    assert database_control("/internal/release/pause")["drained"]
    assert not database_control("/internal/release/complete")["dispatch_paused"]
    with pytest.raises(RuntimeError, match="unknown_release_control_command"):
        database_control("/internal/release/unrecognized")


def test_local_release_requires_staged_smoke_access_before_pausing():
    from scripts import release_local

    class Project:
        def __init__(self, project_id, name, bypass=True):
            self.details = {"id": project_id, "name": name,
                            "targets": {"production": {"id": "existing"}},
                            "protectionBypass": {"key": {"scope": "automation-bypass"}} if bypass else {}}

        def project(self):
            return self.details

    api = Project(release_local.API_PROJECT_ID, "forecastlab-api")
    web = Project(release_local.WEB_PROJECT_ID, "forecastlab-web")
    release_local.validate_cloud_targets(api, web)
    web.details["protectionBypass"] = {}
    with pytest.raises(RuntimeError, match="staged_smoke_bypass_missing"):
        release_local.validate_cloud_targets(api, web)
