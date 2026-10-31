"""Evidence gathered by cursor-agent on Nico's Mac (or Claude, for the gaps), one headless run per question.

The Mac job (``scripts/mac_research.sh``) builds a task for each new question, asks cursor-agent
(a non-Claude model, ``configs/research.json``) to search, read the primary sources and reply with
JSON evidence, checks every answer, and saves ``research.json``. The Actions ``download`` step then
fetches each cited page and checks the quotes against it (``scripts/research_download.py``).

Each question is researched in up to three passes, one agent run each, in order: the outside view
(the reference class and its counts), the inside view (what is different this time, with the topic's
checklist from ``configs/research_checklists.json``) and the other side (the strongest evidence for the
outcome the first two make less likely). The replies are merged into one set of findings. Claude, as the
fallback researcher, uses one combined pass to save tokens (``passes`` in ``configs/research.json``).

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
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple, Union  # noqa: UP035 -- the Mac may run Python 3.9
from zoneinfo import ZoneInfo

from forecastlab.research import MARKET_WORDS, RESEARCH_FILE, clean, is_market_site

UTC = timezone.utc  # noqa: UP017 -- datetime.UTC needs Python 3.11; the Mac may run 3.9
CONFIG_FILE = "configs/research.json"
CHECKLIST_FILE = "configs/research_checklists.json"
MIN_SOURCES = 2
MIN_FACTS = 2
MAX_SOURCES = 8  # per reply
MAX_TOTAL_SOURCES = 14  # after the passes are merged
RESERVE_PER_LATER_PASS = 3  # an early pass may not use the room a later pass needs for its own sources
MAX_MERGED_QUOTES = 5
MAX_UNCERTAINTIES = 6
MAX_CHECKLIST_ITEMS = 6  # per topic
PASS_ORDER = ("combined", "outside", "inside", "against")
# The US economic-data cohort questions carry their series, not a topic; these match forecastlab.plain_questions.TOPICS.
COHORT_TOPICS = {"unemployment": "Jobs", "payrolls": "Jobs", "jobless_claims": "Jobs", "cpi": "Inflation",
                 "treasury_10y": "Interest rates"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# prompt -> the agent's reply text, or the text and what the run cost in USD when the agent reports it
Agent = Callable[[str], Union[str, Tuple[str, Optional[float]]]]  # noqa: UP006, UP007, UP045 -- Python 3.9
# An agent error that will repeat for every question until the allowance resets, so the run stops asking.
LIMIT_REACHED = re.compile(r"usage limit|limit reached|rate limit|quota", re.IGNORECASE)


@dataclass(frozen=True)
class Settings:
    mode: str  # "dry_run" (research to a -dryrun branch, nothing forecast) or "live"
    model: str
    model_family: str  # judges from this family may not sit on the panel
    parallel: int
    timeout_s: int
    daily_run_cap: int
    weekly_run_cap: int
    window_hours: list[int]  # [start, end): hours of the day, read in window_tz
    window_tz: str  # IANA zone of the window; a US zone moves it with daylight saving time
    base_branch: str
    # Claude fills the questions Cursor could not research (weekly limit hit, timeouts); see `fill`.
    fallback_model: str = ""
    fallback_family: str = ""
    fallback_timeout_s: int = 600
    passes: int = 1  # agent runs per question: 3 = outside view, inside view, other side; 1 = one combined run
    fallback_passes: int = 1

    @property
    def families(self) -> tuple[str, ...]:
        """Every model family that may have researched a question; a judge of one sits out what it researched."""
        return tuple(f for f in (self.model_family, self.fallback_family) if f)

    def in_window(self, now: datetime) -> bool:
        """True on a weekday, in the window's own time zone, when the hour there is inside the window."""
        local = now.astimezone(ZoneInfo(self.window_tz))
        start, end = self.window_hours
        return local.weekday() < 5 and start <= local.hour < end


