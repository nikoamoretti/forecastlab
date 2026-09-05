from __future__ import annotations

import os
import subprocess
from pathlib import Path

from forecastlab.paths import project_root

ROOT = project_root()


def current_git_commit(*, root: Path | None = None) -> str | None:
    if root is None and os.environ.get("VERCEL_GIT_COMMIT_SHA"):
        return os.environ["VERCEL_GIT_COMMIT_SHA"]
    directory = root or ROOT
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=directory,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None
