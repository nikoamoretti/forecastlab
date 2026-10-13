"""Download every page cursor-agent cited and check its quotes (see forecastlab.cursor_research).

    research_download.py run artifacts/<dir> [...]

Runs as the "download" step of the "Daily run steps" workflow, after the Mac job pushes
research.json. Each source gets the page's own text (HTML or PDF) around its quotes, cleaned of
market names and odds, and ``verified``: True when every quote is on the page, False when the page
was read but a quote is not on it, None when the page could not be downloaded (the quotes stay).
"""

from __future__ import annotations

import io
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import trafilatura
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))

from forecastlab.research import RESEARCH_FILE, TEXT_CHARS, clean  # noqa: E402
from forecastlab.timeutil import utcnow  # noqa: E402

AGENT = "ForecastLab research check (+https://github.com/nikoamoretti/forecastlab)"
WINDOW = 1200  # characters kept on each side of a quote
Fetch = Callable[[str], tuple[str, bytes]]  # url -> (content type, body)


def normal(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def page_text(content_type: str, body: bytes) -> str:
    if "pdf" in content_type or body[:5] == b"%PDF-":
        reader = PdfReader(io.BytesIO(body))
        return "\n".join(page.extract_text() or "" for page in reader.pages[:40])
    return trafilatura.extract(body.decode("utf-8", errors="replace"), include_tables=True) or ""


def excerpt(text: str, quotes: list[str]) -> tuple[str, bool]:
    """The page text around its quotes, within TEXT_CHARS, and whether every quote was found."""
    flat = normal(text)
    spans, found = [], 0
    for quote in quotes:
        at = flat.find(normal(quote))
        if at >= 0 and normal(quote):
            found += 1
            share = at / max(1, len(flat))  # positions in the normalised text map roughly onto the page
            centre = int(share * len(text))
            spans.append((max(0, centre - WINDOW), centre + WINDOW))
    if not spans:
        return text[:TEXT_CHARS], False
    spans.sort()
    merged = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return "\n[...]\n".join(text[a:b] for a, b in merged)[:TEXT_CHARS], found == len(quotes)


def check_source(source: dict[str, Any], fetch: Fetch) -> dict[str, Any]:
    try:
        content_type, body = fetch(source["url"])
        text = clean(page_text(content_type, body))
    except Exception as error:  # an unreadable page keeps its quotes, marked unchecked
        return {**source, "verified": None, "download_error": f"{type(error).__name__}: {error}"[:200]}
    if len(text) < 200:
        return {**source, "verified": None, "download_error": "no readable text on the page"}
    around, verified = excerpt(text, source.get("quotes", []))
    quoted = "\n".join(source.get("quotes", []))
    return {**source, "verified": verified, "text": (quoted + "\n\n" + around if quoted else around)[:TEXT_CHARS]}


def run(directories: list[Path], fetch: Fetch) -> None:
    for directory in directories:
        path = directory / RESEARCH_FILE
        data = json.loads(path.read_text())
        if data.get("provider") != "cursor":
            raise SystemExit(f"{directory.name}: research.json is not from the cursor-agent job")
        checked = {qid: [check_source(s, fetch) for s in items] for qid, items in data["sources"].items()}
        data.update(sources=checked, downloaded_at=utcnow().isoformat())
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        for qid, items in sorted(checked.items()):
            ok = sum(s["verified"] is True for s in items)
            missing = sum(s["verified"] is None for s in items)
            print(f"{directory.name}/{qid}: {len(items)} sources, {ok} with every quote found, {missing} not downloaded")


def http_fetch(client: httpx.Client) -> Fetch:
    def fetch(url: str) -> tuple[str, bytes]:
        response = client.get(url)
        response.raise_for_status()
        return response.headers.get("content-type", ""), response.content
    return fetch


def main(argv: list[str]) -> None:
    if len(argv) < 2 or argv[0] != "run":
        raise SystemExit(__doc__)
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": AGENT}) as client:
        run([ROOT / d for d in argv[1:]], http_fetch(client))


if __name__ == "__main__":
    main(sys.argv[1:])