def load_settings(path: Path) -> Settings:
    data = json.loads(path.read_text())
    if data.get("provider") != "cursor":
        raise ValueError("configs/research.json: provider must be cursor")
    if data["mode"] not in ("dry_run", "live"):
        raise ValueError("configs/research.json: mode must be dry_run or live")
    fallback = data.get("fallback") or {}
    if fallback and fallback.get("provider") != "claude":
        raise ValueError("configs/research.json: fallback provider must be claude")
    zone = data.get("window_tz") or "UTC"
    ZoneInfo(zone)  # a mistyped zone fails here, loudly, not silently at the next run
    passes, fallback_passes = int(data.get("passes", 1)), int(fallback.get("passes", 1))
    if not (1 <= passes <= 3 and 1 <= fallback_passes <= 3):
        raise ValueError("configs/research.json: passes must be 1, 2 or 3")
    return Settings(mode=data["mode"], model=data["model"], model_family=data["model_family"].lower(),
                    parallel=int(data["parallel"]), timeout_s=int(data["timeout_s"]),
                    daily_run_cap=int(data["daily_run_cap"]), weekly_run_cap=int(data["weekly_run_cap"]),
                    window_hours=list(data.get("window_hours") or data["window_utc"]), window_tz=zone,
                    base_branch=data["base_branch"],
                    fallback_model=fallback.get("model", ""), fallback_family=fallback.get("model_family", "").lower(),
                    fallback_timeout_s=int(fallback.get("timeout_s", 600)), passes=passes, fallback_passes=fallback_passes)


def load_checklists(path: Path) -> dict[str, list[str]]:
    """The research checklist for each topic, {} when the file is missing; a malformed file fails loudly."""
    if not path.exists():
        return {}
    topics = json.loads(path.read_text()).get("topics")
    if not isinstance(topics, dict):
        raise ValueError(f"{path.name}: topics must be an object")
    found: dict[str, list[str]] = {}
    for topic, items in topics.items():
        if not isinstance(items, list) or not all(isinstance(i, str) and i.strip() for i in items):
            raise ValueError(f"{path.name}: {topic} must be a list of non-empty strings")
        if len(items) > MAX_CHECKLIST_ITEMS:
            raise ValueError(f"{path.name}: {topic} has more than {MAX_CHECKLIST_ITEMS} items")
        if any(MARKET_WORDS.search(i) for i in items):
            raise ValueError(f"{path.name}: {topic} names a market or odds")
        found[topic] = [i.strip() for i in items]
    return found


def checklist_for(checklists: dict[str, list[str]], topic: str) -> list[str]:
    """The checklist items for a question's topic (exact match, then ignoring case)."""
    if topic in checklists:
        return checklists[topic]
    wanted = topic.strip().lower()
    return next((items for name, items in checklists.items() if name.lower() == wanted), [])


def tasks(directory: Path) -> list[dict[str, str]]:
    """One task per open question in a daily directory, with every market reference removed."""
    found = []
    if (directory / "frozen_manifest.json").exists():
        for entry in json.loads((directory / "questions.json").read_text()):
            found.append({"id": entry["entry_id"], "question": clean(entry["question"]),
                          "criteria": clean(f"{entry['question']} It resolves on the first published value, released "
                                            f"around {entry['release_at'][:10]}; later revisions do not count."),
                          "resolves_on": entry["release_at"][:10],
                          "topic": COHORT_TOPICS.get((entry.get("macro") or {}).get("indicator", ""), "US economic data")})
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


SOURCES_JSON = (
    '"sources": [{"url": "...", "title": "...", "published": "YYYY-MM-DD, or empty if the page has no date", '
    '"quotes": ["a sentence copied word for word from the page", "..."]}],\n'
    ' "facts": [{"fact": "one specific, dated fact", "source": <index of its source in this reply\'s sources, from 0>}]'
)
QUOTE_RULES = (
    "Prefer official and primary pages over aggregators and blogs. Copy quotes exactly from the page text you "
    "opened, never from a search snippet or a summary; if you could not open a page, keep it only if it matters "
    "and leave its quotes empty."
)
SEARCH_RULES = (
    "Search the web and read the primary sources that decide this question first (official data and "
    "releases, government and agency pages, court and legislative records, filings, schedules, company "
    "announcements). Use only information published before now. "
    "Never use or mention prediction markets, betting sites or odds (Polymarket, Kalshi, Manifold, "
    "Metaculus, PredictIt, bookmakers).\n\n"
)
ONLY_JSON = "Reply with one JSON object and nothing else:\n"


