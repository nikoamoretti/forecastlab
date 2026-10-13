"""Fetch source documents for the day's questions (see forecastlab.research).

    research_pack.py run artifacts/<dir> [...]

Each directory needs ``research_queries.json``: ``{"queries": {"<question id>": ["query", ...]}}``,
written by Claude. The step writes ``research.json`` next to it. It needs TAVILY_API_KEY and runs
in the "Daily run steps" workflow, since the Claude container cannot reach Tavily.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))

from forecastlab.research import (  # noqa: E402
    MARKET_WORDS,
    MAX_QUERIES_PER_QUESTION,
    MAX_QUERIES_PER_RUN,
    QUERIES_FILE,
    RESEARCH_FILE,
    Search,
    gather,
    tavily_search,
)
from forecastlab.timeutil import utcnow  # noqa: E402


def question_ids(directory: Path) -> set[str]:
    if (directory / "frozen_manifest.json").exists():
        return {entry["entry_id"] for entry in json.loads((directory / "questions.json").read_text())}
    return {q["id"] for q in json.loads((directory / "forecasts.json").read_text())["questions"]}


def load_queries(directory: Path) -> dict[str, list[str]]:
    path = directory / QUERIES_FILE
    if not path.exists():
        raise SystemExit(f"{directory.name}: {QUERIES_FILE} is missing")
    queries: dict[str, list[str]] = json.loads(path.read_text())["queries"]
    unknown = set(queries) - question_ids(directory)
    if unknown:
        raise SystemExit(f"{directory.name}: no such questions: {', '.join(sorted(unknown))}")
    for qid, items in queries.items():
        if not items or any(not q.strip() for q in items):
            raise SystemExit(f"{directory.name}/{qid}: queries must be non-empty")
        if any(MARKET_WORDS.search(q) for q in items):
            raise SystemExit(f"{directory.name}/{qid}: a query looks for prediction markets or odds")
    return {qid: items[:MAX_QUERIES_PER_QUESTION] for qid, items in queries.items()}


def run(directories: list[Path], search: Search) -> int:
    plans = {directory: load_queries(directory) for directory in directories}
    planned = sum(len(items) for queries in plans.values() for items in queries.values())
    if planned > MAX_QUERIES_PER_RUN:
        raise SystemExit(f"{planned} queries planned; the cap is {MAX_QUERIES_PER_RUN} per run")
    for directory, queries in plans.items():
        found = {qid: gather(items, search) for qid, items in queries.items()}
        (directory / RESEARCH_FILE).write_text(json.dumps({
            "fetched_at": utcnow().isoformat(), "provider": "tavily", "queries": queries,
            "sources": {qid: [asdict(s) for s in sources] for qid, sources in found.items()},
        }, indent=2, sort_keys=True) + "\n")
        for qid, sources in found.items():
            print(f"{directory.name}/{qid}: {len(sources)} sources from {len(queries[qid])} queries")
    print(f"{planned} searches")
    return planned


def main(argv: list[str]) -> None:
    if len(argv) < 2 or argv[0] != "run":
        raise SystemExit(__doc__)
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        raise SystemExit("TAVILY_API_KEY is not set")
    with httpx.Client(timeout=60) as client:
        run([ROOT / d for d in argv[1:]], tavily_search(key, client.post))


if __name__ == "__main__":
    main(sys.argv[1:])
