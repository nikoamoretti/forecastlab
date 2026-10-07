#!/usr/bin/env python3
"""Answer the owner's questions in the daily Claude run.

  FORECASTLAB_PASSPHRASE=... python scripts/question_requests.py waiting
  python scripts/question_requests.py record <answers.json>
  FORECASTLAB_PASSPHRASE=... python scripts/question_requests.py answered <id> [<id> ...]
  python scripts/question_requests.py outcome <artifact_dir> <id> yes|no --note "4.2%" --source <url>

``waiting`` prints the questions waiting for Claude. ``record`` validates Claude's answers
(``{"forecast_made_at", "answers": [{"id", "question", "topic", "resolution_criteria",
"resolution_date", "resolution_source", "probability", "rationale", "sources"}]}``) and
writes ``artifacts/prospective_requests_<date>/forecasts.json``, which the track record
reads. ``answered`` marks the requests answered in the app. ``outcome`` records how an
asked question resolved; the forecast itself is never changed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("FORECASTLAB_WEB", "https://forecastlab-web.vercel.app")
METHOD = "claude_code_forecaster_v1"
FIELDS = ("id", "question", "topic", "resolution_criteria", "resolution_date", "resolution_source", "probability",
          "rationale", "sources")


class Client:
    """Signs in through the web app like the owner's browser does (origin, cookies, CSRF)."""

    def __init__(self) -> None:
        passphrase = os.environ.get("FORECASTLAB_PASSPHRASE")
        if not passphrase:
            raise SystemExit("Set FORECASTLAB_PASSPHRASE to the owner passphrase")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.call("POST", "/api/auth/code", {"code": passphrase})

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        headers = {"content-type": "application/json", "origin": BASE}
        csrf = next((c.value for c in self.jar if c.name == "forecastlab_csrf"), None)
        if csrf and method != "GET":
            headers["x-csrf-token"] = csrf
        request = urllib.request.Request(BASE + path, method=method, headers=headers,
                                         data=None if body is None else json.dumps(body).encode())
        with self.opener.open(request, timeout=60) as response:
            return json.loads(response.read() or b"null")


def validate(data: dict[str, Any], today: date) -> list[dict[str, Any]]:
    made_at = datetime.fromisoformat(data["forecast_made_at"].replace("Z", "+00:00"))
    questions = []
    for answer in data["answers"]:
        missing = [field for field in FIELDS if answer.get(field) in (None, "", [])]
        if missing:
            raise SystemExit(f"{answer.get('id')}: missing {missing}")
        probability = float(answer["probability"])
        if not 0.02 <= probability <= 0.98:
            raise SystemExit(f"{answer['id']}: probability {probability} outside [0.02, 0.98]")
        if date.fromisoformat(answer["resolution_date"]) <= today:
            raise SystemExit(f"{answer['id']}: resolution date must be after today")
        questions.append({
            "id": answer["id"], "question": answer["question"], "topic": answer["topic"],
            "resolution_criteria": answer["resolution_criteria"], "resolution_date": answer["resolution_date"],
            "resolution_source": answer["resolution_source"], "outcome": None,
            "forecasts": [{"method": METHOD, "probability": probability, "rationale": answer["rationale"],
                           "sources": answer["sources"], "created_at": made_at.isoformat()}]})
    return questions


def record(path: Path, today: date | None = None) -> Path:
    today = today or date.today()
    questions = validate(json.loads(path.read_text()), today)
    directory = ROOT / "artifacts" / f"prospective_requests_{today.strftime('%Y%m%d')}"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "forecasts.json"
    existing = json.loads(target.read_text())["questions"] if target.exists() else []
    known = {q["id"] for q in existing}
    data = {"schema": "owner_requests_v1", "created_at": today.isoformat(), "note": (
        "Questions the owner asked in the app, answered by Claude in the daily run with web research. "
        "Each id matches its question request. Outcomes are recorded after the resolution date."),
            "questions": existing + [q for q in questions if q["id"] not in known]}
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"{directory.name}: {len(data['questions'])} answered questions")
    return target


def set_outcome(directory: Path, question_id: str, outcome: str, note: str, source: str) -> None:
    target = directory / "forecasts.json"
    data = json.loads(target.read_text())
    question = next((q for q in data["questions"] if q["id"] == question_id), None)
    if question is None:
        raise SystemExit(f"{question_id} is not in {target}")
    if date.fromisoformat(question["resolution_date"]) > date.today():
        raise SystemExit(f"{question_id} resolves on {question['resolution_date']}")
    question |= {"outcome": int(outcome == "yes"), "outcome_note": note, "outcome_source": source}
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("waiting")
    commands.add_parser("record").add_argument("answers", type=Path)
    commands.add_parser("answered").add_argument("ids", nargs="+")
    outcome = commands.add_parser("outcome")
    outcome.add_argument("directory", type=Path)
    outcome.add_argument("id")
    outcome.add_argument("result", choices=["yes", "no"])
    outcome.add_argument("--note", required=True)
    outcome.add_argument("--source", required=True)
    args = parser.parse_args(argv)
    if args.command == "waiting":
        print(json.dumps([r for r in Client().call("GET", "/api/question-requests") if r["status"] == "waiting"], indent=2))
    elif args.command == "record":
        record(args.answers)
    elif args.command == "answered":
        client = Client()
        for request_id in args.ids:
            client.call("POST", f"/api/question-requests/{request_id}/answered")
        print(f"marked {len(args.ids)} answered")
    else:
        set_outcome(args.directory, args.id, args.result, args.note, args.source)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
