"""Questions borrowed from Polymarket, resolved by Polymarket.

Selection is a fixed rule (``SELECTION_VERSION``): yes/no markets on economics, finance,
politics, geopolitics, world affairs, technology and crypto; closing in 2 to 21 days; at
least $10,000 traded; a price between 5% and 95% (near-certain questions say nothing
about skill); one market per Polymarket event, so siblings such as "who will be prime
minister" do not crowd the sample; topics taken in turn, at most ``TOPIC_LIMIT`` per topic
and ``DAILY_LIMIT`` in all a day.

The market price at selection time is kept as a benchmark forecaster. It is never part of
our call. A question resolves only when Polymarket's resolution is final: the market is
closed, one outcome pays 1, and the last UMA resolution status is "resolved".
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

SELECTION_VERSION = "polymarket_selection_v1"
MARKET_METHOD = "market_price_v1"
GAMMA = "https://gamma-api.polymarket.com"
USER_AGENT = "ForecastLab/0.3 (+https://github.com/nikoamoretti/forecastlab)"
TOPICS = {"economy": "Economy", "finance": "Economy", "politics": "Politics", "geopolitics": "World",
          "world": "World", "tech": "Tech", "crypto": "Crypto"}
MIN_DAYS, MAX_DAYS = 2, 21
MIN_VOLUME = 10_000
MIN_PRICE, MAX_PRICE = 0.05, 0.95
DAILY_LIMIT = 8
TOPIC_LIMIT = 2

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
                        or not MIN_PRICE <= price <= MAX_PRICE):
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
