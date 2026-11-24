#!/usr/bin/env python3
"""Run the isolated Autopilot outcome acceptance against a private local stack."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.helpers.autopilot_outcome_acceptance import (  # noqa: E402
    REPO_WEB_DIR,
    ForbiddenDatabaseTarget,
    OutcomeAcceptanceError,
    assert_isolated_database_url,
    assert_isolated_workspace,
    assert_post_browser_database,
    build_isolated_web,
    install_isolated_web_dependencies,
    load_receipt,
    materialize_isolated_web,
    official_pdf_sha256,
    repo_web_next_fingerprint,
    sanitized_environ,
    seed_outcome_acceptance,
    write_mock_runtime_credentials,
)

RESERVED_APP_PORTS = {3000, 8765}


class OwnedProcess:
    def __init__(self, popen: subprocess.Popen[str], *, name: str, log_path: Path):
        self.popen = popen
        self.name = name
        self.log_path = log_path
        self.pgid = os.getpgid(popen.pid)

    @property
    def pid(self) -> int:
        return self.popen.pid


def free_port() -> int:
    for _ in range(32):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
            handle.bind(("127.0.0.1", 0))
            port = int(handle.getsockname()[1])
        if port not in RESERVED_APP_PORTS:
            return port
    raise RuntimeError("unable_to_allocate_isolated_port")


def wait_for_http(url: str, *, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last_error = "unreachable"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return
                last_error = f"http_{response.status}"
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return
            last_error = f"http_{exc.code}"
        except Exception as exc:  # noqa: BLE001
            last_error = type(exc).__name__
        time.sleep(0.4)
    raise RuntimeError(f"service_not_ready:{url}:{last_error}")


def start_owned(command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path, name: str) -> OwnedProcess:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
    popen = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    handle.close()
    return OwnedProcess(popen, name=name, log_path=log_path)


def stop_owned(processes: list[OwnedProcess]) -> None:
    for owned in processes:
        if owned.popen.poll() is not None:
            continue
        try:
            os.killpg(owned.pgid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    deadline = time.time() + 10
    for owned in processes:
        remaining = max(0.1, deadline - time.time())
        try:
            owned.popen.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(owned.pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            owned.popen.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-workspace", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--purge-workspace",
        action="store_true",
        help="Delete the private workspace after a successful run. Evidence is kept by default.",
    )
    args = parser.parse_args()
    workspace = Path(tempfile.mkdtemp(prefix="forecastlab-outcome-acceptance-"))
    assert_isolated_workspace(workspace)
    database_url = f"sqlite:///{workspace / 'acceptance.sqlite'}"
    assert_isolated_database_url(database_url, workspace)
    logs = workspace / "logs"
    logs.mkdir()
    seed_receipt_path = workspace / "seed-receipt.json"
    browser_actions = workspace / "browser-actions.json"
    final_receipt_path = workspace / "final-receipt.json"
    api_port = free_port()
    web_port = free_port()
    api_origin = f"http://127.0.0.1:{api_port}"
    web_origin = f"http://127.0.0.1:{web_port}"
    env = sanitized_environ(
        workspace=workspace,
        database_url=database_url,
        api_origin=api_origin,
        web_origin=web_origin,
    )
    owned: list[OwnedProcess] = []
    failed = False
    try:
        os.environ.clear()
        os.environ.update(env)
        receipt = seed_outcome_acceptance(workspace)
        if receipt.get("source_sha256") != official_pdf_sha256():
            raise OutcomeAcceptanceError("retained_source_hash_mismatch")
        write_mock_runtime_credentials(workspace)
        receipt_path = Path(receipt["receipt_path"])
        if receipt_path.resolve() != seed_receipt_path.resolve():
            raise OutcomeAcceptanceError("seed_receipt_must_stay_in_workspace")

        owned.append(
            start_owned(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "forecastlab_api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api_port),
                ],
                cwd=REPO_ROOT,
                env=env,
                log_path=logs / "api.log",
                name="api",
            )
        )
        wait_for_http(f"{api_origin}/health")
        prior_repo_next = repo_web_next_fingerprint()
        isolated_web = materialize_isolated_web(workspace)
        install_isolated_web_dependencies(isolated_web, env=env, log_path=logs / "npm-ci.log")
        isolated_next = build_isolated_web(isolated_web, env=env, log_path=logs / "web-build.log")
        if repo_web_next_fingerprint() != prior_repo_next:
            raise OutcomeAcceptanceError("repo_next_mutated")
        owned.append(
            start_owned(
                ["npx", "next", "start", "--hostname", "127.0.0.1", "--port", str(web_port)],
                cwd=isolated_web,
                env={**env, "FORECASTLAB_API_ORIGIN": api_origin, "PORT": str(web_port)},
                log_path=logs / "web.log",
                name="web",
            )
        )
        wait_for_http(web_origin)
        playwright = subprocess.run(
            [
                "npx",
                "playwright",
                "test",
                "-c",
                "playwright.outcome-acceptance.config.ts",
                "--reporter=line",
            ],
            cwd=REPO_WEB_DIR,
            env={
                **env,
                "PLAYWRIGHT_BASE_URL": web_origin,
                "AUTOPILOT_ACCEPTANCE_RECEIPT": str(receipt_path),
                "AUTOPILOT_ACCEPTANCE_BROWSER_ACTIONS": str(browser_actions),
            },
            text=True,
            capture_output=True,
            check=False,
        )
        (logs / "playwright.stdout.log").write_text(playwright.stdout, encoding="utf-8")
        (logs / "playwright.stderr.log").write_text(playwright.stderr, encoding="utf-8")
        if playwright.returncode != 0:
            raise OutcomeAcceptanceError(
                f"playwright_failed:{playwright.stdout[-1500:]}\n{playwright.stderr[-1500:]}"
            )
        if not browser_actions.is_file():
            raise OutcomeAcceptanceError("browser_actions_receipt_missing")
        database = assert_post_browser_database(
            workspace, load_receipt(receipt_path), json.loads(browser_actions.read_text(encoding="utf-8"))
        )
        final = {
            "status": "passed",
            "workspace": str(workspace),
            "seed_receipt": str(seed_receipt_path),
            "browser_actions": str(browser_actions),
            "final_receipt": str(final_receipt_path),
            "isolated_web": str(isolated_web),
            "isolated_next": str(isolated_next),
            "repo_next_fingerprint": prior_repo_next,
            "question_id": receipt["question_id"],
            "proposal_id": receipt["proposal_id"],
            "observation_period": receipt["observation_period"],
            "release_at": receipt["release_at"],
            "database": database,
            "api_origin": api_origin,
            "web_origin": web_origin,
            "owned_pids": [owned_proc.pid for owned_proc in owned],
        }
        final_receipt_path.write_text(json.dumps(final, indent=2), encoding="utf-8")
        print(json.dumps(final, indent=2))
        return 0
    except (ForbiddenDatabaseTarget, OutcomeAcceptanceError, RuntimeError, json.JSONDecodeError) as exc:
        failed = True
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(exc),
                    "workspace": str(workspace),
                    "logs": str(logs),
                    "seed_receipt": str(seed_receipt_path) if seed_receipt_path.exists() else None,
                    "final_receipt": str(final_receipt_path) if final_receipt_path.exists() else None,
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        stop_owned(owned)
        if args.purge_workspace and not failed:
            shutil.rmtree(workspace, ignore_errors=True)
        else:
            print(f"acceptance workspace preserved: {workspace}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
