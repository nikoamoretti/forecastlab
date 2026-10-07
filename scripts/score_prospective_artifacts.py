#!/usr/bin/env python3
"""Score exported prospective cohort artifacts once official values are published.

  python scripts/score_prospective_artifacts.py                 # every artifacts/prospective_* directory
  python scripts/score_prospective_artifacts.py artifacts/<dir>  # one batch

Writes ``scores.json`` next to each artifact, then rebuilds ``data/track_record.json``.
Unpublished questions stay pending.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "scripts"))

from build_track_record import main as build_track_record  # noqa: E402

from forecastlab_api.artifact_scoring import write_scores  # noqa: E402


def main(argv: list[str]) -> int:
    directories = [Path(arg) for arg in argv] or sorted(
        path.parent for path in (ROOT / "artifacts").glob("prospective_*/cohort_report.json"))
    for directory in directories:
        scores = write_scores(directory)
        print(f"{directory.name}: {scores['resolved_entries']}/{scores['total_entries']} questions resolved")
        for method, summary in scores["methods"].items():
            brier = summary["mean_brier"]
            print(f"  {method}: {summary['forecasted']}/{summary['assigned']} forecasts, "
                  f"{summary['resolved_forecasts']} scored, mean Brier "
                  f"{'n/a' if brier is None else f'{brier:.3f}'} (50% guess = 0.250)")
    return build_track_record()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
