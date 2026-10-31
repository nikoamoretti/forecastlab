"""Learn from the forecaster's misses (see docs/CURSOR_RESEARCH.md, "Learning from misses").

    miss_review.py export [--since YYYY-MM-DD] [--out FILE]
    miss_review.py apply <review.json>

``export`` prints a compact packet (JSON) of Claude's misses: every resolved, scored question it got wrong,
and any resolved forecast with a squared error of 0.36 or more (a call of 60 points or more off), each with
the rule, Claude's probability and rationale, the outcome and the research it had (base rate, what was
different, facts, the passes that ran). By default it lists misses since the newest review in
``data/miss_reviews/`` that are not yet in any review; ``--since`` lists every miss resolving on or after a
date. Nothing in it is a market price, and the research files it reads carry none.

``apply`` takes a review written from that packet:

    {"reviewed_on": "YYYY-MM-DD",            # optional, today if left out
     "summary": "optional, one or two sentences",
     "misses": [{"id": "<question id>", "cause": "<one of CAUSES>", "note": "one line"}],
     "checklist_items": {"<topic as in the questions>": ["one concrete research instruction", ...]}}

It checks the review, saves it as ``data/miss_reviews/<date>.json`` and merges the new checklist items into
``configs/research_checklists.json`` (duplicates skipped, at most MAX_CHECKLIST_ITEMS per topic, the oldest
dropped first). Pass 2 of the next research run reads those items.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))

from forecastlab.cursor_research import CHECKLIST_FILE, MAX_CHECKLIST_ITEMS  # noqa: E402
from forecastlab.research import MARKET_WORDS, RESEARCH_FILE, clean  # noqa: E402

UTC = timezone.utc  # noqa: UP017 -- matches the research job, which may run on Python 3.9
FORECASTER = "claude_code_forecaster_v1"
TRACK_RECORD = "data/track_record.json"
REVIEWS_DIR = "data/miss_reviews"
BIG_ERROR = 0.36  # squared error; a forecast 0.6 or more away from the outcome
CAUSES = ("stale_data", "wrong_base_rate", "misread_rules", "missed_scheduled_event", "overconfident",
          "unlucky", "other")
MAX_NOTE = 300
MAX_ITEM = 400
MIN_ITEM = 20
MAX_FACTS = 8
ODDS = re.compile(r"\bodds\b", re.IGNORECASE)
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LEGACY_PASSES = ["legacy_single"]  # research saved before the three-pass research kept no passes_done


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def say(text: Any, limit: int) -> str:
    """Text without any sentence that names a market or odds, cut to ``limit`` characters."""
    return " ".join(clean(text if isinstance(text, str) else "").split())[:limit]


def reviews(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Every saved review, oldest first (the file name starts with its date)."""
    folder = root / REVIEWS_DIR
    return [(path, load(path)) for path in sorted(folder.glob("*.json"))] if folder.is_dir() else []


