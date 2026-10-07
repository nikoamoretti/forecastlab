#!/usr/bin/env python3
"""Daily zero-cost forecasting set: deterministic questions, the statistical
baseline, and Claude's own forecasts, stored as a scoreable artifact.

  python scripts/daily_forecasts.py prepare            # writes artifacts/prospective_daily_<date>/
  python scripts/daily_forecasts.py record <dir> <claude_forecasts.json>

``prepare`` applies the current ``FAST_SELECTION_VERSION`` (at most one jobless
claims and one week-ahead 10-year yield question, thresholds at the latest
observed value) to fresh keyless FRED data. It proposes nothing for an indicator
that already has an unreleased question in any artifact, drops questions already
present in any earlier artifact, and computes
``statistical_baseline_v1`` for each. It writes ``frozen_manifest.json`` and
``cohort_report.json`` in the shape ``scripts/score_prospective_artifacts.py``
scores, plus ``questions.json`` with each question's text and forecast cutoff.

``record`` validates a forecaster's JSON (``{"method", "forecast_made_at",
"forecasts": [{"entry_id", "probability", "rationale", "sources"}]}``). Every
entry must be covered, probabilities must lie in [0.02, 0.98], and
``forecast_made_at`` must precede every entry's cutoff. It then writes
``claude_code_supplement.json``, which the scorer reports as its own method.
Nothing here calls a paid API.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from forecastlab.fast_questions import FAST_SELECTION_VERSION, propose_fast_questions  # noqa: E402
from forecastlab.macro import MacroSpec, fetch_fred_snapshot, fetch_macro  # noqa: E402
from forecastlab.plain_questions import question_text  # noqa: E402
from forecastlab.statistical_baseline import (  # noqa: E402
    METHOD as BASELINE,
)
from forecastlab.statistical_baseline import (  # noqa: E402
    BaselineUnavailable,
    forecast_from_snapshot,
    fred_history_options,
)
from forecastlab.timeutil import as_utc, utcnow  # noqa: E402

DAILY_SCHEMA = "prospective_daily_v1"
CLAUDE_METHOD = "claude_code_forecaster_v1"


def entry_id(macro: dict) -> str:
    key = f"{macro['indicator']}|{macro['observation_period']}|{macro['threshold']}|{macro['comparison']}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def existing_targets(artifacts: Path) -> set[tuple[str, str]]:
    targets = set()
    for manifest in artifacts.glob("prospective_*/frozen_manifest.json"):
        for entry in json.loads(manifest.read_text())["manifest"]["entries"]:
            targets.add((entry["macro"]["indicator"], entry["macro"]["observation_period"]))
    return targets


def open_indicators(artifacts: Path, now: datetime) -> set[str]:
    """Indicators with a question in any artifact whose release is still ahead of ``now``."""
    indicators = set()
    for manifest in artifacts.glob("prospective_*/frozen_manifest.json"):
        for entry in json.loads(manifest.read_text())["manifest"]["entries"]:
            if as_utc(datetime.fromisoformat(entry["macro"]["release_at"].replace("Z", "+00:00"))) > now:
                indicators.add(entry["macro"]["indicator"])
    return indicators


def prepare(now: datetime | None = None, artifacts: Path = ROOT / "artifacts") -> Path | None:
    now = as_utc(now or utcnow())
    snapshots = {name: fetch_fred_snapshot(name, now=now) for name in ("jobless_claims", "treasury_10y")}
    seen = existing_targets(artifacts)
    proposals = [q for q in propose_fast_questions(now, snapshots, open_indicators(artifacts, now))
                 if (q["macro"]["indicator"], q["macro"]["observation_period"]) not in seen]
    if not proposals:
        print("no new questions today")
        return None
    directory = artifacts / f"prospective_daily_{now.strftime('%Y%m%d')}"
    directory.mkdir(parents=True, exist_ok=False)
    entries, questions, cells_by_id = [], [], {}
    for proposal in proposals:
        spec = MacroSpec.model_validate(proposal["macro"])
        identifier = entry_id(proposal["macro"])
        entries.append({"entry_id": identifier, "macro": proposal["macro"], "cutoff": proposal["cutoff"],
                        "release_event": proposal["release_event"]})
        questions.append({"entry_id": identifier, "question": question_text(spec), "cutoff": proposal["cutoff"],
                          "release_at": proposal["macro"]["release_at"], "macro": proposal["macro"]})
        try:
            result = forecast_from_snapshot(spec, fetch_macro(spec, **fred_history_options(spec.indicator)))
            cells_by_id[identifier] = {"method": BASELINE, "status": "forecasted", "probability": result["probability"],
                                       "interval_80": result["interval_80"], "snapshot": result["snapshot"]}
        except BaselineUnavailable as exc:
            cells_by_id[identifier] = {"method": BASELINE, "status": "insufficient_evidence", "probability": None,
                                       "gap": str(exc)}
    manifest = {"schema_version": DAILY_SCHEMA, "selection": FAST_SELECTION_VERSION, "methods": [BASELINE],
                "created_at": now.isoformat(), "entries": entries}
    manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    (directory / "frozen_manifest.json").write_text(json.dumps(
        {"frozen_at": now.isoformat(), "manifest": manifest, "manifest_hash": manifest_hash}, indent=2, sort_keys=True) + "\n")
    report = {"id": directory.name, "manifest_hash": manifest_hash, "methods": [BASELINE],
              "questions": [{"id": e["entry_id"], "release_event": e["release_event"], "cells": [cells_by_id[e["entry_id"]]]}
                            for e in entries],
              "interpretation": "Daily zero-cost set; small, correlated sample."}
    (directory / "cohort_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (directory / "questions.json").write_text(json.dumps(questions, indent=2, sort_keys=True) + "\n")
    print(f"{directory.name}: {len(entries)} questions")
    return directory


def record(directory: Path, forecasts_path: Path) -> Path:
    manifest = json.loads((directory / "frozen_manifest.json").read_text())
    report = json.loads((directory / "cohort_report.json").read_text())
    entries = {e["entry_id"]: e for e in manifest["manifest"]["entries"]}
    data = json.loads(forecasts_path.read_text())
    made_at = as_utc(datetime.fromisoformat(data["forecast_made_at"].replace("Z", "+00:00")))
    forecasts = {f["entry_id"]: f for f in data["forecasts"]}
    if set(forecasts) != set(entries):
        raise SystemExit(f"forecasts must cover exactly the entries: missing {sorted(set(entries) - set(forecasts))}, "
                         f"unknown {sorted(set(forecasts) - set(entries))}")
    events = {q["id"]: q["release_event"] for q in report["questions"]}
    cells = []
    for identifier, forecast in sorted(forecasts.items()):
        probability = float(forecast["probability"])
        if not 0.02 <= probability <= 0.98:
            raise SystemExit(f"{identifier}: probability {probability} outside [0.02, 0.98]")
        cutoff = as_utc(datetime.fromisoformat(entries[identifier]["cutoff"]))
        if made_at >= cutoff:
            raise SystemExit(f"{identifier}: forecast made at {made_at.isoformat()} is not before cutoff {cutoff.isoformat()}")
        cells.append({"entry_id": identifier, "release_event": events[identifier], "status": "forecasted",
                      "probability": probability, "computed_at": made_at.isoformat(),
                      "rationale": forecast.get("rationale", ""), "sources": forecast.get("sources", [])})
    supplement = {"method": data.get("method", CLAUDE_METHOD), "generated_at": made_at.isoformat(),
                  "cohort_id": report["id"], "manifest_hash": report["manifest_hash"],
                  "note": "Recorded before every entry's cutoff; scored as a separate method.", "cells": cells}
    path = directory / "claude_code_supplement.json"
    path.write_text(json.dumps(supplement, indent=2, sort_keys=True) + "\n")
    print(f"{directory.name}: recorded {len(cells)} forecasts")
    return path


def main(argv: list[str]) -> int:
    if argv[:1] == ["prepare"]:
        prepare()
        return 0
    if argv[:1] == ["record"] and len(argv) == 3:
        record(Path(argv[1]), Path(argv[2]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
