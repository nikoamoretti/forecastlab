"""Questions borrowed from Polymarket, resolved by Polymarket.

Selection is a fixed rule (``SELECTION_VERSION``): yes/no markets on economics, finance,
politics, geopolitics, world affairs, technology and crypto; closing in 2 to 10 days; at
least ``MIN_VOLUME`` ($2,500, since v7) traded; a price between 5% and 95% (near-certain questions say nothing
about skill); one market per Polymarket event, so siblings such as "who will be prime
minister" do not crowd the sample; topics taken in turn, at most ``TOPIC_LIMIT`` per topic
and ``DAILY_LIMIT`` in all a day.

v2 (2026-10-07) also skips "who will be the next prime minister/president" markets. Their
trading closes on election day, but they settle only when someone takes office, often
months later, so they would sit unresolved long past their listed date.

v3 (2026-10-25, owner's call) takes more questions that settle sooner, so results build up
faster: up to 18 a day (was 8), up to 4 per topic (was 2), closing within 10 days (was 21).

v4 (2026-10-27) also skips markets whose rules allow settling much later than trading
closes: if the rules name a deadline more than ``LATE_SETTLING`` after the listed end
date ("the result isn't known by December 31, 2027"), the market can sit unsettled for
months, like the Bulgarian presidential election market that closed on election day but
waits for any runoff.

v5 (2026-11-04) caps clusters. The US midterms put about 34 questions (one per race, each its
own Polymarket event) into one resolution window, and one national swing decided them all, so
a single surprise wrecked the record. A question is now skipped when ``CLUSTER_LIMIT`` (5)
questions in the same topic are still pending with a resolution date within ``CLUSTER_DAYS``
(1) day of its own, counting those chosen earlier in the same run. On top of that, at most
``ASSET_LIMIT`` (2) pending questions may be about the same asset (Bitcoin, Ethereum, gold,
the S&P 500 ..., named in the title, see ``asset_of``) with resolution dates within a day of
each other: several price thresholds on one asset are one bet. Both caps look at the pending
questions committed under ``artifacts/prospective_markets_*``.

v6 (2026-11-09) takes no market from an event that already has a pending question (same ``market_url``) from an earlier
day: sibling markets of one event, selected on different days, counted its single outcome many times.

v7 (2026-11-09) lowers the traded-volume floor from $10,000 to $2,500. The 2026-11-09 pool run showed the fetch was
already limited to the closing window (1,902 of 1,904 fetched markets end inside it), so the floor was the only rule
thinning the pool. Of the markets passing every other rule, 17 traded $10,000 or more (8 events), 13 traded $5,000 to
$10,000 (11 events), 42 traded $2,500 to $5,000 (28 events) and 945 traded under $1,000. A $5,000 floor left 13
candidates, short of the 15 a day needed; $2,500 left 31. Thinner markets are a weaker benchmark: a price on a market
with few traders moves on one order and says less about what a well-informed crowd thinks, so beating it is a lower
bar. Each recorded question therefore keeps the floor it was chosen under (``volume_floor``), so scores can be read
with that in mind.

Manifold (``MANIFOLD_SELECTION_VERSION``, added 2026-10-07; v2 on 2026-10-25 raised the limit
from 4 to 10 under the v3 window and topic cap; v3 on 2026-11-04 adds the v5 cluster and asset caps) adds up to ``MANIFOLD_DAILY_LIMIT``
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
from datetime import UTC, date, datetime, timedelta
from typing import Any

SELECTION_VERSION = "polymarket_selection_v7"
MARKET_METHOD = "market_price_v1"
GAMMA = "https://gamma-api.polymarket.com"
USER_AGENT = "ForecastLab/0.3 (+https://github.com/nikoamoretti/forecastlab)"
TOPICS = {"economy": "Economy", "finance": "Economy", "politics": "Politics", "geopolitics": "World",
          "world": "World", "tech": "Tech", "crypto": "Crypto"}
MIN_DAYS, MAX_DAYS = 2, 10
MIN_VOLUME = 2_500
MIN_PRICE, MAX_PRICE = 0.05, 0.95
DAILY_LIMIT = 18
# Markets that settle when someone takes office, not when trading closes.
SLOW_SETTLING = re.compile(r"\bnext (prime minister|president|chancellor|premier|pm|leader|speaker)\b", re.IGNORECASE)
# Rules that allow settling this long after the listed end date mark a slow-settling market.
LATE_SETTLING = timedelta(days=30)
RULES_DATE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
                        r" (\d{1,2}),? (\d{4})\b")
TOPIC_LIMIT = 4
# Pending questions in one topic resolving within CLUSTER_DAYS of each other hinge on much the same news.
CLUSTER_LIMIT = 5
CLUSTER_DAYS = 1
# Several price thresholds or windows on one asset are one bet.
ASSET_LIMIT = 2
ASSETS = {
    "bitcoin": r"bitcoin|\bbtc\b", "ethereum": r"ethereum|\beth\b|\bether\b", "solana": r"solana|\bsol\b",
    "xrp": r"\bxrp\b|ripple", "dogecoin": r"dogecoin|\bdoge\b", "gold": r"\bgold\b|\bxau\b",
    "silver": r"\bsilver\b", "oil": r"\boil\b|\bwti\b|\bbrent\b|crude",
    "s&p 500": r"s&p|\bspx\b|\bsp500\b", "nasdaq": r"nasdaq|\bndx\b", "dow": r"\bdow jones\b|\bdjia\b",
}
ASSET_PATTERNS = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in ASSETS.items()}
# Fallback: a ticker written as "(NVDA)" or "$NVDA".
TICKER = re.compile(r"\(([A-Z]{1,5})\)|\$([A-Z]{1,5})\b")

MANIFOLD_SELECTION_VERSION = "manifold_selection_v3"
MANIFOLD_METHOD = "manifold_price_v1"
MANIFOLD = "https://api.manifold.markets/v0"
MANIFOLD_TOPICS = {"economics-default": "Economy", "politics-default": "Politics", "technology-default": "Tech",
                   "world-default": "World"}
MANIFOLD_MIN_BETTORS = 15
MANIFOLD_MIN_VOLUME = 1_000
MANIFOLD_DAILY_LIMIT = 10
# Markets about the creator themselves ("Will I get the job?"), which the creator resolves.
PERSONAL = re.compile(r"\b(I|I'm|I'll|me|my)\b")

Fetch = Callable[[str], Any]
# Optional funnel counter: the selection functions add 1 under a key such as "drop:volume" or "select:chosen".
Stats = dict[str, int]


def _count(stats: Stats | None, key: str, amount: int = 1) -> None:
    if stats is not None and amount:
        stats[key] = stats.get(key, 0) + amount


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def settles_late(rules: str, closes: datetime) -> bool:
    """Whether the rules name a deadline well after the listed end date."""
    dates = [datetime.strptime(" ".join(m), "%B %d %Y").replace(tzinfo=UTC) for m in RULES_DATE.findall(rules)]
    return any(date > closes + LATE_SETTLING for date in dates)


def _yes_price(market: dict[str, Any]) -> float | None:
    outcomes, prices = json.loads(market.get("outcomes") or "[]"), json.loads(market.get("outcomePrices") or "[]")
    if outcomes != ["Yes", "No"] or len(prices) != 2:
        return None
    return float(prices[0])


def _polymarket_reject(market: dict[str, Any], price: float | None, volume: float, now: datetime) -> str | None:
    """The first rule a market fails (None if it passes), in the order the rule has always been applied."""
    closes = market.get("endDate")
    if market.get("closed"):
        return "closed"
    if price is None:
        return "not_yes_no"
    if not closes:
        return "no_end_date"
    if volume < MIN_VOLUME:
        return "volume"
    if not MIN_PRICE <= price <= MAX_PRICE:
        return "price_band"
    if SLOW_SETTLING.search(market.get("question") or ""):
        return "next_leader"
    if not now + timedelta(days=MIN_DAYS) <= _parse_time(closes) <= now + timedelta(days=MAX_DAYS):
        return "window"
    if settles_late(market.get("description") or "", _parse_time(closes)):
        return "settles_late"
    return None


# Traded-volume bands (USD) for the pool funnel: how many markets that pass every rule except the volume floor sit in each.
VOLUME_BANDS = (("under_1k", 0, 1_000), ("1k_2.5k", 1_000, 2_500), ("2.5k_5k", 2_500, 5_000),
                ("5k_10k", 5_000, 10_000), ("10k_up", 10_000, float("inf")))


def _volume_band(volume: float) -> str:
    return next(name for name, low, high in VOLUME_BANDS if low <= volume < high)


def _window_side(closes: Any, now: datetime) -> str:
    """Where a market's end date sits against the closing window, for the funnel only."""
    try:
        moment = _parse_time(str(closes))
    except ValueError:
        return "no_end_date"
    if moment.tzinfo is None:
        return "no_end_date"
    if moment < now + timedelta(days=MIN_DAYS):
        return "ends_too_soon"
    return "ends_in_window" if moment <= now + timedelta(days=MAX_DAYS) else "ends_too_late"


