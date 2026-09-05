#!/usr/bin/env python3
"""Verify the supported Mac-local launcher in an isolated mock-only stack."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import tempfile
from pathlib import Path


class MacStartupVerificationError(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def verify_launcher_layout(root: Path) -> dict[str, str]:
    required = {
        "uv_lock": root / "uv.lock",
        "package_lock": root / "apps" / "web" / "package-lock.json",
        "start_command": root / "Start ForecastLab.command",
        "stop_command": root / "Stop ForecastLab.command",
        "up_script": root / "scripts" / "dev_up.sh",
        "down_script": root / "scripts" / "dev_down.sh",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise MacStartupVerificationError(
            "missing_startup_files:" + ",".join(sorted(missing))
        )
    up_text = required["up_script"].read_text(encoding="utf-8")
    down_text = required["down_script"].read_text(encoding="utf-8")
    required_up_markers = (
        "uv sync --extra dev --frozen",
        "npm ci",
        "/health/worker",
        "FORECASTLAB_STARTUP_EXIT_AFTER_READY",
        "FORECASTLAB_CREDENTIALS_PATH",
        "FORECASTLAB_API_ORIGIN",
    )
    if any(marker not in up_text for marker in required_up_markers):
        raise MacStartupVerificationError("launcher_missing_frozen_or_readiness_controls")
    if "pkill" in down_text:
        raise MacStartupVerificationError("launcher_uses_broad_process_kill")
    return {name: str(path.relative_to(root)) for name, path in required.items()}


def verify_isolated_mock_startup(root: Path, *, timeout: float = 180.0) -> dict[str, object]:
    layout = verify_launcher_layout(root)
    with tempfile.TemporaryDirectory(prefix="forecastlab-mac-startup-") as temp:
        isolated = Path(temp)
        data_dir = isolated / "data"
        log_dir = isolated / "logs"
        api_port = _free_port()
        web_port = _free_port()
        env = dict(os.environ)
        env.update(
            {
                "FORECASTLAB_API_HOST": "127.0.0.1",
                "FORECASTLAB_API_PORT": str(api_port),
                "FORECASTLAB_WEB_HOST": "127.0.0.1",
                "FORECASTLAB_WEB_PORT": str(web_port),
                "FORECASTLAB_DATA_DIR": str(data_dir),
                "FORECASTLAB_LOG_DIR": str(log_dir),
                "FORECASTLAB_DATABASE_URL": f"sqlite:///{data_dir / 'forecastlab.db'}",
                "FORECASTLAB_CREDENTIALS_PATH": str(data_dir / "local" / "credentials.json"),
                "FORECASTLAB_MODEL_PROVIDER": "mock",
                "FORECASTLAB_MODEL_NAME": "mock-forecast-v1",
                "FORECASTLAB_MODEL_API_KEY": "",
                "FORECASTLAB_SEARCH_PROVIDER": "mock",
                "FORECASTLAB_SEARCH_API_KEY": "",
                "FORECASTLAB_ALLOW_LOCAL_FIXTURES": "1",
                "FORECASTLAB_NO_BROWSER": "1",
                "FORECASTLAB_STARTUP_EXIT_AFTER_READY": "1",
                "FORECASTLAB_SKIP_DEPENDENCY_SYNC": "1",
            }
        )
        completed = subprocess.run(
            [str(root / "scripts" / "dev_up.sh")],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        sanitized_output = "\n".join(
            line
            for line in (completed.stdout + "\n" + completed.stderr).splitlines()
            if "KEY" not in line.upper() and "AUTHORIZATION" not in line.upper()
        )
        if completed.returncode != 0:
            raise MacStartupVerificationError(
                f"isolated_startup_failed:{completed.returncode}:{sanitized_output[-1200:]}"
            )
        if "isolated startup verification passed" not in sanitized_output.casefold():
            raise MacStartupVerificationError("isolated_startup_receipt_missing")
        residual_pids = sorted(path.name for path in log_dir.glob("*.pid"))
        if residual_pids:
            raise MacStartupVerificationError(
                "residual_startup_pid_files:" + ",".join(residual_pids)
            )
        if not (data_dir / "forecastlab.db").is_file():
            raise MacStartupVerificationError("isolated_database_missing")
        return {
            "status": "passed",
            "mode": "mock",
            "provider_calls": 0,
            "api_port": api_port,
            "web_port": web_port,
            "database_created": True,
            "residual_pid_files": [],
            "layout": layout,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--layout-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.layout_only:
        result: dict[str, object] = {
            "status": "passed",
            "layout": verify_launcher_layout(root),
        }
    else:
        result = verify_isolated_mock_startup(root)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
