"""Throwaway supply probe (counts only; never prints a price or a per-market probability)."""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))
from forecastlab import market_questions as mq  # noqa: E402

UA = {"User-Agent": "ForecastLab/0.3 (+https://github.com/nikoamoretti/forecastlab)", "Accept": "application/json"}
CHECKABLE = re.compile(
    r"leaderboard|lmarena|chatbot arena|app store|google play|top free|#1\b|number one|rank|release[ds]?\b|launch|"
    r"announce|github|stars|downloads|top (ai|model)|best ai|which company|hugging ?face|benchmark|"
    r"official|publish|counter|confirmed|available|ship|version", re.I)


def get(url: str, retries: int = 2) -> Any:
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503) and attempt < retries:
                time.sleep(2 + 3 * attempt)
                continue
            raise
        except Exception:
            if attempt < retries:
                time.sleep(2)
                continue
            raise


def bucket(now: datetime, closes: str) -> int | None:
    try:
        d = (mq._parse_time(closes) - now).total_seconds() / 86400
    except Exception:  # noqa: BLE001
        return None
    return min(int(d), 9) if d >= 2 else None


def poly_events(now: datetime, tag: str | None, pages: int) -> tuple[list[dict], int, str]:
    w = {"closed": "false", "active": "true", "limit": "100", "order": "volume", "ascending": "false",
         "end_date_min": mq._iso(now + timedelta(days=2)), "end_date_max": mq._iso(now + timedelta(days=10))}
    if tag:
        w["tag_slug"] = tag
    out: list[dict] = []
    first = 0
    err = ""
    for p in range(pages):
        try:
            ev = get(f"{mq.GAMMA}/events?{urllib.parse.urlencode(w | {'offset': str(p * 100)})}")
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__} {getattr(exc, 'code', '')}"
            break
        if p == 0:
            first = len(ev)
        out += ev
        if len(ev) < 100:
            break
        time.sleep(0.3)
    return out, first, err


def passing(now: datetime, events: list[dict]) -> dict[str, list[dict]]:
    """event id -> passing markets (every existing rule at the floor)."""
    res: dict[str, list[dict]] = {}
    for e in events:
        for m in e.get("markets", []):
            price = mq._yes_price(m)
            vol = float(m.get("volumeNum") or 0)
            if mq._polymarket_reject(m, price, vol, now) is None:
                res.setdefault(str(e["id"]), []).append(m)
    return res