def candidates(now: datetime, fetch: Fetch = fetch_json, stats: Stats | None = None) -> list[dict[str, Any]]:
    """Every market that passes the rule, one per event, with its topic and event link.

    ``stats``, if given, counts the funnel (events and markets fetched per tag, markets dropped per rule)
    without changing what is returned.
    """
    window = {"closed": "false", "active": "true", "limit": "100", "order": "volume", "ascending": "false",
              "end_date_min": _iso(now + timedelta(days=MIN_DAYS)), "end_date_max": _iso(now + timedelta(days=MAX_DAYS))}
    found: dict[str, dict[str, Any]] = {}
    band_events: dict[str, set[str]] = {}  # funnel only: events with a market passing every rule but the volume floor
    for tag, topic in TOPICS.items():
        events = fetch(f"{GAMMA}/events?{urllib.parse.urlencode(window | {'tag_slug': tag})}")
        _count(stats, f"events:{tag}", len(events))
        for event in events:
            if event["id"] in found:
                _count(stats, "drop:event_already_taken_from_other_tag")
                continue
            eligible = []
            for market in event.get("markets", []):
                _count(stats, f"markets:{tag}")
                _count(stats, f"fetched:{_window_side(market.get('endDate'), now)}")
                price = _yes_price(market)
                volume = float(market.get("volumeNum") or 0)
                if stats is not None and _polymarket_reject(market, price, float("inf"), now) is None:
                    band = _volume_band(volume)
                    _count(stats, f"volume_band:markets:{band}")
                    band_events.setdefault(band, set()).add(str(event["id"]))
                reason = _polymarket_reject(market, price, volume, now)
                if reason:
                    _count(stats, f"drop:{reason}")
                    continue
                assert price is not None
                eligible.append((volume, market, price))
            if eligible:
                _count(stats, "drop:sibling_in_same_event", len(eligible) - 1)
                volume, market, price = max(eligible, key=lambda item: item[0])
                _count(stats, f"candidates:{tag}")
                found[event["id"]] = {
                    "id": f"polymarket-{market['id']}", "market_id": str(market["id"]), "topic": topic, "tag": tag,
                    "question": market["question"].strip(), "resolution_criteria": (market.get("description") or "").strip(),
                    "closes_at": market["endDate"], "volume": volume, "volume_floor": MIN_VOLUME, "market_price": price,
                    "market_url": f"https://polymarket.com/event/{event['slug']}"}
            else:
                _count(stats, "drop:events_with_no_eligible_market")
    for band, event_ids in band_events.items():
        _count(stats, f"volume_band:events:{band}", len(event_ids))
    return list(found.values())


