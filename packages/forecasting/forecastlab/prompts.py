from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


def load_prompt(name: str, *, prompts_dir: Path | None = None) -> tuple[str, str]:
    """Return (prompt_text, version_label) from a versioned prompt file."""
    directory = prompts_dir or PROMPTS_DIR
    path = directory / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Missing prompt file: {path}")
    text = path.read_text(encoding="utf-8")
    version = "unknown"
    for line in text.splitlines():
        if line.startswith("PROMPT_VERSION:"):
            version = line.split(":", 1)[1].strip()
            break
    return text, version