def misses(track: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Resolved, scored questions the forecaster got wrong or missed by squared error BIG_ERROR or more, with its forecast."""
    found = []
    for question in track.get("questions", []):
        if question.get("status") != "resolved" or question.get("scored") is False or question.get("outcome") not in (0, 1):
            continue
        mine = next((f for f in question.get("forecasts", []) if f.get("method") == FORECASTER), None)
        if not mine:
            continue
        p, outcome = float(mine["probability"]), int(question["outcome"])
        if (p != 0.5 and (p > 0.5) != bool(outcome)) or (p - outcome) ** 2 >= BIG_ERROR:
            found.append((question, mine))
    return sorted(found, key=lambda row: (row[0]["resolves_on"], row[0]["id"]))


def research_for(root: Path, source: str, qid: str) -> dict[str, Any] | None:
    """The research the forecaster had for a question, from the directory's research.json or the site export."""
    path = root / "artifacts" / source / RESEARCH_FILE
    if path.exists():
        data = load(path)
        found = data.get("findings", {}).get(qid)
        if found:
            checked = [s.get("verified") for s in data.get("sources", {}).get(qid, [])]
            return shape(found, found.get("passes_done"), data.get("researched_by", {}).get(qid) or data.get("model"),
                         [fact["fact"] for fact in found.get("facts", [])], found.get("uncertainties", []),
                         f"{sum(v is True for v in checked)} of {len(checked)} sources with every quote found")
    path = root / "data" / "research" / f"{source}.json"
    found = (load(path) if path.exists() else {}).get(qid)
    if found and (found.get("facts") or found.get("base_rate")):
        return shape(found, found.get("passes_done"), found.get("researched_by"),
                     [fact["fact"] for fact in found.get("facts", [])], found.get("unknowns", []),
                     f"{sum(s.get('verified') is True for s in found.get('sources', []))} of "
                     f"{len(found.get('sources', []))} sources with every quote found")
    return None


def shape(found: dict[str, Any], passes: Any, by: Any, facts: list[str], unknowns: list[str], checked: str) -> dict[str, Any]:
    return {"researched_by": by, "passes_ran": passes or LEGACY_PASSES, "base_rate": say(found.get("base_rate"), 600),
            "different_now": say(found.get("different_now"), 600), "against": say(found.get("against"), 900),
            "facts": [say(f, 300) for f in facts[:MAX_FACTS]], "unknowns": [say(u, 200) for u in unknowns[:5]],
            "sources_checked": checked}


def export(root: Path, since: str | None = None, today: str | None = None) -> dict[str, Any]:
    """The review packet: the misses to look at, and the checklists they would change."""
    track = load(root / TRACK_RECORD)
    saved = reviews(root)
    # By default: after the newest review, and not any miss a review already covers. An explicit --since lists them all.
    reviewed = {m["id"] for _, review in saved for m in review.get("misses", [])} if since is None else set()
    if since is None and saved:
        since = saved[-1][0].name[:10]
    found = []
    for question, mine in misses(track):
        p = float(mine["probability"])
        if (since and question["resolves_on"] < since) or question["id"] in reviewed:
            continue
        outcome = int(question["outcome"])
        actual = re.sub(r"^(Polymarket|Manifold|Kalshi|Metaculus):\s*", "", question.get("actual") or "")
        found.append({"id": question["id"], "date": question["resolves_on"], "topic": question.get("topic") or "Other",
                      "question": say(question.get("title"), 300), "resolution_rule": say(question.get("detail"), 1200),
                      "probability": p, "squared_error": round((p - outcome) ** 2, 3),
                      "rationale": say(mine.get("rationale"), 1500),
                      "outcome": "yes" if outcome else "no", "actual": say(actual, 120),
                      "research": research_for(root, question.get("source") or "", question["id"])})
    checklist = root / CHECKLIST_FILE
    return {"generated_on": today or datetime.now(UTC).date().isoformat(), "forecaster": FORECASTER,
            "since": since, "criteria": f"wrong calls, and any resolved forecast with squared error >= {BIG_ERROR}",
            "causes": list(CAUSES),
            "answer_with": {"reviewed_on": "YYYY-MM-DD", "summary": "one or two sentences",
                            "misses": [{"id": "<id>", "cause": "<one of causes>", "note": "one line"}],
                            "checklist_items": {"<topic>": ["one concrete research instruction"]}},
            "current_checklists": load(checklist).get("topics", {}) if checklist.exists() else {},
            "misses": found}


def one_line(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    text = " ".join(value.split())
    if len(text) > limit:
        raise ValueError(f"{name} is longer than {limit} characters")
    return text


def check_review(data: Any, questions: dict[str, dict[str, Any]] | None, today: str) -> dict[str, Any]:
    """The review cleaned and checked, or ValueError saying what is wrong with it."""
    if not isinstance(data, dict):
        raise ValueError("the review must be a JSON object")
    reviewed_on = data.get("reviewed_on") or today
    if not isinstance(reviewed_on, str) or not DAY.match(reviewed_on):
        raise ValueError("reviewed_on must be YYYY-MM-DD")
    date.fromisoformat(reviewed_on)
    listed = data.get("misses")
    if not isinstance(listed, list):
        raise ValueError("misses must be a list")
    checked, seen = [], set()
    for n, item in enumerate(listed, 1):
        if not isinstance(item, dict):
            raise ValueError(f"misses[{n}] must be an object")
        qid = item.get("id")
        if not isinstance(qid, str) or not qid:
            raise ValueError(f"misses[{n}] has no id")
        if qid in seen:
            raise ValueError(f"misses[{n}]: {qid} is listed twice")
        seen.add(qid)
        if item.get("cause") not in CAUSES:
            raise ValueError(f"misses[{n}] ({qid}): cause must be one of {', '.join(CAUSES)}")
        entry = {"id": qid, "cause": item["cause"], "note": one_line(item.get("note"), f"misses[{n}] ({qid}) note", MAX_NOTE)}
        if questions is not None:
            question = questions.get(qid)
            if question is None or question.get("status") != "resolved":
                raise ValueError(f"misses[{n}]: {qid} is not a resolved question in {TRACK_RECORD}")
            mine = next((f for f in question.get("forecasts", []) if f.get("method") == FORECASTER), None)
            entry |= {"topic": question.get("topic"), "resolves_on": question.get("resolves_on"),
                      "probability": mine["probability"] if mine else None, "outcome": question.get("outcome")}
        checked.append(entry)
    proposed = data.get("checklist_items") or {}
    if not isinstance(proposed, dict):
        raise ValueError("checklist_items must be an object of topic to list")
    items: dict[str, list[str]] = {}
    for topic, texts in proposed.items():
        name = one_line(topic, "a checklist topic", 80)
        if not isinstance(texts, list):
            raise ValueError(f"checklist_items[{name}] must be a list")
        for text in texts:
            item = one_line(text, f"a checklist item for {name}", MAX_ITEM)
            if len(item) < MIN_ITEM:
                raise ValueError(f"a checklist item for {name} is too short to be a concrete instruction")
            if MARKET_WORDS.search(item) or ODDS.search(item):
                raise ValueError(f"a checklist item for {name} mentions a market or odds")
            items.setdefault(name, []).append(item)
    if not checked and not items:
        raise ValueError("the review has no misses and no checklist items")
    summary = data.get("summary")
    return {"reviewed_on": reviewed_on, "summary": one_line(summary, "summary", 600) if summary else "",
            "misses": checked, "checklist_items": items}


def key(item: str) -> str:
    return " ".join(item.lower().split()).rstrip(".")


def merge_checklists(topics: dict[str, list[str]], proposed: dict[str, list[str]]
                     ) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[str]]]:
    """The checklists with the proposed items added, and what was added and dropped, per topic."""
    merged = {topic: list(items) for topic, items in topics.items()}
    added: dict[str, list[str]] = {}
    dropped: dict[str, list[str]] = {}
    for topic, texts in proposed.items():
        name = next((t for t in merged if t.lower() == topic.lower()), topic)
        current = merged.setdefault(name, [])
        for text in texts:
            if key(text) not in {key(i) for i in current}:
                current.append(text)
                added.setdefault(name, []).append(text)
        while len(current) > MAX_CHECKLIST_ITEMS:
            dropped.setdefault(name, []).append(current.pop(0))
    return merged, added, dropped


