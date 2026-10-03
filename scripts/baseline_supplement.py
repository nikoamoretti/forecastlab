#!/usr/bin/env python3
"""Add post-freeze statistical-baseline forecasts to an exported prospective cohort.

  python scripts/baseline_supplement.py artifacts/<dir> [...]

Cohorts frozen before ``statistical_baseline_v1`` existed have no baseline
assignment. This computes the deterministic baseline for each exported entry
from the official data available now and writes ``baseline_supplement.json``.
It refuses to run after any entry's release time, and records the snapshot hash,
last observation and computation time, so a reader can check that no newer
observation than the cohort saw was available. It is scored as a separate,
clearly labeled method and never alters the frozen manifest.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from forecastlab.macro import MacroSpec, fetch_macro  # noqa: E402
from forecastlab.statistical_baseline import (  # noqa: E402
    METHOD,
    RULE_VERSION,
    BaselineUnavailable,
    forecast_from_snapshot,
    fred_history_options,
)
from forecastlab.timeutil import as_utc, utcnow  # noqa: E402


def supplement(directory: Path) -> dict:
    manifest = json.loads((directory / "frozen_manifest.json").read_text())["manifest"]
    report = json.loads((directory / "cohort_report.json").read_text())
    events = {question["id"]: question["release_event"] for question in report["questions"]}
    now = utcnow()
    cells = []
    for entry in manifest["entries"]:
        spec = MacroSpec.model_validate(entry["macro"])
        if as_utc(spec.release_at) <= now:
            raise SystemExit(f"{directory.name}: {entry['entry_id']} already released; a supplement would see the outcome")
        cell = {"entry_id": entry["entry_id"], "release_event": events[entry["entry_id"]], "computed_at": now.isoformat()}
        try:
            result = forecast_from_snapshot(spec, fetch_macro(spec, **fred_history_options(spec.indicator)))
            cell |= {"status": "forecasted", "probability": result["probability"], "n": result["n"], "k": result["k"],
                     "interval_80": result["interval_80"], "mode": result["mode"],
                     "last_observation": result["last_observation"], "snapshot": result["snapshot"]}
        except BaselineUnavailable as exc:
            cell |= {"status": "insufficient_evidence", "probability": None, "gap": str(exc)}
        cells.append(cell)
    return {"method": METHOD, "rule_version": RULE_VERSION, "cohort_id": report["id"],
            "manifest_hash": report["manifest_hash"], "generated_at": now.isoformat(),
            "note": "Computed after the cohort froze, before any entry's release. Not part of the frozen manifest; "
                    "scored separately as a post-freeze supplement.", "cells": cells}


def main(argv: list[str]) -> int:
    for arg in argv:
        directory = Path(arg)
        data = supplement(directory)
        (directory / "baseline_supplement.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        print(f"{directory.name}: {sum(c['probability'] is not None for c in data['cells'])}/{len(data['cells'])} baseline forecasts")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
