"""Checks what the blind forecaster opened (docs/DAILY_CLAUDE_FORECASTS.md, step 3).

The forecaster is a Claude Code subagent that is handed one folder: the saved digests are in it, and its
forecast files and briefs go into it. It must open nothing else, because the repository's files and the
market sites hold prices of open questions. ``audit`` reads the subagent's transcript (one JSON object per
line, as Claude Code saves it) and lists every tool call that reached outside that folder.

It reads what the forecaster asked for, never what came back, so the audit prints no price. It is made to
catch a mistake (2026-11-10: the forecaster printed an earlier day's ``forecasts.json`` to look up a file
format), not a forecaster that hides what it does: a flag is a tool call to look at, and a clean result means
no call named a place outside the folder.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Tools that read or write files by a path we can check, and bookkeeping tools that open nothing.
FILE_TOOLS = frozenset({"Read", "Write", "Edit", "Glob", "Grep"})
QUIET_TOOLS = frozenset({"SubagentHandback", "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
                         "ToolSearch"})
SHELL_TOOLS = frozenset({"Bash"})

DEVICES = frozenset({"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr"})
PROGRAM_FOLDERS = ("/usr/bin/", "/bin/", "/usr/local/bin/", "/opt/homebrew/bin/")
FOLDER_WORD = "FORECASTERFOLDER"

# An absolute or home path in a command. A "/" inside a URL, a fraction or "and/or" follows a letter, a
# digit, ":" or "/" and is not one.
PATH_IN_COMMAND = re.compile(r"(?<![\w:/.\-])(?:~|\$HOME|\$\{HOME\})?(?:/[\w.\-+@%~]+)+/?")
HOME_IN_COMMAND = re.compile(r"\$HOME\b|\$\{HOME\}|(?<![\w/.~])~(?![\w.~])")
PARENT_FOLDER = re.compile(r"(?<![\w.])\.\.(?=/|\s|$|['\"])")
WHOLE_DISK = re.compile(r"\b(?:find|ls|tree|du|grep|rg|cat)\b[^\n|;&]*\s/(?=\s|$)")
REACHES_OUT = re.compile(
    r"\b(?:curl|wget)\b|\burllib\b|\burlopen\b|\bhttp\.client\b"
    r"|\b(?:import|from)\s+(?:requests|httpx|aiohttp|socket)\b|\brequests\.(?:get|post|request)\b"
    r"|\bgh\s+(?:api|pr|run|repo|workflow|issue|release)\b"
    r"|\bgit\s+(?:clone|show|log|diff|checkout|fetch|pull|cat-file|grep|status|add|commit|push|archive|worktree|stash)\b"
)
# Places that hold prices or another agent's work, however the command spells the way there.
PRICE_PLACES = ("artifacts/", "market_snapshot", "calls.json", "scores.json", "calibrated.json", "track_record",
                "track-record", "packet.json", "second-look", "second_look", "forecastlab", "subagents/",
                ".claude/")


class AuditError(Exception):
    """The transcript could not be audited: say so, and never read that as a clean result."""


@dataclass(frozen=True)
class Call:
    time: str
    tool: str
    input: dict[str, Any]


@dataclass(frozen=True)
class Flag:
    time: str
    tool: str
    reason: str
    asked: str

    def line(self) -> str:
        return f"{self.time} {self.tool}: {self.reason} | {self.asked}"


@dataclass(frozen=True)
class Audit:
    calls: int
    flags: list[Flag]

    @property
    def clean(self) -> bool:
        return not self.flags


def tool_calls(lines: Iterable[str]) -> list[Call]:
    """Every tool call in a Claude Code transcript, in order. Lines that are not such a row are passed over."""
    calls: list[Call] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        message = row.get("message") if isinstance(row, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "tool_use":
                asked = part.get("input")
                calls.append(Call(time=str(row.get("timestamp") or "")[11:19], tool=str(part.get("name") or "?"),
                                  input=asked if isinstance(asked, dict) else {}))
    return calls


def _spellings(folder: Path) -> list[str]:
    """The folder as given and with links resolved (/tmp is /private/tmp on a Mac), longest first."""
    given = os.path.normpath(os.path.expanduser(str(folder)))
    return sorted({given, os.path.realpath(given)}, key=len, reverse=True)


def _inside(path: str, folder: Path) -> bool:
    spelled = os.path.normpath(os.path.expanduser(path))
    for candidate in (spelled, os.path.realpath(spelled)):
        if any(candidate == root or candidate.startswith(root + os.sep) for root in _spellings(folder)):
            return True
    return False


def _file_reasons(call: Call, folder: Path) -> list[str]:
    path = call.input.get("file_path") or call.input.get("path")
    pattern = str(call.input.get("pattern") or "") if call.tool == "Glob" else ""
    reasons: list[str] = []
    if not isinstance(path, str) or not path:
        if not (pattern.startswith(("/", "~")) and _inside(re.split(r"[*?\[{]", pattern)[0], folder)):
            reasons.append("searched without naming the forecaster's folder")
    elif not path.startswith(("/", "~")):
        reasons.append(f"path not spelled from the root, so it is read from the session's own folder: {path}")
    elif not _inside(path, folder):
        reasons.append(f"path outside the folder: {path}")
    if PARENT_FOLDER.search(pattern):
        reasons.append('pattern climbs out with ".."')
    return reasons


def _shell_reasons(command: str, folder: Path) -> list[str]:
    text = command
    for spelling in _spellings(folder):
        text = text.replace(spelling, FOLDER_WORD)
    reasons: list[str] = []
    outside = sorted({found.group(0).rstrip("/") for found in PATH_IN_COMMAND.finditer(text)}
                     - DEVICES - {""})
    outside = [path for path in outside if not path.startswith(PROGRAM_FOLDERS)]
    if outside:
        reasons.append("path outside the folder: " + ", ".join(outside[:4]))
    if HOME_IN_COMMAND.search(text):
        reasons.append("names the home folder")
    if PARENT_FOLDER.search(text):
        reasons.append('climbs out of the folder with ".."')
    if WHOLE_DISK.search(text):
        reasons.append("reads from the top of the disk")
    reached = REACHES_OUT.search(text)
    if reached:
        reasons.append(f"reaches the network or the repository: {reached.group(0)}")
    places = [place for place in PRICE_PLACES if place in text]
    if places:
        reasons.append("names a place that holds prices or another agent's work: " + ", ".join(places))
    return reasons


def reasons(call: Call, folder: Path) -> list[str]:
    """Why this call reached outside ``folder``; empty when it did not."""
    if call.tool in QUIET_TOOLS:
        return []
    if call.tool in FILE_TOOLS:
        return _file_reasons(call, folder)
    if call.tool in SHELL_TOOLS:
        return _shell_reasons(str(call.input.get("command") or ""), folder)
    return ["a tool the forecaster has no use for"]


def _asked(call: Call) -> str:
    """What the call asked for, cut short. Tool results are never part of it."""
    for key in ("command", "file_path", "path", "pattern", "url", "query", "prompt"):
        value = call.input.get(key)
        if isinstance(value, str) and value:
            return " ".join(value.split())[:160]
    return ", ".join(sorted(call.input))[:160]


def audit(transcript: Path, folder: Path) -> Audit:
    """Read the forecaster's transcript and flag each tool call that reached outside ``folder``."""
    try:
        with transcript.open(encoding="utf-8", errors="replace") as lines:
            calls = tool_calls(lines)
    except OSError as error:
        raise AuditError(f"cannot read {transcript}: {error.strerror or error}") from error
    if not calls:
        raise AuditError(f"no tool calls in {transcript}: the wrong file, or a transcript format this does not know")
    flags = [Flag(call.time, call.tool, "; ".join(why), _asked(call))
             for call in calls if (why := reasons(call, folder))]
    return Audit(calls=len(calls), flags=flags)


def find_transcript(named: str, home: Path | None = None) -> Path:
    """``named`` is a transcript file, or a subagent's id as the Agent tool reports it."""
    path = Path(named).expanduser()
    if path.is_file():
        return path
    if re.fullmatch(r"[\w-]+", named):
        projects = (home or Path.home()) / ".claude" / "projects"
        found = sorted(projects.glob(f"**/subagents/agent-{named}.jsonl"), key=lambda each: each.stat().st_mtime)
        if found:
            return found[-1]
    raise AuditError(f"no transcript found for {named}")