def _header(task: dict[str, str], today: str, part: str = "") -> str:
    return (
        "You are a research assistant gathering evidence for a forecasting question. Do not forecast: "
        "give no probability, odds or lean.\n\n"
        + (f"{part}\n\n" if part else "")
        + f"Today is {today} (UTC).\n"
        f"Question: {task['question']}\n"
        f"How it resolves: {task['criteria']}\n"
        f"Resolution date: {task['resolves_on']}\n"
        + (f"Topic: {task['topic']}\n" if task.get("topic") else "")
        + "\n"
    )


def _checklist(items: list[str]) -> str:
    if not items:
        return ""
    return ("Checklist for this topic. Work through every item; record what you find for each as a dated fact "
            "with its source, and if you cannot find one, say so under uncertainties:\n"
            + "\n".join(f"{n}. {item}" for n, item in enumerate(items, 1)) + "\n\n")


def prompt(task: dict[str, str], today: str, checklist: list[str] | None = None) -> str:
    """One combined pass (the fallback researcher's): outside view, inside view and the other side in one run."""
    return (
        _header(task, today)
        + "Work in three steps. First the outside view: find the reference class of comparable past cases "
        "(earlier releases of the same statistic, earlier votes, launches or rulings of the same kind) and count "
        "how often the outcome happened. Then the inside view: find what is specific to this case now that "
        "makes it more or less likely than that base rate. Last the other side: find the strongest evidence for "
        "the outcome that the evidence so far makes less likely.\n"
        "Read the primary sources that decide this question first, then reporting from the last 7 days.\n"
        + SEARCH_RULES
        + _checklist(checklist or [])
        + ONLY_JSON
        + "{" + SOURCES_JSON + ",\n"
        ' "base_rate": "how often comparable events happened: the reference class, the counts and the period; '
        'empty if none applies",\n'
        ' "different_now": "what is specific to this case now, compared with the reference class, and which way '
        'it pushes; empty if nothing stands out",\n'
        ' "against": "the strongest evidence for the outcome the evidence so far makes less likely, stated in the '
        'question\'s own terms and without saying how likely it is; empty if you found none",\n'
        ' "uncertainties": ["what is still unknown and could change the answer"]}\n'
        "Use 3 to 8 sources, each with 1 to 3 quotes that bear on the question. " + QUOTE_RULES
    )


def outside_prompt(task: dict[str, str], today: str, total: int = 3) -> str:
    """Pass 1: the reference class of comparable past cases, counted, from primary data where possible."""
    return (
        _header(task, today, f"Pass 1 of {total}: the outside view only. Later passes look at what is specific to this case.")
        + "Find the reference class of comparable past cases (earlier releases of the same statistic, earlier votes, "
        "launches or rulings of the same kind) and count how often the outcome happened, and over what period. "
        "Work from primary data where you can (the statistics office's own series, official records, the archive of "
        "past votes, rulings or releases) and do the counting yourself instead of quoting an article's summary. "
        "Give the counts and the period. Do not yet look for what makes this case different.\n"
        + SEARCH_RULES
        + ONLY_JSON
        + "{" + SOURCES_JSON + ",\n"
        ' "base_rate": "how often comparable events happened: the reference class, the counts and the period; '
        'empty if none applies"}\n'
        "Use 2 to 6 sources, each with 1 to 3 quotes that bear on the question. " + QUOTE_RULES
    )


