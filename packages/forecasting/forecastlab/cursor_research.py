"""Evidence gathered by Claude Haiku in the cloud routine (provider "claude", since 2026-11-10) or cursor-agent on
Nico's Mac (provider "cursor"), one headless run per pass.

The Mac job (``scripts/mac_research.sh``) builds a task for each new question, asks cursor-agent
(a non-Claude model, ``configs/research.json``) to search, read the primary sources and reply with
JSON evidence, checks every answer, and saves ``research.json``. The Actions ``download`` step then
fetches each cited page and checks the quotes against it (``scripts/research_download.py``).

Each question is researched in up to five passes, one agent run each, in order: the rules (the exact
resolution rules, the source that settles it and its latest value), the outside view (the reference class
and its counts), the inside view (what is different this time, with the topic's checklist from
``configs/research_checklists.json``), the other side (the strongest evidence for the outcome the first
three make less likely) and the gap pass (search specifically for the top open unknowns). If the merged
result then has fewer than two primary sources with quotes, one extra round asks only for primary sources;
if it is still short the result carries a warning. The replies are merged into one set of findings.
``passes`` in ``configs/research.json`` picks how many: 5 = all of that, 4 = no gap pass, 3 = the original
outside / inside / against (no rules pass, no primary-source round), 2 = outside and inside, 1 = one
combined pass (the Claude fallback's, to save tokens).

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

from forecastlab import live_counts
from forecastlab.research import MARKET_WORDS, RESEARCH_FILE, clean, is_market_site

UTC = timezone.utc  # noqa: UP017 -- datetime.UTC needs Python 3.11; the Mac may run 3.9
CONFIG_FILE = "configs/research.json"
CHECKLIST_FILE = "configs/research_checklists.json"
MIN_SOURCES = 2
MIN_FACTS = 2
MAX_SOURCES = 8  # per reply
MAX_TOTAL_SOURCES = 20  # after the passes are merged
RESERVE_PER_LATER_PASS = 3  # an early pass may not use the room a later pass needs for its own sources (3 x 4 later passes = 12 of 20)
MIN_PRIMARY = 2  # primary sources with quotes the merged result needs before the extra primary-source round is skipped
MAX_MERGED_QUOTES = 5
MAX_UNCERTAINTIES = 6
MAX_CHECKLIST_ITEMS = 6  # per topic
PASS_ORDER = ("combined", "rules", "outside", "inside", "against", "gap", "primary")
# The passes one question takes for each `passes` setting; "primary" is the conditional extra round after them.
PLANS = {1: ("combined",), 2: ("outside", "inside"), 3: ("outside", "inside", "against"),
         4: ("rules", "outside", "inside", "against"), 5: ("rules", "outside", "inside", "against", "gap")}
MAX_PASSES = max(PLANS)
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
    passes: int = 1  # agent runs per question, see PLANS: 5 = rules, outside, inside, against, gap; 3 = the first three without rules; 1 = one combined run
    fallback_passes: int = 1
    provider: str = "cursor"  # "claude": the claude CLI researches (cloud routine, `fill`); "cursor": cursor-agent on the Mac

    @property
    def families(self) -> tuple[str, ...]:
        """What a judge's model name must not contain to grade a question: it sits out what it researched itself.

        With Cursor this is the model family (Grok, plus Claude for the fallback's gaps). With Claude as the
        researcher the rule is per model, not per family: the panel's only judge is Claude Sonnet, so a family
        rule would leave nobody to judge anything; Haiku research is checked by Sonnet, but Sonnet research
        would leave the Sonnet judge sitting out every question.
        """
        if self.provider == "claude":
            return (self.model.lower(),)
        return tuple(f for f in (self.model_family, self.fallback_family) if f)

    @property
    def barred(self) -> tuple[str, ...]:
        """Names no judge on the panel may carry at all (the research model's family, or with Claude its exact model)."""
        return (self.model.lower(),) if self.provider == "claude" else (self.model_family,)

    def in_window(self, now: datetime) -> bool:
        """True on a weekday, in the window's own time zone, when the hour there is inside the window."""
        local = now.astimezone(ZoneInfo(self.window_tz))
        start, end = self.window_hours
        return local.weekday() < 5 and start <= local.hour < end


def load_settings(path: Path) -> Settings:
    data = json.loads(path.read_text())
    provider = data.get("provider")
    if provider not in ("cursor", "claude"):
        raise ValueError("configs/research.json: provider must be claude or cursor")
    if data["mode"] not in ("dry_run", "live"):
        raise ValueError("configs/research.json: mode must be dry_run or live")
    fallback = data.get("fallback") or {}
    if fallback and provider == "claude":
        raise ValueError("configs/research.json: a claude provider needs no fallback (remove it)")
    if fallback and fallback.get("provider") != "claude":
        raise ValueError("configs/research.json: fallback provider must be claude")
    zone = data.get("window_tz") or "UTC"
    ZoneInfo(zone)  # a mistyped zone fails here, loudly, not silently at the next run
    passes, fallback_passes = int(data.get("passes", 1)), int(fallback.get("passes", 1))
    if not (1 <= passes <= MAX_PASSES and 1 <= fallback_passes <= MAX_PASSES):
        raise ValueError("configs/research.json: passes must be 1, 2, 3, 4 or 5")
    return Settings(mode=data["mode"], model=data["model"], model_family=data["model_family"].lower(),
                    parallel=int(data["parallel"]), timeout_s=int(data["timeout_s"]),
                    daily_run_cap=int(data["daily_run_cap"]), weekly_run_cap=int(data["weekly_run_cap"]),
                    window_hours=list(data.get("window_hours") or data["window_utc"]), window_tz=zone,
                    base_branch=data["base_branch"],
                    fallback_model=fallback.get("model", ""), fallback_family=fallback.get("model_family", "").lower(),
                    fallback_timeout_s=int(fallback.get("timeout_s", 600)), passes=passes, fallback_passes=fallback_passes,
                    provider=provider)


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
    '"quotes": ["a sentence copied word for word from the page", "..."], '
    '"primary": true for an official, agency, company, court, exchange or data-publisher page or the source '
    'that settles the question, else false}],\n'
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
        + live_counts.live_rules(task, today)[0]
    )


def _sources_json(task: dict[str, str], today: str) -> str:
    """The shared reply fields, plus `tally` (count questions) or `spot` (asset-price questions)."""
    return SOURCES_JSON + live_counts.live_rules(task, today)[1]


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
        + "{" + _sources_json(task, today) + ",\n"
        ' "base_rate": "how often comparable events happened: the reference class, the counts and the period; '
        'empty if none applies",\n'
        ' "different_now": "what is specific to this case now, compared with the reference class, and which way '
        'it pushes; empty if nothing stands out",\n'
        ' "against": "the strongest evidence for the outcome the evidence so far makes less likely, stated in the '
        'question\'s own terms and without saying how likely it is; empty if you found none",\n'
        ' "uncertainties": ["what is still unknown and could change the answer"]}\n'
        "Use 3 to 8 sources, each with 1 to 3 quotes that bear on the question. " + QUOTE_RULES
    )


