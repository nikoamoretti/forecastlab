"""Evidence gathered by cursor-agent on Nico's Mac (or Claude, for the gaps), one headless run per question.

The Mac job (``scripts/mac_research.sh``) builds a task for each new question, asks cursor-agent
(a non-Claude model, ``configs/research.json``) to search, read the primary sources and reply with
JSON evidence, checks every answer, and saves ``research.json``. The Actions ``download`` step then
fetches each cited page and checks the quotes against it (``scripts/research_download.py``).

Cursor gathers evidence only: no probability, no lean. Claude forecasts from a digest of it and the
judge panel, which may not include the research model's family, checks the forecast. No one sees a
market price: tasks carry no market names, links or odds, and answers that cite a market site or
odds are cleaned or rejected.

Runs on the Mac's own Python, so this module uses the standard library only (3.9 or later).
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forecastlab.research import MARKET_WORDS, RESEARCH_FILE, clean, is_market_site

UTC = timezone.utc  # noqa: UP017 -- datetime.UTC needs Python 3.11; the Mac may run 3.9
CONFIG_FILE = "configs/research.json"
MIN_SOURCES = 2
MIN_FACTS = 2
MAX_SOURCES = 8
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

Agent = Callable[[str], str]  # prompt -> the agent's reply text


@dataclass(frozen=True)
class Settings:
    mode: str  # "dry_run" (research to a -dryrun branch, nothing forecast) or "live"
    model: str
    model_family: str  # judges from this family may not sit on the panel
    parallel: int
    timeout_s: int
    daily_run_cap: int
    weekly_run_cap: int
    window_utc: list[int]
    base_branch: str
    # Claude fills the questions Cursor could not research (weekly limit hit, timeouts); see `fill`.
    fallback_model: str = ""
    fallback_family: str = ""
    fallback_timeout_s: int = 600

    @property
    def families(self) -> tuple[str, ...]:
        """Every model family that may have researched a question; none of them may judge."""
        return tuple(f for f in (self.model_family, self.fallback_family) if f)


def load_settings(path: Path) -> Settings:
    data = json.loads(path.read_text())
    if data.get("provider") != "cursor":
        raise ValueError("configs/research.json: provider must be cursor")
    if data["mode"] not in ("dry_run", "live"):
        raise ValueError("configs/research.json: mode must be dry_run or live")
    fallback = data.get("fallback") or {}
    if fallback and fallback.get("provider") != "claude":
        raise ValueError("configs/research.json: fallback provider must be claude")
    return Settings(mode=data["mode"], model=data["model"], model_family=data["model_family"].lower(),
                    parallel=int(data["parallel"]), timeout_s=int(data["timeout_s"]),
                    daily_run_cap=int(data["daily_run_cap"]), weekly_run_cap=int(data["weekly_run_cap"]),
                    window_utc=list(data["window_utc"]), base_branch=data["base_branch"],
                    fallback_model=fallback.get("model", ""), fallback_family=fallback.get("model_family", "").lower(),
                    fallback_timeout_s=int(fallback.get("timeout_s", 600)))


def tasks(directory: Path) -> list[dict[str, str]]:
    """One task per open question in a daily directory, with every market reference removed."""
    found = []
    if (directory / "frozen_manifest.json").exists():
        for entry in json.loads((directory / "questions.json").read_text()):
            found.append({"id": entry["entry_id"], "question": clean(entry["question"]),
                          "criteria": clean(f"{entry['question']} It resolves on the first published value, released "
                                            f"around {entry['release_at'][:10]}; later revisions do not count."),
                          "resolves_on": entry["release_at"][:10], "topic": "US economic data"})
        return found
    listed = json.loads((directory / "questions.json").read_text()) if (directory / "questions.json").exists() else {}
    if isinstance(listed, dict) and listed.get("questions"):
        # A market set as prepared, before anyone has forecast it (forecasts.json comes with `record`).
        # Only these fields are read: the entry's market link and price are never passed on.
        for question in listed["questions"]:
            found.append({"id": question["id"], "question": clean(question["question"]),
                          "criteria": clean(question.get("resolution_criteria") or ""),
                          "resolves_on": (question.get("closes_at") or "")[:10], "topic": question.get("topic") or ""})
        return found
    for question in json.loads((directory / "forecasts.json").read_text())["questions"]:
        if question.get("outcome") is not None or question.get("cancelled"):
            continue
        found.append({"id": question["id"], "question": clean(question["question"]),
                      "criteria": clean(question.get("resolution_criteria") or ""),
                      "resolves_on": question["resolution_date"], "topic": question.get("topic") or ""})
    return found


def prompt(task: dict[str, str], today: str) -> str:
    return (
        "You are a research assistant gathering evidence for a forecasting question. Do not forecast: "
        "give no probability, odds or lean.\n\n"
        f"Today is {today} (UTC).\n"
        f"Question: {task['question']}\n"
        f"How it resolves: {task['criteria']}\n"
        f"Resolution date: {task['resolves_on']}\n"
        + (f"Topic: {task['topic']}\n" if task.get("topic") else "")
        + "\nSearch the web and read the primary sources that decide this question first (official data and "
        "releases, government and agency pages, court and legislative records, filings, schedules, company "
        "announcements), then reporting from the last 7 days. Use only information published before now. "
        "Never use or mention prediction markets, betting sites or odds (Polymarket, Kalshi, Manifold, "
        "Metaculus, PredictIt, bookmakers).\n\n"
        "Reply with one JSON object and nothing else:\n"
        '{"sources": [{"url": "...", "title": "...", "published": "YYYY-MM-DD, or empty if the page has no date", '
        '"quotes": ["a sentence copied word for word from the page", "..."]}],\n'
        ' "facts": [{"fact": "one specific, dated fact", "source": <index of its source in sources, from 0>}],\n'
        ' "base_rate": "how often comparable events happened: the reference class, the counts and the period; '
        'empty if none applies",\n'
        ' "uncertainties": ["what is still unknown and could change the answer"]}\n'
        "Use 3 to 8 sources, each with 1 to 3 quotes that bear on the question."
    )


def _text(value: Any, limit: int) -> str:
    return clean(value if isinstance(value, str) else "")[:limit]


def validate(reply: str) -> dict[str, Any]:
    """The evidence in an agent's reply, cleaned, or ValueError saying why it cannot be used."""
    match = re.search(r"\{.*\}", reply or "", re.DOTALL)
    if not match:
        raise ValueError("no JSON object in the reply")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise ValueError(f"the reply is not valid JSON: {error.msg}") from None
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise ValueError("the reply has no sources list")
    sources: list[dict[str, Any]] = []
    index: dict[int, int] = {}
    for n, item in enumerate(data["sources"]):
        if not isinstance(item, dict):
            continue
        url = item["url"] if isinstance(item.get("url"), str) else ""
        if not url.startswith(("http://", "https://")) or is_market_site(url) or MARKET_WORDS.search(url):
            continue
        if any(s["url"] == url for s in sources) or len(sources) == MAX_SOURCES:
            continue
        quotes = [q for q in (_text(q, 600) for q in item.get("quotes") or [] if isinstance(q, str)) if q][:3]
        published = item.get("published") if isinstance(item.get("published"), str) else ""
        index[n] = len(sources)
        sources.append({"url": url, "title": _text(item.get("title"), 200),
                        "published": published if DATE.match(published) else "", "quotes": quotes})
    facts = []
    for item in data.get("facts") or []:
        if not isinstance(item, dict) or not isinstance(item.get("source"), int) or item["source"] not in index:
            continue
        fact = _text(item.get("fact"), 500)
        if fact:
            facts.append({"fact": fact, "source": index[item["source"]]})
    if len(sources) < MIN_SOURCES:
        raise ValueError(f"only {len(sources)} usable sources")
    if len(facts) < MIN_FACTS:
        raise ValueError(f"only {len(facts)} usable facts")
    uncertainties = [u for u in (_text(u, 300) for u in data.get("uncertainties") or [] if isinstance(u, str)) if u]
    return {"sources": sources, "facts": facts, "base_rate": _text(data.get("base_rate"), 600),
            "uncertainties": uncertainties[:5]}