def asset_of(title: str) -> str | None:
    """The asset a price question is about (Bitcoin, gold, ...), named in its title, else None."""
    for name, pattern in ASSET_PATTERNS.items():
        if pattern.search(title):
            return name
    match = TICKER.search(title)
    return (match.group(1) or match.group(2)).lower() if match else None


def _day(item: dict[str, Any]) -> date | None:
    value = item.get("closes_at") or item.get("resolution_date")
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _near(a: dict[str, Any], b: dict[str, Any]) -> bool:
    first, second = _day(a), _day(b)
    return first is not None and second is not None and abs((first - second).days) <= CLUSTER_DAYS


def _crowd(item: dict[str, Any], held: list[dict[str, Any]]) -> str | None:
    """Which cap ("cluster" or "asset") the pending questions in ``held`` already fill around ``item``, else None."""
    near = [other for other in held if _near(item, other)]
    if sum(other.get("topic") == item["topic"] for other in near) >= CLUSTER_LIMIT:
        return "cluster"
    asset = asset_of(item.get("question") or "")
    if asset is not None and sum(asset_of(other.get("question") or "") == asset for other in near) >= ASSET_LIMIT:
        return "asset"
    return None


def _crowded(item: dict[str, Any], held: list[dict[str, Any]]) -> bool:
    """Whether the pending questions in ``held`` already fill the cluster or asset cap around ``item``."""
    return _crowd(item, held) is not None