def rules_prompt(task: dict[str, str], today: str, total: int = 5) -> str:
    """Pass 1: the exact resolution rules, the source that settles the question and its latest value."""
    return (
        _header(task, today, f"Pass 1 of {total}: the rules. Later passes look at base rates, what is specific to "
                             "this case and the other side; do not do their work.")
        + "Find the exact resolution rules of this question and the source that settles it: the agency, site, "
        "exchange, court or release that decides the outcome, and the page with its rules or methodology. Open it "
        "and read the current status or latest official value as of now (the latest print, count, level, ruling or "
        "stage, with its date). Then list the wording edge cases that could flip the answer: the exact dates and "
        "time zones that count, whether revised or later-corrected values count, ties and exact-threshold cases, "
        "what does and does not count, and what happens if the source is delayed or changes.\n"
        + SEARCH_RULES
        + ONLY_JSON
        + "{" + _sources_json(task, today) + ",\n"
        ' "resolution": {"settling_source": "the agency or site that settles it, with its URL",\n'
        '   "criteria": "the resolution rule in the source\'s own terms, short",\n'
        '   "current_status": "the latest official value or status as of today, dated",\n'
        '   "edge_cases": ["one wording edge case each: dates, time zones, revisions, ties, what counts"]}}\n'
        "Use 2 to 5 sources, each with 1 to 3 quotes that bear on the rules or the current value. " + QUOTE_RULES
    )


