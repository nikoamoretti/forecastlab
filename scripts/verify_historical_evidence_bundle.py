#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from forecastlab.historical_evidence_releases import (
    HistoricalEvidenceBundleManifest,
    verify_historical_evidence_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a ForecastLab historical-evidence bundle without network access."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--expected-release-hash", required=True)
    args = parser.parse_args()
    manifest = HistoricalEvidenceBundleManifest.model_validate_json(
        args.manifest.read_text(encoding="utf-8")
    )
    result = verify_historical_evidence_bundle(
        manifest,
        args.bundle_root,
        expected_release_hash=args.expected_release_hash,
    )
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
