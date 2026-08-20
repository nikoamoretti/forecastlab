#!/usr/bin/env python3
"""Opt-in live smoke. Does nothing unless FORECASTLAB_RUN_PAID_SMOKE=1 and live keys exist.

  FORECASTLAB_RUN_PAID_SMOKE=1 python scripts/paid_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from forecastlab_api.paid_smoke import main

if __name__ == "__main__":
    sys.exit(main())