def outside_prompt(task: dict[str, str], today: str, total: int = 3, number: int = 1) -> str:
    """The reference class of comparable past cases, counted, from primary data where possible."""
    return (
        _header(task, today, f"Pass {number} of {total}: the outside view only. Later passes look at what is specific to this case.")
        + "Find the reference class of comparable past cases (earlier releases of the same statistic, earlier votes, "
        "launches or rulings of the same kind) and count how often the outcome happened, and over what period. "
        "Work from primary data where you can (the statistics office's own series, official records, the archive of "
        "past votes, rulings or releases) and do the counting yourself instead of quoting an article's summary. "
        "Give the counts and the period. Do not yet look for what makes this case different.\n"
        + SEARCH_RULES
        + ONLY_JSON
        + "{" + _sources_json(task, today) + ",\n"
        ' "base_rate": "how often comparable events happened: the reference class, the counts and the period; '
        'empty if none applies"}\n'
        "Use 2 to 6 sources, each with 1 to 3 quotes that bear on the question. " + QUOTE_RULES
    )


def _earlier(found: dict[str, Any]) -> str:
    lines = []
    rules = found.get("resolution") or {}
    for label, key in (("Settling source", "settling_source"), ("Resolution rule", "criteria"),
                       ("Status as of the rules pass", "current_status")):
        if rules.get(key):
            lines.append(f"{label}: {rules[key]}")
    if rules.get("edge_cases"):
        lines.append("Wording edge cases: " + "; ".join(rules["edge_cases"]))
    if found.get("base_rate"):
        lines.append(f"Base rate: {found['base_rate']}")
    if found.get("different_now"):
        lines.append(f"Different this time: {found['different_now']}")
    if found.get("against"):
        lines.append(f"Case for the other outcome: {found['against']}")
    tally = found.get("tally")
    if tally:
        lines.append(f"Running tally already read: {live_counts.fmt(tally['value'])} {tally.get('unit', '')} at {tally.get('read_at', '')} "
                     f"from {tally['source_url']}")
    lines += [f"- {f['fact']}" for f in found.get("facts", [])]
    lines += [f"Still unknown: {u}" for u in found.get("uncertainties", [])]
    urls = [s["url"] for s in found.get("sources", [])]
    if urls:
        lines.append("Pages already read: " + ", ".join(urls))
    return "\n".join(lines)


def inside_prompt(task: dict[str, str], today: str, outside: dict[str, Any], checklist: list[str] | None = None,
                  total: int = 3, number: int = 2) -> str:
    """The latest facts from primary sources, what is different this time, what is scheduled, what is unknown."""
    return (
        _header(task, today, f"Pass {number} of {total}: the inside view. The outside view is done; do not repeat it.")
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
        + "{" + _sources_json(task, today) + ",\n"
        ' "different_now": "what is specific to this case now, compared with the reference class, and which way '
        'it pushes, including the scheduled events before resolution; empty if nothing stands out",\n'
        ' "uncertainties": ["what is still unknown and could change the answer"]}\n'
        "Use 2 to 6 sources, each with 1 to 3 quotes that bear on the question. " + QUOTE_RULES
    )


def against_prompt(task: dict[str, str], today: str, found: dict[str, Any], total: int = 3, number: int = 3) -> str:
    """The strongest evidence for the outcome that the earlier passes make less likely."""
    return (
        _header(task, today, f"Pass {number} of {total}: the other side. The outside and inside views are done.")
        + "Found so far:\n" + _earlier(found) + "\n\n"
        "Decide which outcome of the question this evidence makes less likely, then find the strongest evidence "
        "for that outcome: facts, precedents, scheduled events, official or expert statements and data that point "
        "toward it, and weaknesses in the evidence above (a stale number, a source that is not primary, a "
        "definition that differs from the resolution rule). Primary sources first. State it in the question's own "
        "terms and give no probability, odds or lean. Do not repeat the facts above; every fact must cite a "
        "source listed in this reply.\n"
        + SEARCH_RULES
        + ONLY_JSON
        + "{" + _sources_json(task, today) + ",\n"
        ' "against": "the strongest evidence for that outcome, in a few sentences, each claim dated",\n'
        ' "uncertainties": ["what is still unknown and could change the answer"]}\n'
        "Use 1 to 5 sources, each with 1 to 3 quotes that bear on the question. " + QUOTE_RULES
    )


