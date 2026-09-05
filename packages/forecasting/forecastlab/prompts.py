from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from forecastlab.hashing import sha256_text
from forecastlab.paths import project_root

PROMPTS_DIR = project_root() / "prompts"


class PromptRecord(BaseModel):
    name: str
    text: str
    version: str
    sha256: str


class PromptBundle(BaseModel):
    prompts: dict[str, PromptRecord] = Field(default_factory=dict)

    def get(self, name: str) -> tuple[str, str]:
        record = self.prompts.get(name)
        if record is None:
            raise FileNotFoundError(f"Prompt {name} is not in the frozen bundle")
        return record.text, record.version

    def hashes(self) -> dict[str, str]:
        return {name: record.sha256 for name, record in self.prompts.items()}

    def versions(self) -> dict[str, str]:
        return {name: record.version for name, record in self.prompts.items()}


def prompt_version_from_text(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("PROMPT_VERSION:"):
            return line.split(":", 1)[1].strip()
    return "unknown"


def load_prompt(name: str, *, prompts_dir: Path | None = None) -> tuple[str, str]:
    """Return (prompt_text, version_label) from a versioned prompt file."""
    directory = prompts_dir or PROMPTS_DIR
    path = directory / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Missing prompt file: {path}")
    text = path.read_text(encoding="utf-8")
    return text, prompt_version_from_text(text)


def load_prompt_bundle(*, prompts_dir: Path | None = None) -> PromptBundle:
    directory = prompts_dir or PROMPTS_DIR
    prompts: dict[str, PromptRecord] = {}
    for path in sorted(directory.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        prompts[path.stem] = PromptRecord(
            name=path.stem,
            text=text,
            version=prompt_version_from_text(text),
            sha256=sha256_text(text),
        )
    return PromptBundle(prompts=prompts)


def prompt_hashes(*, prompts_dir: Path | None = None) -> dict[str, str]:
    return load_prompt_bundle(prompts_dir=prompts_dir).hashes()
