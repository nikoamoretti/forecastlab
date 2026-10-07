"""Questions borrowed from Polymarket, resolved by Polymarket.

Selection is a fixed rule (``SELECTION_VERSION``): yes/no markets on economics, finance,
politics, geopolitics, world affairs, technology and crypto; closing in 2 to 21 days; at
least $10,000 traded; a price between 5% and 95% (near-certain questions say nothing
about skill); one market per Polymarket event, so siblings such as "who will be prime
minister" do not crowd the sample; topics taken in turn, at most ``TOPIC_LIMIT`` per topic
and ``DAILY_LIMIT`` in all a day.

v2 (2026-10-07) also skips "who will be the next prime minister/president" markets. Their
trading closes on election day, but they settle only when someone takes office, often
months later, so they would sit unresolved long past their listed date.

Manifold (``MANIFOLD_SELECTION_VERSION``, added 2026-10-07) adds up to ``MANIFOLD_DAILY_LIMIT``
more questions a day from Manifold's economics, politics, technology and world topics, under
the same window, price band and topic cap. Manifold trades play money, so it needs at least
``MANIFOLD_MIN_BETTORS`` traders and ``MANIFOLD_MIN_VOLUME`` mana traded, and skips personal
markets ("Will I ...") whose creator decides the answer about themselves. Its price is a
separate benchmark (``MANIFOLD_METHOD``). A Manifold question resolves when the market
resolves YES or NO; MKT or CANCEL counts as cancelled.

The market price at selection time is kept as a benchmark forecaster. It is never part of
our call. A question resolves only when Polymarket's resolution is final: the market is
closed, one outcome pays 1, and the last UMA resolution status is "resolved".
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

SELECTION_VERSION = "polymarket_selection_v2"
MARKET_METHOD = "market_price_v1"
GAMMA = "https://gamma-api.polymarket.com"
USER_AGENT = "ForecastLab/0.3 (+https://github.com/nikoamoretti/forecastlab)"
TOPICS = {"economy": "Economy", "finance": "Economy", "politics": "Politics", "geopolitics": "World",
          "world": "World", "tech": "Tech", "crypto": "Crypto"}
MIN_DAYS, MAX_DAYS = 2, 21
MIN_VOLUME = 10_000
MIN_PRICE, MAX_PRICE = 0.05, 0.95
DAILY_LIMIT = 8
# Markets that settle when someone takes office, not when trading closes.
SLOW_SETTLING = re.compile(r"\bnext (prime minister|president|chancellor|premier|pm|leader|speaker)\b", re.IGNORECASE)
TOPIC_LIMIT = 2

MANIFOLD_SELECTION_VERSION = "manifold_selection_v1"
MANIFOLD_METHOD = "manifold_price_v1"
MANIFOLD = "https://api.manifold.markets/v0"
MANIFOLD_TOPICS = {"economics-default": "Economy", "politics-default": "Politics", "technology-default": "Tech",
                   "world-default": "World"}
MANIFOLD_MIN_BETTORS = 15
MANIFOLD_MIN_VOLUME = 1_000
MANIFOLD_DAILY_LIMIT = 4
# Markets about the creator themselves ("Will I get the job?"), which the creator resolves.
PERSONAL = re.compile(r"\b(I|I'm|I'll|me|my)\b")

Fetch = Callable[[str], Any]


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _yes_price(market: dict[str, Any]) -> float | None:
    outcomes, prices = json.loads(market.get("outcomes") or "[]"), json.loads(market.get("outcomePrices") or "[]")
    if outcomes != ["Yes", "No"] or len(prices) != 2:
        return None
    return float(prices[0])


def candidates(now: datetime, fetch: Fetch = fetch_json) -> list[dict[str, Any]]:
    """Every market that passes the rule, one per event, with its topic and event link."""
    window = {"closed": "false", "active": "true", "limit": "100", "order": "volume", "ascending": "false",
              "end_date_min": _iso(now + timedelta(days=MIN_DAYS)), "end_date_max": _iso(now + timedelta(days=MAX_DAYS))}
    found: dict[str, dict[str, Any]] = {}
    for tag, topic in TOPICS.items():
        for event in fetch(f"{GAMMA}/events?{urllib.parse.urlencode(window | {'tag_slug': tag})}"):
            if event["id"] in found:
                continue
            eligible = []
            for market in event.get("markets", []):
                price = _yes_price(market)
                volume = float(market.get("volumeNum") or 0)
                closes = market.get("endDate")
                if (market.get("closed") or price is None or not closes or volume < MIN_VOLUME
                        or not MIN_PRICE <= price <= MAX_PRICE or SLOW_SETTLING.search(market.get("question") or "")):
                    continue
                if not now + timedelta(days=MIN_DAYS) <= _parse_time(closes) <= now + timedelta(days=MAX_DAYS):
                    continue
                eligible.append((volume, market, price))
            if eligible:
                volume, market, price = max(eligible, key=lambda item: item[0])
                found[event["id"]] = {
                    "id": f"polymarket-{market['id']}", "market_id": str(market["id"]), "topic": topic, "tag": tag,
                    "question": market["question"].strip(), "resolution_criteria": (market.get("description") or "").strip(),
                    "closes_at": market["endDate"], "volume": volume, "market_price": price,
                    "market_url": f"https://polymarket.com/event/{event['slug']}"}
    return list(found.values())


def select(pool: list[dict[str, Any]], seen: set[str], limit: int = DAILY_LIMIT) -> list[dict[str, Any]]:
    """Take topics in turn, the most traded market first within each topic, skipping known ones."""
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for item in sorted(pool, key=lambda c: -c["volume"]):
        if item["id"] not in seen and len(by_topic.setdefault(item["topic"], [])) < TOPIC_LIMIT:
            by_topic[item["topic"]].append(item)
    chosen: list[dict[str, Any]] = []
    while len(chosen) < limit and any(by_topic.values()):
        for topic in sorted(by_topic):
            if by_topic[topic] and len(chosen) < limit:
                chosen.append(by_topic[topic].pop(0))
    return chosen


def resolution(market_id: str, fetch: Fetch = fetch_json) -> dict[str, Any]:
    """{"status": "resolved", "outcome": 1|0} once final; "cancelled" for a 50/50 settlement; else pending."""
    market = fetch(f"{GAMMA}/markets/{market_id}")
    statuses = market.get("umaResolutionStatuses") or "[]"
    final = (json.loads(statuses) if isinstance(statuses, str) else statuses or [])[-1:] == ["resolved"]
    prices = [float(p) for p in json.loads(market.get("outcomePrices") or "[]")]
    if not (market.get("closed") and final and len(prices) == 2):
        return {"status": "pending"}
    if prices == [1.0, 0.0]:
        return {"status": "resolved", "outcome": 1}
    if prices == [0.0, 1.0]:
        return {"status": "resolved", "outcome": 0}
    return {"status": "cancelled"}


def manifold_candidates(now: datetime, fetch: Fetch = fetch_json) -> list[dict[str, Any]]:
    """Manifold yes/no markets that pass the rule, in the same shape as Polymarket candidates."""
    start, end = now + timedelta(days=MIN_DAYS), now + timedelta(days=MAX_DAYS)
    found: dict[str, dict[str, Any]] = {}
    for slug, topic in MANIFOLD_TOPICS.items():
        query = {"term": "", "filter": "open", "contractType": "BINARY", "sort": "close-date", "topicSlug": slug,
                 "limit": "500"}
        for market in fetch(f"{MANIFOLD}/search-markets?{urllib.parse.urlencode(query)}"):
            if market["id"] in found or market.get("isResolved") or market.get("outcomeType") != "BINARY":
                continue
            closes, price = market.get("closeTime"), market.get("probability")
            question = (market.get("question") or "").strip()
            if not isinstance(closes, int | float) or not isinstance(price, int | float):
                continue
            closes_at = datetime.fromtimestamp(closes / 1000, tz=now.tzinfo)
            if (not start <= closes_at <= end or not MIN_PRICE <= price <= MAX_PRICE
                    or int(market.get("uniqueBettorCount") or 0) < MANIFOLD_MIN_BETTORS
                    or float(market.get("volume") or 0) < MANIFOLD_MIN_VOLUME
                    or PERSONAL.search(question) or SLOW_SETTLING.search(question)):
                continue
            found[market["id"]] = {
                "id": f"manifold-{market['id']}", "market_id": str(market["id"]), "topic": topic, "tag": slug,
                "question": question, "closes_at": _iso(closes_at), "volume": float(market["volume"]),
                "market_price": round(float(price), 4), "market_url": market["url"], "method": MANIFOLD_METHOD}
    return list(found.values())


def manifold_rules(item: dict[str, Any], fetch: Fetch = fetch_json) -> dict[str, Any]:
    """Search results carry no resolution rules; the market itself does. Fetched for chosen questions only."""
    detail = fetch(f"{MANIFOLD}/market/{item['market_id']}")
    rules = (detail.get("textDescription") or "").strip()
    return item | {"resolution_criteria": rules or f"Resolves as the Manifold market resolves: {item['market_url']}"}


def manifold_resolution(market_id: str, fetch: Fetch = fetch_json) -> dict[str, Any]:
    """{"status": "resolved", "outcome": 1|0} for YES/NO; "cancelled" for MKT or CANCEL; else pending."""
    market = fetch(f"{MANIFOLD}/market/{market_id}")
    if not market.get("isResolved"):
        return {"status": "pending"}
    resolved = market.get("resolution")
    if resolved == "YES":
        return {"status": "resolved", "outcome": 1}
    if resolved == "NO":
        return {"status": "resolved", "outcome": 0}
    return {"status": "cancelled"}