def gap_prompt(task: dict[str, str], today: str, found: dict[str, Any], total: int = 5, number: int = 5) -> str:
    """The last pass: search specifically for the top open unknowns of everything gathered so far."""
    return (
        _header(task, today, f"Pass {number} of {total}: the gaps. Every other pass is done; do not repeat them.")
        + "Found so far:\n" + _earlier(found) + "\n\n"
        "Pick the 2 or 3 open unknowns that matter most to the answer: the items listed as still unknown above, and "
        "any latest number that is missing or stale (the newest official value, a count, a date, a scheduled release "
        "not yet found). Search specifically to answer each one, primary sources first (the agency, company, court or "
        "exchange that publishes it), and open the page. Record each answer as a dated fact with its source; if an "
        "unknown cannot be answered, say so under uncertainties. Do not repeat the facts above; every fact must cite "
        "a source listed in this reply, so list again any page above that you rely on.\n"
        + SEARCH_RULES
        + ONLY_JSON
        + "{" + _sources_json(task, today) + ",\n"
        ' "uncertainties": ["what is still unknown after this pass and could change the answer"]}\n'
        "Use 1 to 6 sources, each with 1 to 3 quotes that bear on the question. " + QUOTE_RULES
    )


def primary_prompt(task: dict[str, str], today: str, found: dict[str, Any]) -> str:
    """The extra round when the merged result has too few primary sources: only primary sources are asked for."""
    return (
        _header(task, today, "Extra round: primary sources only. The earlier passes found too few official pages.")
        + "Found so far:\n" + _earlier(found) + "\n\n"
        "Find the official page behind the facts above and the one that settles this question: the statistics "
        "agency, central bank, government or court page, company filing or announcement, exchange notice or data "
        "publisher that issues the number or decision itself, not an article about it. Open each page and copy "
        "quotes from it. Do not list news articles, aggregators or blogs. Every fact must cite a source listed in "
        "this reply.\n"
        + SEARCH_RULES
        + ONLY_JSON
        + "{" + _sources_json(task, today) + "}\n"
        "Use 2 to 5 primary sources, each with 1 to 3 quotes that bear on the question, each marked primary. " + QUOTE_RULES
    )


def _text(value: Any, limit: int) -> str:
    return clean(value if isinstance(value, str) else "")[:limit]


def _resolution(value: Any) -> dict[str, Any]:
    """The rules pass's short summary of the resolution rules, cleaned; {} when it has nothing."""
    if not isinstance(value, dict):
        return {}
    found: dict[str, Any] = {key: _text(value.get(key), limit) for key, limit in
                             (("settling_source", 250), ("criteria", 500), ("current_status", 400))}
    edges = value.get("edge_cases")
    edges = [edges] if isinstance(edges, str) else edges if isinstance(edges, list) else []
    found["edge_cases"] = [e for e in (_text(e, 250) for e in edges if isinstance(e, str)) if e][:4]
    return found if any(found.values()) else {}


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
                        "published": published if DATE.match(published) else "", "quotes": quotes,
                        "primary": item.get("primary") is True})
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
    found: dict[str, Any] = {"sources": sources, "facts": facts, "base_rate": _text(data.get("base_rate"), 600),
                             "different_now": _text(data.get("different_now"), 600), "against": against,
                             "uncertainties": uncertainties[:5]}
    resolution = _resolution(data.get("resolution"))
    if resolution:
        found["resolution"] = resolution
    bad = lambda url: is_market_site(url) or bool(MARKET_WORDS.search(url))  # noqa: E731
    tally, spot = live_counts.clean_tally(data.get("tally"), bad), live_counts.clean_spot(data.get("spot"), bad)
    if tally:
        found["tally"] = tally
    if spot:
        found["spot"] = spot
    return found


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
                known["primary"] = bool(known.get("primary") or source.get("primary"))
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

    merged = {"sources": sources, "facts": facts, "base_rate": owned("base_rate", "combined", "outside"),
              "different_now": owned("different_now", "combined", "inside"),
              "against": owned("against", "combined", "against"),
              "uncertainties": uncertainties[:MAX_UNCERTAINTIES], "passes_done": order}
    resolution = next((parts[o]["resolution"] for o in ("rules", "combined") if o in parts and parts[o].get("resolution")), None)
    if resolution:
        merged["resolution"] = resolution
    for key in ("tally", "spot"):  # the reading taken last wins
        best = live_counts.pick_latest([parts[name].get(key) for name in order])
        if best:
            merged[key] = best
    return merged