def polymarket(now: datetime) -> None:
    print("=== A. POLYMARKET (window 2-10 days, floor $2,500, all rules) ===")
    current = list(mq.TOPICS)
    candidates = ["ai", "science", "business", "culture", "pop-culture", "technology", "big-tech", "openai", "earnings",
                  "weather", "climate", "elections", "sports", "entertainment", "movies", "music", "awards", "space",
                  "spacex", "apple", "nvidia", "tesla", "elon-musk", "fed", "inflation", "trump", "china", "ukraine",
                  "middle-east", "tiktok", "chatgpt", "anthropic", "gemini", "grok", "ipo", "stocks", "commodities",
                  "oil", "mentions", "tweet-markets", "twitter", "gaming", "video-games", "ai-models", "app-store",
                  "economic-policy", "global-elections", "health", "covid", "pandemic", "bird-flu", "tariffs"]
    try:
        tags = get(f"{mq.GAMMA}/tags?limit=500")
        slugs = sorted({t.get("slug") for t in tags if t.get("slug")})
        print(f"gamma /tags returned {len(slugs)} slugs; slugs matching ai/tech/science/app/rank/game/release/music/movie/space/weather: "
              + ", ".join(s for s in slugs if re.search(r"(^|-)ai($|-)|tech|science|app|rank|game|release|music|movie|space|weather", s))[:900])
    except Exception as exc:  # noqa: BLE001
        print(f"gamma /tags error: {type(exc).__name__} {getattr(exc, 'code', '')}")
    cache: dict[str, dict[str, list[dict]]] = {}
    ev_by_id: dict[str, dict] = {}
    reach_today: set[str] = set()   # events reachable via first 100 of each current tag (what the pipeline fetches)
    reach_paged: set[str] = set()
    rows = []
    for tag in current + candidates:
        events, first, err = poly_events(now, tag, 6)
        for e in events:
            ev_by_id[str(e["id"])] = e
        p = passing(now, events)
        cache[tag] = p
        if tag in current:
            reach_paged |= set(p)
            first_ids = {str(e["id"]) for e in events[:first]}
            reach_today |= set(passing(now, [e for e in events if str(e["id"]) in first_ids]))
        rows.append((tag, first, len(events), err, p))
        time.sleep(0.3)
    for tag, first, n, err, p in rows:
        mk = sum(len(v) for v in p.values())
        new = [i for i in p if i not in reach_paged]
        newtoday = [i for i in p if i not in reach_today]
        chk = sum(1 for i in p if CHECKABLE.search(ev_by_id[i].get("title") or ""))
        label = "CURRENT" if tag in current else "cand"
        print(f"{label:7} {tag:16} first_page={first:3} fetched_paged={n:4} cap_hit={'Y' if first == 100 else 'N'} passing_markets={mk:4} "
              f"events={len(p):4} siblings_dropped={mk - len(p):4} new_vs_current_paged={len(new):4} new_vs_today={len(newtoday):4} checkable_kw={chk}"
              + (f" ERR={err}" if err else ""))
    allev, first, err = poly_events(now, None, 30)
    pall = passing(now, allev)
    for e in allev:
        ev_by_id.setdefault(str(e["id"]), e)
    union_tags = set().union(*[set(v) for v in cache.values()])
    print(f"\nALL events in window, no tag filter (<=30 pages): events={len(allev)} first_page={first} err={err or 'none'} "
          f"passing_markets={sum(len(v) for v in pall.values())} passing_events={len(pall)}; "
          f"in current tags (paged)={len(set(pall) & reach_paged)}, in today's fetch={len(set(pall) & reach_today)}, "
          f"in none of the tags probed={len(set(pall) - union_tags)}")
    unreachable = [i for i in pall if i not in reach_today]
    tagc: Counter = Counter()
    for i in unreachable:
        for t in ev_by_id[i].get("tags") or []:
            tagc[t.get("slug")] += 1
    print("passing events NOT in today's fetch, by event tag slug (top 40, counts): " + ", ".join(f"{k}={v}" for k, v in tagc.most_common(40)))

    def inflow(label: str, ids: set[str]) -> None:
        mk, ev = Counter(), defaultdict(set)
        for i in ids:
            ms = pall.get(i) or next((cache[t][i] for t in cache if i in cache[t]), [])
            for m in ms:
                b = bucket(now, m["endDate"])
                if b is not None:
                    mk[b] += 1
                    ev[b].add(i)
        print(f"inflow {label} (bucket = days-to-close floor, 9 = 9-10 days): markets "
              + " ".join(f"d{b}={mk[b]}" for b in range(2, 10)) + " | events "
              + " ".join(f"d{b}={len(ev[b])}" for b in range(2, 10)))
    print()
    inflow("current tags, paged", reach_paged)
    inflow("current tags, today's fetch (first 100/tag)", reach_today)
    inflow("ALL (untagged fetch)", set(pall))
    inflow("all passing NOT in today's fetch", {i for i in pall if i not in reach_today})
    print("\nsample titles (<=10, no prices) of passing events not in today's fetch, per candidate tag:")
    shown: set[str] = set()
    for tag, first, n, err, p in rows:
        if tag in current:
            continue
        fresh = [i for i in p if i not in reach_today and i not in shown]
        shown |= set(fresh)
        titles = [ev_by_id[i].get("title") or "" for i in fresh][:10]
        if titles:
            print(f"  [{tag}] ({len(fresh)} new) " + " || ".join(t[:90] for t in titles))
    chk = [ev_by_id[i].get("title") or "" for i in pall if CHECKABLE.search(ev_by_id[i].get("title") or "")][:10]
    print("sample checkable-keyword titles among all passing events: " + " || ".join(t[:90] for t in chk))


# ---------------- Kalshi ----------------
def kget(base: str, path: str, params: dict | None = None) -> tuple[int, Any]:
    url = f"{base}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(3)
                continue
            return exc.code, exc.read()[:200].decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            return -1, f"{type(exc).__name__}: {exc}"
    return -1, "retries"


def cents(m: dict, key: str) -> float | None:
    if m.get(key) not in (None, ""):
        return float(m[key])
    if m.get(key + "_dollars") not in (None, ""):
        return float(m[key + "_dollars"]) * 100
    return None


def count(m: dict, key: str) -> float | None:
    for k in (key, key + "_fp"):
        if m.get(k) not in (None, ""):
            return float(m[k])
    return None