def _earlier(found: dict[str, Any]) -> str:
    lines = []
    if found.get("base_rate"):
        lines.append(f"Base rate: {found['base_rate']}")
    if found.get("different_now"):
        lines.append(f"Different this time: {found['different_now']}")
    if found.get("against"):
        lines.append(f"Case for the other outcome: {found['against']}")
    lines += [f"- {f['fact']}" for f in found.get("facts", [])]
    urls = [s["url"] for s in found.get("sources", [])]
    if urls:
        lines.append("Pages already read: " + ", ".join(urls))
    return "\n".join(lines)


def inside_prompt(task: dict[str, str], today: str, outside: dict[str, Any], checklist: list[str] | None = None,
                  total: int = 3) -> str:
    """Pass 2: the latest facts from primary sources, what is different this time, what is scheduled, what is unknown."""
    return (
        _header(task, today, f"Pass 2 of {total}: the inside view. The outside view is done; do not repeat it.")
        + "Already found:\n" + _earlier(outside) + "\n\n"
        "Now find the latest facts from the primary sources that decide this question (central banks, statistics "
        "offices, election boards, courts, filings, schedules) and the reporting from the last 7 days. Report what "
        "is different this time compared with that reference class and which way it pushes, the events scheduled "
        "between now and the resolution date (releases, meetings, votes, deadlines, with their dates), and what is "
        "still unknown. Do not repeat the facts above; every fact must cite a source listed in this reply, so list "
        "again any page above that you rely on.\n"
        + SEARCH_RULES
        + _checklist(checklist or [])
        + ONLY_JSON
        + "{" + SOURCES_JSON + ",\n"
        ' "different_now": "what is specific to this case now, compared with the reference class, and which way '
        'it pushes, including the scheduled events before resolution; empty if nothing stands out",\n'
        ' "uncertainties": ["what is still unknown and could change the answer"]}\n'
        "Use 2 to 6 sources, each with 1 to 3 quotes that bear on the question. " + QUOTE_RULES
    )


def against_prompt(task: dict[str, str], today: str, found: dict[str, Any]) -> str:
    """Pass 3: the strongest evidence for the outcome that passes 1 and 2 make less likely."""
    return (
        _header(task, today, "Pass 3 of 3: the other side. The outside and inside views are done.")
        + "Found so far:\n" + _earlier(found) + "\n\n"
        "Decide which outcome of the question this evidence makes less likely, then find the strongest evidence "
        "for that outcome: facts, precedents, scheduled events, official or expert statements and data that point "
        "toward it, and weaknesses in the evidence above (a stale number, a source that is not primary, a "
        "definition that differs from the resolution rule). Primary sources first. State it in the question's own "
        "terms and give no probability, odds or lean. Do not repeat the facts above; every fact must cite a "
        "source listed in this reply.\n"
        + SEARCH_RULES
        + ONLY_JSON
        + "{" + SOURCES_JSON + ",\n"
        ' "against": "the strongest evidence for that outcome, in a few sentences, each claim dated",\n'
        ' "uncertainties": ["what is still unknown and could change the answer"]}\n'
        "Use 1 to 5 sources, each with 1 to 3 quotes that bear on the question. " + QUOTE_RULES
    )


def _text(value: Any, limit: int) -> str:
    return clean(value if isinstance(value, str) else "")[:limit]


def validate(reply: str, *, min_sources: int = MIN_SOURCES, min_facts: int = MIN_FACTS,
             need_against: bool = False) -> dict[str, Any]:
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
    if len(sources) < min_sources:
        raise ValueError(f"only {len(sources)} usable sources")
    if len(facts) < min_facts:
        raise ValueError(f"only {len(facts)} usable facts")
    against = _text(data.get("against"), 900)
    if need_against and not against:
        raise ValueError("no case for the other outcome")
    uncertainties = [u for u in (_text(u, 300) for u in data.get("uncertainties") or [] if isinstance(u, str)) if u]
    return {"sources": sources, "facts": facts, "base_rate": _text(data.get("base_rate"), 600),
            "different_now": _text(data.get("different_now"), 600), "against": against,
            "uncertainties": uncertainties[:5]}


def _url_key(url: str) -> str:
    return url.split("#")[0].rstrip("/").lower()