def apply(root: Path, review_path: Path, today: str | None = None) -> dict[str, Any]:
    """Check the review, save it under data/miss_reviews and merge its checklist items; returns what was saved."""
    today = today or datetime.now(UTC).date().isoformat()
    track_path = root / TRACK_RECORD
    questions = {q["id"]: q for q in load(track_path).get("questions", [])} if track_path.exists() else None
    review = check_review(load(review_path), questions, today)
    path = root / CHECKLIST_FILE
    saved = load(path) if path.exists() else {"version": 0, "topics": {}}
    merged, added, dropped = merge_checklists(saved.get("topics", {}), review["checklist_items"])
    if added or dropped:
        saved |= {"version": int(saved.get("version", 0)) + 1, "updated": review["reviewed_on"], "topics": merged}
        path.write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n")
    review |= {"checklist_added": added, "checklist_dropped": dropped,
               "checklist_version": saved.get("version"), "saved_on": today}
    folder = root / REVIEWS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{review['reviewed_on']}.json"
    n = 2
    while target.exists():  # a second review on the same day keeps the first
        target = folder / f"{review['reviewed_on']}-{n}.json"
        n += 1
    target.write_text(json.dumps(review, indent=2, ensure_ascii=False) + "\n")
    review["path"] = str(target.relative_to(root))
    return review


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    out = sub.add_parser("export", help="print a review packet of the forecaster's misses")
    out.add_argument("--since", help="list misses resolving on or after YYYY-MM-DD, reviewed or not")
    out.add_argument("--out", help="write the packet to this file instead of printing it")
    app = sub.add_parser("apply", help="check a review, save it and merge its checklist items")
    app.add_argument("review", help="the review JSON file")
    args = parser.parse_args(argv)
    if args.command == "export":
        if args.since and not DAY.match(args.since):
            parser.error("--since must be YYYY-MM-DD")
        text = json.dumps(export(ROOT, args.since), indent=2, ensure_ascii=False) + "\n"
        if args.out:
            Path(args.out).write_text(text)
            print(f"wrote {args.out}")
        else:
            print(text, end="")
        return
    try:
        done = apply(ROOT, Path(args.review))
    except (ValueError, OSError) as error:
        raise SystemExit(f"miss_review: {error}") from None
    counts: dict[str, int] = {}
    for miss in done["misses"]:
        counts[miss["cause"]] = counts.get(miss["cause"], 0) + 1
    print(f"saved {done['path']}: {len(done['misses'])} misses ({', '.join(f'{c} {n}' for c, n in sorted(counts.items())) or 'none'})")
    for topic, items in sorted(done["checklist_added"].items()):
        print(f"  checklist {topic}: +{len(items)}" + (f", dropped {len(done['checklist_dropped'][topic])} oldest"
                                                       if topic in done["checklist_dropped"] else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