def kalshi(now: datetime) -> None:
    print("\n=== B. KALSHI ===")
    base = None
    for b in ("https://api.elections.kalshi.com/trade-api/v2", "https://trading-api.kalshi.com/trade-api/v2",
              "https://api.kalshi.com/trade-api/v2"):
        code, data = kget(b, "/markets", {"limit": "1", "status": "open"})
        print(f"GET {b}/markets?limit=1&status=open -> HTTP {code}" + ("" if code == 200 else f" body={str(data)[:150]}"))
        if code == 200:
            base = b
            break
    if not base:
        print("Kalshi unreachable or needs auth; stopped.")
        return
    lo, hi = int((now + timedelta(days=2)).timestamp()), int((now + timedelta(days=10)).timestamp())
    variants = [{"status": "open", "min_close_ts": lo, "max_close_ts": hi, "mve_filter": "exclude"},
                {"status": "open", "min_close_ts": lo, "max_close_ts": hi},
                {"min_close_ts": lo, "max_close_ts": hi}]
    used = None
    for v in variants:
        code, data = kget(base, "/markets", {**v, "limit": "1000"})
        print(f"variant {sorted(v)} -> HTTP {code}" + ("" if code == 200 else f" body={str(data)[:150]}"))
        if code == 200:
            used = v
            break
    if used is None:
        print("no variant worked; stopped")
        return
    markets: list[dict] = []
    cursor, pages = "", 0
    while pages < 150:
        params = {**used, "limit": "1000"} | ({"cursor": cursor} if cursor else {})
        code, data = kget(base, "/markets", params)
        if code != 200:
            print(f"page {pages} HTTP {code} {str(data)[:150]}")
            break
        markets += data.get("markets", [])
        cursor = data.get("cursor") or ""
        pages += 1
        if not cursor:
            break
        time.sleep(0.2)
    print(f"fetched {len(markets)} markets in {pages} pages; more_pages_left={'Y' if cursor else 'N'}")
    if not markets:
        return
    print("market top-level keys: " + ",".join(sorted(markets[0].keys())))

    def closes(m: dict) -> datetime | None:
        try:
            return datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            return None
    print(f"status values: {dict(Counter(m.get('status') for m in markets))}; market_type: {dict(Counter(m.get('market_type') for m in markets))}")
    inwin = [m for m in markets if closes(m) and now + timedelta(days=2) <= closes(m) <= now + timedelta(days=10)
             and m.get("status") in ("open", "active", None)]
    mve = [m for m in inwin if str(m.get("ticker", "")).startswith("KXMVE") or m.get("mve_collection_ticker")]
    print(f"open markets closing 2-10d: total={len(inwin)} distinct_events={len({m.get('event_ticker') for m in inwin})}; "
          f"of which multivariate combos={len(mve)}")
    inwin = [m for m in inwin if m not in mve]
    print(f"EXCLUDING multivariate combos: markets={len(inwin)} events={len({m.get('event_ticker') for m in inwin})}")

    def price(m: dict) -> float | None:
        last = cents(m, "last_price")
        if last and last > 0:
            return last
        b, a = cents(m, "yes_bid"), cents(m, "yes_ask")
        return (b + a) / 2 if b and a else None
    prices = {id(m): price(m) for m in inwin}
    band = [m for m in inwin if prices[id(m)] is not None and 5 <= prices[id(m)] <= 95]
    print(f"with yes price 5-95c: markets={len(band)} events={len({m.get('event_ticker') for m in band})}; "
          f"no usable price={sum(1 for m in inwin if prices[id(m)] is None)}")
    cat: dict[str, str] = {}
    code, data = kget(base, "/series", {})
    if code == 200:
        for s in data.get("series", []) or []:
            cat[s.get("ticker")] = s.get("category") or "unknown"
        print(f"/series -> HTTP 200, {len(cat)} series with a category")
    else:
        print(f"/series -> HTTP {code} {str(data)[:120]}")

    def category(m: dict) -> str:
        st = m.get("series_ticker") or str(m.get("event_ticker", "")).split("-")[0]
        return cat.get(st) or "unknown"
    cats = Counter(category(m) for m in inwin)
    catb = Counter(category(m) for m in band)
    cate: dict[str, set] = defaultdict(set)
    ecat: dict[str, set] = defaultdict(set)
    for m in inwin:
        ecat[category(m)].add(m.get("event_ticker"))
    for m in band:
        cate[category(m)].add(m.get("event_ticker"))
    print("by category: category | window_markets | window_events | in_price_band | band_events")
    for c, n in cats.most_common():
        print(f"  {c:28} {n:5} {len(ecat[c]):5} {catb[c]:5} {len(cate[c]):5}")

    def vb(x: float) -> str:
        return "under100" if x < 100 else "100-1k" if x < 1000 else "1k-10k" if x < 10000 else "over10k"
    for name in ("volume", "open_interest"):
        cnt: Counter = Counter()
        ev: dict[str, set] = defaultdict(set)
        for m in band:
            b = vb(count(m, name) or 0.0)
            cnt[b] += 1
            ev[b].add(m.get("event_ticker"))
        print(f"band by {name} (contracts): " + " | ".join(f"{b}: markets={cnt[b]} events={len(ev[b])}" for b in ("under100", "100-1k", "1k-10k", "over10k")))
    for thr in (100, 1000):
        c2: Counter = Counter()
        e2: dict[str, set] = defaultdict(set)
        for m in band:
            if (count(m, "volume") or 0) >= thr:
                c2[category(m)] += 1
                e2[category(m)].add(m.get("event_ticker"))
        print(f"band & volume>={thr}: markets={sum(c2.values())} events={len(set().union(*e2.values())) if e2 else 0}; by category markets/events: "
              + ", ".join(f"{c}={n}/{len(e2[c])}" for c, n in c2.most_common()))
    for label, group in (("all window", inwin), ("price band", band), ("band & vol>=100", [m for m in band if (count(m, 'volume') or 0) >= 100])):
        mk: Counter = Counter()
        ev2: dict[int, set] = defaultdict(set)
        for m in group:
            b = min(int((closes(m) - now).total_seconds() / 86400), 9)
            mk[b] += 1
            ev2[b].add(m.get("event_ticker"))
        print(f"inflow {label}: markets " + " ".join(f"d{b}={mk[b]}" for b in range(2, 10)) + " | events " + " ".join(f"d{b}={len(ev2[b])}" for b in range(2, 10)))
    need = ["ticker", "title", "subtitle", "yes_sub_title", "rules_primary", "rules_secondary", "close_time", "expiration_time",
            "last_price", "last_price_dollars", "yes_bid", "yes_bid_dollars", "yes_ask", "yes_ask_dollars", "volume", "volume_fp",
            "volume_24h", "open_interest", "open_interest_fp", "event_ticker", "result", "status", "market_type", "settlement_value",
            "can_close_early", "expected_expiration_time", "strike_type"]
    print("field nonempty/present among window markets: " + ", ".join(
        f"{k}={sum(1 for m in inwin if m.get(k) not in (None, ''))}/{sum(1 for m in inwin if k in m)}" for k in need))
    code, data = kget(base, "/markets", {"status": "settled", "limit": "200"})
    if code == 200 and data.get("markets"):
        sm = data["markets"]
        print(f"settled sample: HTTP 200, {len(sm)} markets; result values {dict(Counter(m.get('result') for m in sm))}; "
              f"status values {dict(Counter(m.get('status') for m in sm))}; keys with settlement info: "
              + ",".join(k for k in sorted(sm[0]) if re.search(r"result|settle|expir|determin", k)))
        c2_, d2 = kget(base, f"/markets/{sm[0]['ticker']}")
        print(f"GET /markets/{{ticker}} -> HTTP {c2_}; has result key={'result' in (d2.get('market', {}) if isinstance(d2, dict) else {})}")
    else:
        print(f"settled sample: HTTP {code} {str(data)[:150]}")
    print("sample titles (<=10 per category, price band, no prices):")
    bycat: dict[str, list[str]] = defaultdict(list)
    for m in sorted(band, key=lambda m: -(count(m, "volume") or 0)):
        t = str(m.get("title"))
        if len(bycat[category(m)]) < 10 and t not in bycat[category(m)]:
            bycat[category(m)].append(t)
    for c, ts in bycat.items():
        print(f"  [{c}] " + " || ".join(t[:90] for t in ts))
    kw = [str(m.get("title")) for m in band if CHECKABLE.search(m.get("title") or "")]
    print(f"checkable-keyword titles in band: {len(kw)}; samples: " + " || ".join(t[:80] for t in kw[:10]))


def run() -> None:
    now = datetime.now(timezone.utc)
    print(f"supply probe at {now.isoformat()}")
    for fn in (polymarket, kalshi):
        try:
            fn(now)
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"{fn.__name__} FAILED: {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=3, file=sys.stdout)


if __name__ == "__main__":
    run()
