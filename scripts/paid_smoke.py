#!/usr/bin/env python3
"""Optional live smoke forecast. Never runs unless explicitly opted in.

Required environment:
  FORECASTLAB_RUN_PAID_SMOKE=1
  live model credentials
  live search credentials
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    if os.environ.get("FORECASTLAB_RUN_PAID_SMOKE") != "1":
        print("Paid live smoke test not executed because explicit opt-in or credentials were absent.")
        return 0
    from forecastlab_api.secrets import load_secrets

    secrets = load_secrets()
    model_ok = bool(secrets.get("model_api_key")) and secrets.get("model_provider") not in {None, "", "mock"}
    search_ok = bool(secrets.get("search_api_key")) and secrets.get("search_provider") not in {None, "", "mock"}
    if not (model_ok and search_ok):
        print("Paid live smoke test not executed because explicit opt-in or credentials were absent.")
        return 0
    print(
        "Opted in with live credentials. Run one binary question through the local API with the smallest "
        "reasonable profile, strict model-call/token/cost limits, and one search call. This script does not "
        "spend money automatically beyond that documented command."
    )
    print(
        "Example: create a demo question, set max_model_calls=2, max_tokens=2000, max_estimated_cost_usd=0.05, "
        "max_search_calls=1, then POST /api/questions/{id}/runs with mode=live."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