def _event_key(item: dict[str, Any]) -> str:
    """The event a question belongs to: its market page (a Polymarket event URL), also kept on recorded questions."""
    return str(item.get("market_url") or item.get("resolution_source") or "")


def select(pool: list[dict[str, Any]], seen: set[str], limit: int = DAILY_LIMIT,
           pending: list[dict[str, Any]] | None = None, stats: Stats | None = None) -> list[dict[str, Any]]:
    """Take topics in turn, the most traded market first within each topic, skipping known ones.

    ``pending`` holds the questions not yet resolved (each with topic, question and closes_at or
    resolution_date); a candidate that would overfill a cluster around them is skipped (see
    ``_crowded``), and so is one that would overfill it together with questions chosen earlier.
    A candidate whose event (``market_url``) already has a pending question, from an earlier day or chosen
    earlier in this run, is skipped too: one event's outcome counts once.
    ``stats``, if given, counts the pool, the questions already seen, those left over at the topic and daily
    limits, those skipped by the event, cluster and asset caps, and those chosen.
    """
    by_topic: dict[str, list[dict[str, Any]]] = {}
    _count(stats, "select:pool", len(pool))
    for item in sorted(pool, key=lambda c: -c["volume"]):
        if item["id"] not in seen:
            by_topic.setdefault(item["topic"], []).append(item)
        else:
            _count(stats, "select:already_seen")
    held = list(pending or [])
    open_events = {_event_key(other) for other in held} - {""}  # events that already have an open question
    taken = dict.fromkeys(by_topic, 0)
    chosen: list[dict[str, Any]] = []
    while len(chosen) < limit and any(by_topic.values()):
        for topic in sorted(by_topic):
            while by_topic[topic] and taken[topic] < TOPIC_LIMIT and len(chosen) < limit:
                item = by_topic[topic].pop(0)
                if _event_key(item) and _event_key(item) in open_events:
                    _count(stats, f"select:event_already_open:{topic}")
                    continue
                crowd = _crowd(item, held)
                if crowd:
                    _count(stats, f"select:{crowd}_cap:{topic}")
                    continue
                chosen.append(item)
                held.append(item)
                open_events.add(_event_key(item))  # "" (no event known) never matches: it is checked above
                taken[topic] += 1
                _count(stats, f"select:chosen:{topic}")
                break
            else:
                # topic exhausted, at its topic limit, or the daily limit reached: what is left is never reached
                _count(stats, "select:left_at_daily_limit" if len(chosen) >= limit else f"select:left_at_topic_limit:{topic}",
                       len(by_topic[topic]))
                by_topic[topic] = []
    return chosen


# UMA's challenge window is two hours; six hours after the end with no dispute, the proposal stands.
PROPOSAL_SETTLED = timedelta(hours=6)


def _ended_before(end: Any, moment: datetime) -> bool:
    try:
        ended = _parse_time(str(end))
        return ended.tzinfo is not None and ended <= moment
    except ValueError:
        return False


def resolution(market_id: str, fetch: Fetch = fetch_json, now: datetime | None = None) -> dict[str, Any]:
    """{"status": "resolved", "outcome": 1|0} once final; "cancelled" for a 50/50 settlement; else pending.

    A closed market is final once its last UMA status is "resolved", or once it pays exactly 1 and 0 and
    either carries no UMA statuses (settled automatically) or has had an undisputed proposal for more
    than PROPOSAL_SETTLED after it ended: Gamma often leaves the status at "proposed" after UMA's two-hour
    challenge window passes, and a dispute would add "disputed". A pending result says why, without
    any price, for the run log.
    """
    market = fetch(f"{GAMMA}/markets/{market_id}")
    raw = market.get("umaResolutionStatuses") or "[]"
    statuses = (json.loads(raw) if isinstance(raw, str) else raw) or []
    prices = [float(p) for p in json.loads(market.get("outcomePrices") or "[]")]
    settled = prices in ([1.0, 0.0], [0.0, 1.0])
    long_proposed = (statuses[-1:] == ["proposed"] and "disputed" not in statuses
                     and _ended_before(market.get("endDate"), (now or datetime.now(UTC)) - PROPOSAL_SETTLED))
    final = statuses[-1:] == ["resolved"] or (settled and (not statuses or long_proposed))
    if not (market.get("closed") and final and len(prices) == 2):
        return {"status": "pending", "closed": bool(market.get("closed")), "uma": statuses[-1] if statuses else "",
                "settled_prices": settled}
    if prices == [1.0, 0.0]:
        return {"status": "resolved", "outcome": 1}
    if prices == [0.0, 1.0]:
        return {"status": "resolved", "outcome": 0}
    return {"status": "cancelled"}