# Hosts of agencies, courts, central banks and exchanges that publish their own numbers and decisions.
PRIMARY_HOST = re.compile(r"(^|\.)(gov|mil|int)$|\.(gov|go|gouv|govt)\.[a-z]{2}$|\.europa\.eu$|\.gc\.ca$|\.admin\.ch$")
PRIMARY_DOMAINS = frozenset("""
stlouisfed.org frbny.org newyorkfed.org bankofengland.co.uk boj.or.jp imf.org worldbank.org oecd.org bis.org
cmegroup.com nyse.com nasdaq.com cboe.com theice.com lseg.com sec.gov courtlistener.com
""".split())
HOST_IN_TEXT = re.compile(r"(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.IGNORECASE)


def _host(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url.strip().lower()).split("/")[0].split(":")[0]


def _registrable(host: str) -> str:
    labels = host.split(".")
    keep = 3 if len(labels) > 2 and len(labels[-1]) == 2 and labels[-2] in ("co", "com", "org", "gov", "ac", "or", "go") else 2
    return ".".join(labels[-keep:])


def is_primary(source: dict[str, Any], settling: str = "") -> bool:
    """True for a source with quotes that is an agency/court/exchange/data-publisher page or the settling source.

    The model's own ``primary`` flag counts, as does a host check (.gov, .mil, .int, national government
    domains, the EU, a list of central banks, exchanges and data publishers) and a match with the domain of
    the settling source named by the rules pass. A page with no quotes is never counted: nothing was read.
    """
    if not source.get("quotes"):
        return False
    host = _host(source["url"])
    if source.get("primary") or PRIMARY_HOST.search(host) or _registrable(host) in PRIMARY_DOMAINS:
        return True
    return any(_registrable(found.lower()) == _registrable(host) for found in HOST_IN_TEXT.findall(settling or ""))


def primary_count(found: dict[str, Any]) -> int:
    settling = (found.get("resolution") or {}).get("settling_source", "")
    return sum(is_primary(s, settling) for s in found["sources"])


CURSOR_TRIES = 2


def cursor_agent(model: str, timeout_s: int, binary: str = "cursor-agent") -> Agent:
    """A headless cursor-agent run in an empty folder; --force lets its search and fetch tools run."""
    def run(text: str) -> str:
        for attempt in range(CURSOR_TRIES):  # a run that times out is tried once more before it counts as failed
            try:
                with tempfile.TemporaryDirectory() as folder:
                    done = subprocess.run([binary, "-p", "--trust", "--force", "--mode", "ask", "--model", model, text],
                                          cwd=folder, capture_output=True, text=True, timeout=timeout_s)
                break
            except subprocess.TimeoutExpired:
                if attempt == CURSOR_TRIES - 1:
                    raise
        if done.returncode != 0:
            raise RuntimeError(f"cursor-agent exited {done.returncode}: {done.stderr.strip()[-300:]}")
        return done.stdout
    return run


# WebFetch does not hand the model the page: a small model answers the `prompt` given to it from the page. A
# researcher that asks "summarize this" therefore has no sentence to copy, and writes no quotes.
CLAUDE_TOOL_NOTES = (
    "How your tools work. WebSearch returns snippets and summaries: facts and leads, never quotes. WebFetch opens "
    "one page, but returns only what its `prompt` asks for, so always call it with a prompt like: \"Copy word for "
    "word, between quotation marks, the 1 to 3 sentences that state <what you need> (the number, date, rule or "
    "count). Add the page's publication date.\" A quote in your reply must be a sentence WebFetch returned that "
    "way, copied unchanged. When WebFetch fails (for example \"proxy refused the connection\"), try one other page "
    "on a different site for that fact; never invent or reconstruct a quote, leave quotes empty for a page you "
    "could not open, and say which pages could not be opened under uncertainties."
)


def claude_agent(model: str, timeout_s: int, binary: str = "claude") -> Agent:
    """A headless Claude Code run in an empty folder, allowed only its web search and fetch tools."""
    def run(text: str) -> str | tuple[str, float | None]:
        with tempfile.TemporaryDirectory() as folder:
            done = subprocess.run([binary, "-p", "--model", model, "--effort", "low", "--output-format", "json",
                                   "--append-system-prompt", CLAUDE_TOOL_NOTES, "--allowedTools", "WebSearch", "WebFetch"],
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


def plan_for(passes: int) -> tuple[str, ...]:
    """The passes one question takes for a ``passes`` setting (see PLANS); out-of-range values are clamped."""
    return PLANS[max(1, min(passes, MAX_PASSES))]


def runs_per_question(passes: int) -> int:
    """The most agent runs one question can take: its passes, plus the one extra primary-source round if it has the rules pass."""
    plan = plan_for(passes)
    return len(plan) + (1 if "rules" in plan else 0)


def research(directory: Path, agent: Agent, today: str, *, parallel: int, budget: int,
             limit: int | None = None, by: str = "", passes: int = 1,
             checklists: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Research the directory's questions not yet in research.json, at most ``budget`` agent runs.

    ``passes`` picks the plan (``PLANS``): 5 = rules, outside view, inside view (with the topic's checklist),
    the other side, gap; 4 = the same without the gap pass; 3 = outside, inside, other side; 2 = the first two
    of those; 1 = one combined run. With the rules pass there is also, only if the merged result has fewer than
    ``MIN_PRIMARY`` primary sources with quotes, one extra round asking only for primary sources, and a warning
    in the findings if it is still short. A question is started only if all its possible runs (see
    ``runs_per_question``) fit in the budget. Passes for one question run in order (each gets the earlier ones'
    findings); questions run ``parallel`` at a time. The outside (or combined) pass failing fails the question;
    the rules pass or a later pass failing keeps the passes that succeeded (``passes_done`` and
    ``passes_failed`` in its findings).
    """
    path = directory / RESEARCH_FILE
    saved = json.loads(path.read_text()) if path.exists() else {}
    if saved and saved.get("provider") != "cursor":
        saved = {}  # an older search provider's file is replaced
    done = set(saved.get("findings", {}))
    names = plan_for(passes)
    each = runs_per_question(passes)
    todo = [t for t in tasks(directory) if t["id"] not in done][:max(0, min(budget // each, limit or budget))]
    limit_hit = threading.Event()
    total = len(names)

    def one(task: dict[str, str]) -> Outcome:
        started, cost, runs = time.monotonic(), None, 0
        parts: dict[str, dict[str, Any]] = {}
        failed: dict[str, str] = {}
        items = checklist_for(checklists or {}, task.get("topic", ""))

        def ask(name: str, number: int) -> bool:
            """Run one pass into parts; False (reason in failed) when it gave no usable evidence."""
            nonlocal cost, runs
            need: dict[str, Any] = {}
            if name == "combined":
                text = prompt(task, today, items)
            elif name == "rules":
                text, need = rules_prompt(task, today, total), {"min_sources": 1, "min_facts": 1}
            elif name == "outside":
                text = outside_prompt(task, today, total, number)
            elif name == "inside":
                text, need = inside_prompt(task, today, merge(parts), items, total, number), {"min_sources": 1, "min_facts": 1}
            elif name == "against":
                text, need = against_prompt(task, today, merge(parts), total, number), {
                    "min_sources": 1, "min_facts": 1, "need_against": True}
            elif name == "gap":
                text, need = gap_prompt(task, today, merge(parts), total, number), {"min_sources": 1, "min_facts": 1}
            else:  # the extra primary-source round
                text, need = primary_prompt(task, today, merge(parts)), {"min_sources": 1, "min_facts": 0}
            try:
                runs += 1
                reply = agent(text)
                if isinstance(reply, tuple):
                    reply, spent = reply
                    cost = (cost or 0.0) + (spent or 0.0)
                parts[name] = validate(reply, **need)
                return True
            except Exception as error:  # a failed question is skipped, never the run
                if LIMIT_REACHED.search(str(error)):
                    limit_hit.set()
                failed[name] = f"{type(error).__name__}: {error}"[:300]
                return False

        for number, name in enumerate(names, 1):
            if limit_hit.is_set():
                if not parts:
                    return Outcome("skipped: the research model's usage limit was reached earlier in this run",
                                   None, None, runs, failed)
                failed[name] = "skipped: the research model's usage limit was reached"
                continue
            if not ask(name, number) and name in ("outside", "combined") and not set(parts) - {"rules"}:
                # the outside (or combined) pass is the base of the rest: without it the question has no evidence
                return Outcome(failed[name], round(time.monotonic() - started, 1), cost, runs, failed)
        if not parts:
            return Outcome(next(iter(failed.values()), "no evidence"), round(time.monotonic() - started, 1), cost, runs, failed)
        merged = merge(parts)
        if live_counts.needs_tally(merged, task, today):
            # A running count that was not read is a blind guess (2-5% on questions that then resolved yes): unresearched.
            return Outcome(live_counts.NO_TALLY, round(time.monotonic() - started, 1), cost, runs, failed)
        warnings: list[str] = []
        if "rules" in names:  # the primary-source gate
            if primary_count(merged) < MIN_PRIMARY and not limit_hit.is_set():
                if ask("primary", total + 1):
                    merged = merge(parts)
            elif primary_count(merged) < MIN_PRIMARY:
                failed["primary"] = "skipped: the research model's usage limit was reached"
            if primary_count(merged) < MIN_PRIMARY:
                warnings.append(f"only {primary_count(merged)} primary source(s) with quotes (need {MIN_PRIMARY}): "
                                "rely on the facts less than on official data")
            merged["primary_sources"] = primary_count(merged)
        if not any(src["quotes"] for src in merged["sources"]):
            warnings.append("no source has a quote: the pages could not be opened, so the facts rest on search "
                            "summaries; rely on them less")
        if warnings:
            merged["warnings"] = warnings
        return Outcome(merged, round(time.monotonic() - started, 1), cost, runs, failed)

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
                                "quotes": s["quotes"], "text": "\n".join(s["quotes"]), "verified": None,
                                "primary": is_primary(s, (result.get("resolution") or {}).get("settling_source", ""))}
                               for s in result["sources"]]
        findings[task["id"]] = {"facts": result["facts"], "base_rate": result["base_rate"],
                                "different_now": result["different_now"], "against": result["against"],
                                "uncertainties": result["uncertainties"], "passes_done": result["passes_done"],
                                **{k: result[k] for k in ("tally", "spot", "resolution", "warnings") if result.get(k)},
                                **({"primary_sources": result["primary_sources"]} if "primary_sources" in result else {}),
                                **({"passes_failed": outcome.failed} if outcome.failed else {})}
        if by:
            researched_by[task["id"]] = by
    # "provider" names the file format (research_download.py reads it); researched_by names the model.
    return {"provider": "cursor", "researched_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "runs": sum(o.runs for o in results), "passes": total, "sources": sources, "findings": findings,
            "failed": failed, "researched_by": researched_by, "seconds": seconds, "cost_usd": costs}


def _resolution_lines(found: dict[str, Any]) -> list[str]:
    """A compact "Resolution rules" block from the rules pass, [] when there was none."""
    rules = found.get("resolution") or {}
    rows = [(label, rules.get(key)) for label, key in (("Settled by", "settling_source"), ("Rule", "criteria"),
                                                      ("Status now", "current_status")) if rules.get(key)]
    if rules.get("edge_cases"):
        rows.append(("Edge cases", "; ".join(rules["edge_cases"])))
    return ["Resolution rules:"] + [f"  {label}: {value}" for label, value in rows] if rows else []


def digest(data: dict[str, Any], qid: str, task: dict[str, str] | None = None, today: str = "") -> str:
    """The evidence for one question as a short text for the forecaster: facts, base rate, what differs now, gaps, sources.

    With the question's ``task``, a count/total question opens with its live tally and projection (or a warning that
    none was found) and an asset-price question gets one price-math reference line, both before the facts.
    """
    sources = data.get("sources", {}).get(qid, [])
    found = data.get("findings", {}).get(qid)
    if not found:
        why = data.get("failed", {}).get(qid)
        return f"No research for this question: SKIP it ({why})." if why else "No research for this question."
    lines = []
    if task:
        try:
            lines = live_counts.digest_head(found, task, today or f"{datetime.now(UTC):%Y-%m-%d}")
        except Exception:  # noqa: BLE001 -- the digest must always print
            lines = []
    lines += _resolution_lines(found)
    lines += [f"- {f['fact']} [{f['source'] + 1}]" for f in found["facts"]]
    if found.get("base_rate"):
        lines.append(f"Base rate: {found['base_rate']}")
    if found.get("different_now"):
        lines.append(f"Different this time: {found['different_now']}")
    if found.get("against"):
        lines.append(f"Case for the other outcome: {found['against']}")
    lines += [f"Unknown: {u}" for u in found.get("uncertainties", [])]
    lines += [f"WARNING: {w}" for w in found.get("warnings", [])]
    for n, s in enumerate(sources, 1):
        check = {True: "quotes checked", False: "quotes not found on page"}.get(s.get("verified"), "not checked")
        check += ", primary" if s.get("primary") else ""
        lines.append(f"[{n}] {s['title']} ({s['url']}{', ' + s['published'] if s['published'] else ''}; {check})")
    return "\n".join(lines)

RELATED_DAYS = 30
RELATED_CAP = 6
COHERENCE_LINE = ("Keep your answer coherent with these where they describe the same or nested outcomes; "
                  "if the evidence has changed, say why in the rationale.")
# Words that appear in many questions and say nothing about which outcome is meant.
GENERIC_WORDS = frozenset("""
a an the and or of in on at to for by with from as is are be been will would does do did has have had it its this that
these those than then there their they not no yes any all more most less least over under above below between before
after during until about into out up down off new next last first second third top high low higher lower highest lowest
win wins won winner winners winning lose loses lost beat beats lead leads end ends ended close closes closed reach
reaches hit hits get gets become becomes make makes
election elections race races vote votes voting poll polls primary general nominee nomination candidate party seat seats
senate senator senators house congress governor governors gubernatorial president presidential presidency mayor
republican republicans democrat democrats democratic gop control majority
price prices rate rates cut cuts hike hikes change level close closing open opening percent number total
us usa united states state states america american
market markets year years month months week weeks day days date time today
who what when where which how why
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
monday tuesday wednesday thursday friday saturday sunday mon tue tues wed thu thur thurs fri sat sun
""".split())
ALIASES = {"btc": "bitcoin", "eth": "ethereum", "ether": "ethereum", "sol": "solana", "doge": "dogecoin"}


def distinctive_words(text: str) -> set[str]:
    """Names, places and tickers in a question: capitalized or long-enough tokens that are not generic words."""
    words = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9]+", re.sub(r"[\u2019']s\b", "", text)):
        word = token.lower()
        if word in GENERIC_WORDS:
            continue
        words.add(ALIASES.get(word, word))
    return words


def related_forecasts(track_record: dict[str, Any], task: dict[str, str], today: str,
                      method: str = "claude_code_forecaster_v1", cap: int = RELATED_CAP) -> str:
    """The forecaster's own earlier calls on related questions, so a new answer does not contradict them.

    Related: same topic and at least one distinctive word in common. Only questions still pending, or
    resolved within the last 30 days, and only the forecaster's own probability: never a market price,
    never a judge. Closest resolution date to this question first.
    """
    own = distinctive_words(task["question"])
    topic = (task.get("topic") or "").strip().lower()
    if not own or not topic:
        return ""
    today_date = datetime.strptime(today[:10], "%Y-%m-%d").date()
    mine_date = task.get("resolves_on") or ""
    anchor = datetime.strptime(mine_date[:10], "%Y-%m-%d").date() if mine_date[:10] else today_date
    found = []
    for question in track_record.get("questions", []):
        if question.get("id") == task["id"] or (question.get("topic") or "").strip().lower() != topic:
            continue
        status = question.get("status")
        if status not in ("pending", "resolved") or not question.get("resolves_on"):
            continue
        resolves = datetime.strptime(question["resolves_on"][:10], "%Y-%m-%d").date()
        if status == "resolved" and (today_date - resolves).days > RELATED_DAYS:
            continue
        mine = [f for f in question.get("forecasts", []) if f.get("method") == method]
        if not mine or not own & distinctive_words(question["title"]):
            continue
        latest = max(mine, key=lambda f: f["made_at"])
        found.append((abs((resolves - anchor).days), question["resolves_on"], question["id"], question, latest))
    if not found:
        return ""
    lines = ["Your earlier forecasts on related questions:"]
    for _, _, _, question, forecast in sorted(found, key=lambda row: row[:3])[:cap]:
        state = "pending"
        if question["status"] == "resolved":
            state = f"resolved {'yes' if question.get('outcome') else 'no'}"
        lines.append(f"- {question['title']} You said {forecast['probability']:.0%} yes on {forecast['made_at'][:10]}; "
                     f"resolves {question['resolves_on'][:10]} ({state}).")
    lines.append(COHERENCE_LINE)
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

