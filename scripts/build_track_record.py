#!/usr/bin/env python3
"""Write data/track_record.json from the committed prospective artifacts.

  python scripts/build_track_record.py

``GET /api/track-record`` serves this file; the API deployment does not include
``artifacts/``. ``scripts/score_prospective_artifacts.py`` runs this after scoring.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from forecastlab_api.track_record import build_track_record  # noqa: E402

OUTPUT = ROOT / "data" / "track_record.json"


def main() -> int:
    record = build_track_record(ROOT / "artifacts")
    OUTPUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    s = record["summary"]
    print(f"track record: {s['right']} right, {s['wrong']} wrong of {s['resolved']} resolved; "
          f"{s['pending']} open, {s['cancelled']} cancelled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
