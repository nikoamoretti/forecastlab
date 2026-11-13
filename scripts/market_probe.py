#!/usr/bin/env python3
"""Read-only check that the market APIs answer from wherever this runs (rule: forecastlab.market_questions).

  python scripts/market_probe.py

One small request per API and per set of request headers. For each it prints the HTTP status, the response's content
type and, for a refusal, the first characters of the error text. A response that came back fine is never printed, so no
market price reaches a log. Use it when ``prepare`` or ``resolve`` cannot reach a market (2026-11-13: Polymarket
answered HTTP 406 to every request from GitHub's runners). It always exits 0: it reports, it does not judge.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))

from forecastlab.market_questions import (  # noqa: E402
    GAMMA,
    KALSHI,
    MANIFOLD,
    MAX_DAYS,
    MIN_DAYS,
    REQUEST_HEADERS,
    USER_AGENT,
)
from forecastlab.timeutil import utcnow  # noqa: E402

# The first entry is what the pipeline sends; the others show which part of a request a refusal depends on.
HEADER_SETS: dict[str, dict[str, str]] = {
    "pipeline": REQUEST_HEADERS,
    "accept json only": {"User-Agent": USER_AGENT, "Accept": "application/json"},
    "accept anything": {"User-Agent": USER_AGENT, "Accept": "*/*"},
    "no accept header": {"User-Agent": USER_AGENT},
    "python default agent": {"Accept": "application/json"},
}


def targets() -> dict[str, str]:
    now = utcnow()
    window = {"closed": "false", "active": "true", "limit": "1", "order": "volume", "ascending": "false",
              "end_date_min": (now + timedelta(days=MIN_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "end_date_max": (now + timedelta(days=MAX_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ"), "tag_slug": "economy"}
    return {
        "polymarket events (selection)": f"{GAMMA}/events?{urllib.parse.urlencode(window)}",
        "polymarket markets (results)": f"{GAMMA}/markets?limit=1&closed=true",
        "manifold search": f"{MANIFOLD}/search-markets?limit=1",
        "kalshi series": f"{KALSHI}/series?limit=1",
    }


def probe(url: str, headers: dict[str, str]) -> str:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            size = len(response.read())
            return f"HTTP {response.status}, {response.headers.get('Content-Type', 'no content type')}, {size} bytes"
    except urllib.error.HTTPError as exc:
        text = " ".join(exc.read(300).decode("utf-8", "replace").split())
        server = exc.headers.get("Server", "?")
        return f"HTTP {exc.code} {exc.reason}, {exc.headers.get('Content-Type', 'no content type')}, server {server}: {text[:200]}"
    except Exception as exc:  # noqa: BLE001 - the point is to see what fails
        return f"{type(exc).__name__}: {exc}"


def main() -> int:
    for name, url in targets().items():
        print(f"{name}: {url.split('?')[0]}")
        for label, headers in HEADER_SETS.items():
            print(f"    {label:22} {probe(url, headers)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