def cursor_agent(model: str, timeout_s: int, binary: str = "cursor-agent") -> Agent:
    """A headless cursor-agent run in an empty folder; --force lets its search and fetch tools run."""
    def run(text: str) -> str:
        with tempfile.TemporaryDirectory() as folder:
            done = subprocess.run([binary, "-p", "--trust", "--force", "--mode", "ask", "--model", model, text],
                                  cwd=folder, capture_output=True, text=True, timeout=timeout_s)
        if done.returncode != 0:
            raise RuntimeError(f"cursor-agent exited {done.returncode}: {done.stderr.strip()[-300:]}")
        return done.stdout
    return run


def claude_agent(model: str, timeout_s: int, binary: str = "claude") -> Agent:
    """A headless Claude Code run in an empty folder, allowed only its web search and fetch tools."""
    def run(text: str) -> str:
        with tempfile.TemporaryDirectory() as folder:
            done = subprocess.run([binary, "-p", "--model", model, "--effort", "low", "--output-format", "text",
                                   "--allowedTools", "WebSearch", "WebFetch"],
                                  input=text, cwd=folder, capture_output=True, text=True, timeout=timeout_s)
        if done.returncode != 0:
            raise RuntimeError(f"claude exited {done.returncode}: {(done.stderr or done.stdout).strip()[-300:]}")
        return done.stdout
    return run


