"""What was done to research and forecast each question, for its page on the site.

Built from a question directory's ``research.json`` (the evidence the forecasters read),
``briefs.json`` (the brief the judges read) and ``judge_panel_log.json`` (every judge call,
including the ones that failed). ``scripts/build_track_record.py`` writes one file per
directory to ``data/research/<directory>.json``, because the API deployment does not ship
``artifacts/``; ``GET /api/research/{directory}/{question}`` serves one question from it.

Downloaded page text is left out (it is long and only used to check quotes), and so is
anything from ``market_snapshot.json``: the forecasters never see market odds.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DIRECTORY = re.compile(r"^prospective_[a-z0-9_]+$")
RESEARCHERS = {"grok-4.7-low": "Cursor (Grok 4.7)", "claude-sonnet": "Claude Sonnet", "claude-haiku": "Claude Haiku"}


def _load(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def _researcher(name: str) -> str:
    return RESEARCHERS.get(name, name)


def directory_research(directory: Path) -> dict[str, dict[str, Any]]:
    """Every question in one directory that has saved research, a brief or judge calls."""
    research = _load(directory / "research.json") or {}
    briefs = (_load(directory / "briefs.json") or {})
    judges = (_load(directory / "judge_panel_log.json") or {}).get("attempts", [])
    ids = set(research.get("findings", {})) | set(research.get("failed", {})) | set(briefs.get("briefs", {})) \
        | {a["id"] for a in judges}
    out: dict[str, dict[str, Any]] = {}
    for qid in sorted(ids):
        found = research.get("findings", {}).get(qid)
        sources = [{key: s.get(key) for key in ("title", "url", "published", "verified", "quotes")}
                   for s in research.get("sources", {}).get(qid, [])]
        by = research.get("researched_by", {}).get(qid) or research.get("model", "")
        entry: dict[str, Any] = {
            "researched_by": _researcher(by) if research else None,
            "researched_at": research.get("researched_at"),
            "research_seconds": research.get("seconds", {}).get(qid),
            "research_cost_usd": research.get("cost_usd", {}).get(qid),
            "quotes_checked_at": research.get("downloaded_at"),
            "research_failed": research.get("failed", {}).get(qid),
            "facts": found.get("facts", []) if found else [],
            "base_rate": found.get("base_rate") if found else None,
            "unknowns": found.get("uncertainties", []) if found else [],
            "sources": sources,
            "brief": briefs.get("briefs", {}).get(qid),
            "brief_written_at": briefs.get("written_at"),
            "judges": [{"method": a["method"], "model": a.get("served_model") or a.get("model"), "at": a.get("at"),
                        "ok": a.get("ok", False), "error": a.get("error"), "cost_usd": a.get("cost_usd")}
                       for a in judges if a["id"] == qid],
        }
        out[qid] = entry
    return out


def build_research(artifacts: Path, output: Path) -> int:
    """Write data/research/<directory>.json for every directory with anything to show; returns the count."""
    output.mkdir(parents=True, exist_ok=True)
    written = 0
    for directory in sorted(artifacts.glob("prospective_*")):
        if not DIRECTORY.match(directory.name):
            continue
        questions = directory_research(directory)
        if questions:
            (output / f"{directory.name}.json").write_text(json.dumps(questions, indent=1, sort_keys=True) + "\n")
            written += 1
    return written


def question_research(data: Path, directory: str, question: str) -> dict[str, Any] | None:
    """One question's research from data/research, or None when nothing was saved for it."""
    if not DIRECTORY.match(directory):
        return None
    saved = _load(data / "research" / f"{directory}.json") or {}
    return saved.get(question)