def _manifold_reach(stats: Stats | None, slug: str, markets: list[dict[str, Any]], now: datetime) -> None:
    """Funnel only: where the fetched markets close (already past, before, inside, after the window) and whether the
    list comes back in ascending close order, to show whether the fetch can reach the window at all."""
    if stats is None:
        return
    times = [m["closeTime"] for m in markets if isinstance(m.get("closeTime"), int | float)]
    _count(stats, f"reach:{slug}:sorted_ascending", int(times == sorted(times)))
    for closes in times:
        days = (datetime.fromtimestamp(closes / 1000, tz=UTC) - now).total_seconds() / 86400
        bucket = ("already_past" if days < 0 else "0_to_2_days" if days < MIN_DAYS else "in_window" if days <= MAX_DAYS
                  else "after_window")
        _count(stats, f"reach:{slug}:{bucket}")


def manifold_candidates(now: datetime, fetch: Fetch = fetch_json, stats: Stats | None = None) -> list[dict[str, Any]]:
    """Manifold yes/no markets that pass the rule, in the same shape as Polymarket candidates.

    ``stats``, if given, counts markets fetched per topic and dropped per rule without changing the result.
    """
    start, end = now + timedelta(days=MIN_DAYS), now + timedelta(days=MAX_DAYS)
    found: dict[str, dict[str, Any]] = {}
    for slug, topic in MANIFOLD_TOPICS.items():
        query = {"term": "", "filter": "open", "contractType": "BINARY", "sort": "close-date", "topicSlug": slug,
                 "limit": "500"}
        markets = fetch(f"{MANIFOLD}/search-markets?{urllib.parse.urlencode(query)}")
        _count(stats, f"markets:{slug}", len(markets))
        _manifold_reach(stats, slug, markets, now)
        for market in markets:
            if market["id"] in found:
                _count(stats, "drop:already_taken_from_other_topic")
                continue
            if market.get("isResolved") or market.get("outcomeType") != "BINARY":
                _count(stats, "drop:resolved_or_not_binary")
                continue
            closes, price = market.get("closeTime"), market.get("probability")
            question = (market.get("question") or "").strip()
            if not isinstance(closes, int | float) or not isinstance(price, int | float):
                _count(stats, "drop:no_close_time_or_probability")
                continue
            closes_at = datetime.fromtimestamp(closes / 1000, tz=now.tzinfo)
            if not start <= closes_at <= end:
                # which side of the window tells whether the 500 fetched per topic reach it at all
                _count(stats, "drop:window_closes_too_soon" if closes_at < start else "drop:window_closes_too_late")
            elif not MIN_PRICE <= price <= MAX_PRICE:
                _count(stats, "drop:price_band")
            elif int(market.get("uniqueBettorCount") or 0) < MANIFOLD_MIN_BETTORS:
                _count(stats, "drop:bettors")
            elif float(market.get("volume") or 0) < MANIFOLD_MIN_VOLUME:
                _count(stats, "drop:volume")
            elif PERSONAL.search(question):
                _count(stats, "drop:personal")
            elif SLOW_SETTLING.search(question):
                _count(stats, "drop:next_leader")
            else:
                _count(stats, f"candidates:{slug}")
                found[market["id"]] = {
                    "id": f"manifold-{market['id']}", "market_id": str(market["id"]), "topic": topic, "tag": slug,
                    "question": question, "closes_at": _iso(closes_at), "volume": float(market["volume"]), "volume_floor": MANIFOLD_MIN_VOLUME,
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