def merge(parts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The replies of the passes that ran as one set of findings.

    Sources are de-duplicated by URL (the quotes of a repeated page are pooled) and each pass's fact
    indices are remapped to the merged list; facts are concatenated in pass order. The merged list is
    capped at MAX_TOTAL_SOURCES, an early pass leaving room for the later ones; a fact whose source
    did not fit is dropped. Each text field comes from the pass that owns it.
    """
    order = [name for name in PASS_ORDER if name in parts]
    sources: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    where: dict[str, int] = {}
    said: set[str] = set()
    for position, name in enumerate(order):
        part = parts[name]
        room = MAX_TOTAL_SOURCES - RESERVE_PER_LATER_PASS * (len(order) - position - 1)
        remap: dict[int, int] = {}
        for n, source in enumerate(part["sources"]):
            key = _url_key(source["url"])
            if key in where:
                known = sources[where[key]]
                known["quotes"] = (known["quotes"] + [q for q in source["quotes"] if q not in known["quotes"]]
                                   )[:MAX_MERGED_QUOTES]
                known["published"] = known["published"] or source["published"]
                remap[n] = where[key]
            elif len(sources) < room:
                where[key] = remap[n] = len(sources)
                sources.append({**source, "quotes": list(source["quotes"])})
        for fact in part["facts"]:
            text = " ".join(fact["fact"].lower().split())
            if fact["source"] in remap and text not in said:
                said.add(text)
                facts.append({"fact": fact["fact"], "source": remap[fact["source"]]})
    uncertainties: list[str] = []
    for name in order:
        for item in parts[name]["uncertainties"]:
            if item.lower() not in (u.lower() for u in uncertainties):
                uncertainties.append(item)

    def owned(field: str, *owners: str) -> str:
        return next((parts[o][field] for o in owners if o in parts and parts[o][field]), "")

    return {"sources": sources, "facts": facts, "base_rate": owned("base_rate", "combined", "outside"),
            "different_now": owned("different_now", "combined", "inside"),
            "against": owned("against", "combined", "against"),
            "uncertainties": uncertainties[:MAX_UNCERTAINTIES], "passes_done": order}


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
    def run(text: str) -> str | tuple[str, float | None]:
        with tempfile.TemporaryDirectory() as folder:
            done = subprocess.run([binary, "-p", "--model", model, "--effort", "low", "--output-format", "json",
                                   "--allowedTools", "WebSearch", "WebFetch"],
                                  input=text, cwd=folder, capture_output=True, text=True, timeout=timeout_s)
        if done.returncode != 0:
            raise RuntimeError(f"claude exited {done.returncode}: {(done.stderr or done.stdout).strip()[-300:]}")
        try:  # the JSON envelope carries the reply and what the run cost
            envelope = json.loads(done.stdout)
        except ValueError:
            return done.stdout
        cost = envelope.get("total_cost_usd")
        return str(envelope.get("result") or ""), float(cost) if isinstance(cost, (int, float)) else None
    return run


@dataclass
class Outcome:
    """What the passes for one question came to."""

    result: Any  # the merged evidence, or why there is none
    seconds: float | None
    cost: float | None
    runs: int  # agent runs made
    failed: dict[str, str]  # passes that ran or were skipped without evidence (the first pass failing fails the question)


def runs_per_question(passes: int) -> int:
    return max(1, min(passes, 3))


def research(directory: Path, agent: Agent, today: str, *, parallel: int, budget: int,
             limit: int | None = None, by: str = "", passes: int = 1,
             checklists: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Research the directory's questions not yet in research.json, at most ``budget`` agent runs.

    ``passes`` is how many runs one question takes: 3 = outside view, inside view (with the topic's
    checklist), the other side; 2 = the first two; 1 = one combined run. A question is started only if its
    passes fit in the budget. Passes for one question run in order (each gets the earlier ones' findings);
    questions run ``parallel`` at a time. The first pass failing fails the question; a later pass failing
    keeps the passes that succeeded (``passes_done`` and ``passes_failed`` in its findings).
    """
    path = directory / RESEARCH_FILE
    saved = json.loads(path.read_text()) if path.exists() else {}
    if saved and saved.get("provider") != "cursor":
        saved = {}  # an older search provider's file is replaced
    done = set(saved.get("findings", {}))
    each = runs_per_question(passes)
    todo = [t for t in tasks(directory) if t["id"] not in done][:max(0, min(budget // each, limit or budget))]
    limit_hit = threading.Event()
    names = ["combined"] if each == 1 else ["outside", "inside", "against"][:each]

    def one(task: dict[str, str]) -> Outcome:
        started, cost, runs = time.monotonic(), None, 0
        parts: dict[str, dict[str, Any]] = {}
        failed: dict[str, str] = {}
        items = checklist_for(checklists or {}, task.get("topic", ""))
        for name in names:
            if limit_hit.is_set():
                if not parts:
                    return Outcome("skipped: the research model's usage limit was reached earlier in this run",
                                   None, None, runs, failed)
                failed[name] = "skipped: the research model's usage limit was reached"
                continue
            need: dict[str, Any] = {}
            if name == "combined":
                text = prompt(task, today, items)
            elif name == "outside":
                text = outside_prompt(task, today, each)
            elif name == "inside":
                text, need = inside_prompt(task, today, merge(parts), items, each), {"min_sources": 1, "min_facts": 1}
            else:
                text, need = against_prompt(task, today, merge(parts)), {"min_sources": 1, "min_facts": 1,
                                                                         "need_against": True}
            try:
                runs += 1
                reply = agent(text)
                if isinstance(reply, tuple):
                    reply, spent = reply
                    cost = (cost or 0.0) + (spent or 0.0)
                parts[name] = validate(reply, **need)
            except Exception as error:  # a failed question is skipped, never the run
                if LIMIT_REACHED.search(str(error)):
                    limit_hit.set()
                reason = f"{type(error).__name__}: {error}"[:300]
                if not parts:  # the first pass is the base of the rest: without it the question has no evidence
                    return Outcome(reason, round(time.monotonic() - started, 1), cost, runs, failed)
                failed[name] = reason
        return Outcome(merge(parts), round(time.monotonic() - started, 1), cost, runs, failed)

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        results = list(pool.map(one, todo))
    sources = dict(saved.get("sources", {}))
    findings = dict(saved.get("findings", {}))
    failed = dict(saved.get("failed", {}))
    researched_by = dict(saved.get("researched_by", {}))
    seconds = dict(saved.get("seconds", {}))
    costs = dict(saved.get("cost_usd", {}))
    for task, outcome in zip(todo, results):  # noqa: B905 -- equal lengths; strict= needs Python 3.10
        result = outcome.result
        if outcome.seconds is not None:
            seconds[task["id"]] = outcome.seconds
        if outcome.cost is not None:
            costs[task["id"]] = round(outcome.cost, 4)
        if isinstance(result, str):
            failed[task["id"]] = result
            continue
        failed.pop(task["id"], None)
        sources[task["id"]] = [{"title": s["title"], "url": s["url"], "published": s["published"],
                                "quotes": s["quotes"], "text": "\n".join(s["quotes"]), "verified": None}
                               for s in result["sources"]]
        findings[task["id"]] = {"facts": result["facts"], "base_rate": result["base_rate"],
                                "different_now": result["different_now"], "against": result["against"],
                                "uncertainties": result["uncertainties"], "passes_done": result["passes_done"],
                                **({"passes_failed": outcome.failed} if outcome.failed else {})}
        if by:
            researched_by[task["id"]] = by
    # "provider" names the file format (research_download.py reads it); researched_by names the model.
    return {"provider": "cursor", "researched_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "runs": sum(o.runs for o in results), "passes": each, "sources": sources, "findings": findings,
            "failed": failed, "researched_by": researched_by, "seconds": seconds, "cost_usd": costs}


def digest(data: dict[str, Any], qid: str) -> str:
    """The evidence for one question as a short text for the forecaster: facts, base rate, what differs now, gaps, sources."""
    sources = data.get("sources", {}).get(qid, [])
    found = data.get("findings", {}).get(qid)
    if not found:
        return "No research for this question."
    lines = [f"- {f['fact']} [{f['source'] + 1}]" for f in found["facts"]]
    if found.get("base_rate"):
        lines.append(f"Base rate: {found['base_rate']}")
    if found.get("different_now"):
        lines.append(f"Different this time: {found['different_now']}")
    if found.get("against"):
        lines.append(f"Case for the other outcome: {found['against']}")
    lines += [f"Unknown: {u}" for u in found.get("uncertainties", [])]
    for n, s in enumerate(sources, 1):
        check = {True: "quotes checked", False: "quotes not found on page"}.get(s.get("verified"), "not checked")
        lines.append(f"[{n}] {s['title']} ({s['url']}{', ' + s['published'] if s['published'] else ''}; {check})")
    return "\n".join(lines)


CONFIDENCE_BANDS = ((0.5, 0.6), (0.6, 0.75), (0.75, 0.9), (0.9, 1.01))


def record_notes(track_record: dict[str, Any], method: str = "claude_code_forecaster_v1", misses: int = 5) -> str:
    """What the forecaster's own results so far say, for the top of the digest.

    How often it was right at each confidence level (calibration), by topic, and its latest misses
    with what it said. Only decided, scored questions count; nothing here is about open questions.
    """
    rows = []
    for question in track_record.get("questions", []):
        if question.get("status") != "resolved" or question.get("scored") is False:
            continue
        mine = next((f for f in question.get("forecasts", []) if f["method"] == method), None)
        if mine:
            rows.append((question, mine["probability"], mine.get("rationale", "")))
    if not rows:
        return "Your record so far: nothing decided yet."
    right = [(p > 0.5) == bool(q["outcome"]) for q, p, _ in rows if p != 0.5]
    lines = [f"Your record so far: {sum(right)} right of {len(right)} decided calls "
             f"(Brier {sum((p - q['outcome']) ** 2 for q, p, _ in rows) / len(rows):.3f}; always saying 50% scores 0.25)."]
    for low, high in CONFIDENCE_BANDS:
        band = [(q, p) for q, p, _ in rows if low <= max(p, 1 - p) < high and p != 0.5]
        if band:
            said = sum(max(p, 1 - p) for _, p in band) / len(band)
            hit = sum((p > 0.5) == bool(q["outcome"]) for q, p in band) / len(band)
            verdict = "overconfident" if hit < said - 0.1 else "underconfident" if hit > said + 0.1 else "about right"
            lines.append(f"- When {round(low * 100)}-{min(round(high * 100), 100)}% sure: said {said:.0%} on average, "
                         f"right {hit:.0%} of {len(band)} ({verdict}).")
    topics: dict[str, list[bool]] = {}
    for question, p, _ in rows:
        if p != 0.5:
            topics.setdefault(question.get("topic") or "Other", []).append((p > 0.5) == bool(question["outcome"]))
    lines.append("- By topic: " + "; ".join(f"{topic} {sum(marks)} of {len(marks)}" for topic, marks in sorted(topics.items())) + ".")
    wrong = [(q, p, r) for q, p, r in rows if p != 0.5 and (p > 0.5) != bool(q["outcome"])]
    if wrong:
        lines.append("Latest misses (what you said, what happened):")
        for question, p, rationale in sorted(wrong, key=lambda row: row[0]["resolves_on"], reverse=True)[:misses]:
            reason = rationale.split(". ")[0].strip().rstrip(".")
            reason = reason if len(reason) <= 200 else reason[:200].rsplit(" ", 1)[0] + "…"
            actual = f" ({question['actual']})" if question.get("actual") else ""
            outcome = "yes" if question["outcome"] else "no"
            lines.append(f"- {question['resolves_on']} {question['title']} You said {p:.0%} yes; it was {outcome}{actual}."
                         + (f" Your reason: {reason}." if reason else ""))
    lines.append("Use this to set how sure you are, not to change what the evidence says.")
    return "\n".join(lines)