def research(directory: Path, agent: Agent, today: str, *, parallel: int, budget: int,
             limit: int | None = None, by: str = "") -> dict[str, Any]:
    """Research the directory's questions not yet in research.json, at most ``budget`` agent runs."""
    path = directory / RESEARCH_FILE
    saved = json.loads(path.read_text()) if path.exists() else {}
    if saved and saved.get("provider") != "cursor":
        saved = {}  # an older search provider's file is replaced
    done = set(saved.get("findings", {}))
    todo = [t for t in tasks(directory) if t["id"] not in done][:max(0, min(budget, limit or budget))]

    def one(task: dict[str, str]) -> Any:
        try:
            return validate(agent(prompt(task, today)))
        except Exception as error:  # a failed question is skipped, never the run
            return f"{type(error).__name__}: {error}"[:300]

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        results = list(pool.map(one, todo))
    sources = dict(saved.get("sources", {}))
    findings = dict(saved.get("findings", {}))
    failed = dict(saved.get("failed", {}))
    researched_by = dict(saved.get("researched_by", {}))
    for task, result in zip(todo, results):  # noqa: B905 -- equal lengths; strict= needs Python 3.10
        if isinstance(result, str):
            failed[task["id"]] = result
            continue
        failed.pop(task["id"], None)
        sources[task["id"]] = [{"title": s["title"], "url": s["url"], "published": s["published"],
                                "quotes": s["quotes"], "text": "\n".join(s["quotes"]), "verified": None}
                               for s in result["sources"]]
        findings[task["id"]] = {"facts": result["facts"], "base_rate": result["base_rate"],
                                "uncertainties": result["uncertainties"]}
        if by:
            researched_by[task["id"]] = by
    # "provider" names the file format (research_download.py reads it); researched_by names the model.
    return {"provider": "cursor", "researched_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "runs": len(todo), "sources": sources, "findings": findings, "failed": failed,
            "researched_by": researched_by}


def digest(data: dict[str, Any], qid: str) -> str:
    """The evidence for one question as a short text for the forecaster: facts, base rate, gaps, sources."""
    sources = data.get("sources", {}).get(qid, [])
    found = data.get("findings", {}).get(qid)
    if not found:
        return "No research for this question."
    lines = [f"- {f['fact']} [{f['source'] + 1}]" for f in found["facts"]]
    if found.get("base_rate"):
        lines.append(f"Base rate: {found['base_rate']}")
    lines += [f"Unknown: {u}" for u in found.get("uncertainties", [])]
    for n, s in enumerate(sources, 1):
        check = {True: "quotes checked", False: "quotes not found on page"}.get(s.get("verified"), "not checked")
        lines.append(f"[{n}] {s['title']} ({s['url']}{', ' + s['published'] if s['published'] else ''}; {check})")
    return "\n".join(lines)
