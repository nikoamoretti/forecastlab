#!/usr/bin/env python3
"""Backtest the price-math model (forecastlab.price_model) on decided questions.

For each asset-price question it uses only daily bars dated before the forecast
day (spot = last close before that day, volatility = 30 daily closes before it).
No prediction-market price is read. Network access is needed for the first run
(Coinbase for crypto, FRED for yields, Yahoo for commodities and stocks); the
bars are saved to artifacts/replay/price_history.json so reruns can be offline.

    python scripts/backtest_price_model.py [--fetch] [--out artifacts/replay]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "forecasting"))
sys.path.insert(0, str(ROOT / "scripts"))

from forecastlab.price_model import parse_question, price_model_probability, realized_daily_vol  # noqa: E402
import replay  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (forecastlab replay backtest)"}
COINBASE = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "XRP": "XRP-USD"}
YAHOO = {"WTI": "CL=F", "GOLD": "GC=F", "SILVER": "SI=F", "SPY": "SPY", "NVDA": "NVDA"}
CLIP = (0.02, 0.98)


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def fetch_coinbase(product: str, start: date, end: date) -> dict[str, dict]:
    bars: dict[str, dict] = {}
    cur = start
    while cur <= end:  # the API returns at most 300 candles per call
        stop = min(cur + timedelta(days=250), end)
        url = (f"https://api.exchange.coinbase.com/products/{product}/candles?granularity=86400"
               f"&start={cur.isoformat()}T00:00:00Z&end={stop.isoformat()}T23:59:59Z")
        for ts, low, high, _open, close, _vol in json.loads(_get(url)):
            d = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
            bars[d] = {"high": high, "low": low, "close": close}
        cur = stop + timedelta(days=1)
    return bars


def fetch_fred(series: str) -> dict[str, dict]:
    text = _get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}").decode()
    bars = {}
    for row in list(csv.reader(io.StringIO(text)))[1:]:
        if len(row) == 2 and row[1] not in ("", "."):
            bars[row[0]] = {"close": float(row[1])}
    return bars


def fetch_yahoo(symbol: str, start: date, end: date) -> dict[str, dict]:
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    p2 = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp()) + 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={p1}&period2={p2}&interval=1d")
    res = json.loads(_get(url))["chart"]["result"][0]
    off = res["meta"].get("gmtoffset", 0)
    q = res["indicators"]["quote"][0]
    bars = {}
    for i, ts in enumerate(res["timestamp"]):
        if q["close"][i] is None:
            continue
        d = datetime.fromtimestamp(ts + off, timezone.utc).date().isoformat()
        bars[d] = {"high": q["high"][i], "low": q["low"][i], "close": q["close"][i]}
    return bars


def fetch_all(assets: set[str], start: date, end: date) -> dict[str, dict]:
    hist: dict[str, dict] = {}
    for a in sorted(assets):
        try:
            if a in COINBASE:
                hist[a] = fetch_coinbase(COINBASE[a], start, end)
            elif a == "DGS10":
                hist[a] = fetch_fred("DGS10")
            elif a in YAHOO:
                try:
                    hist[a] = fetch_yahoo(YAHOO[a], start, end)
                except Exception as e:  # noqa: BLE001
                    print(f"{a}: yahoo failed ({e!r})", file=sys.stderr)
                    if a == "WTI":
                        hist[a] = fetch_fred("DCOILWTICO")
                    else:
                        raise
        except Exception as e:  # noqa: BLE001
            print(f"{a}: fetch failed ({e!r})", file=sys.stderr)
            continue
        print(f"{a}: {len(hist[a])} bars", file=sys.stderr)
    return hist


def run(history: dict[str, dict], rows: list[dict]) -> tuple[dict, list[dict]]:
    probs: dict[str, float] = {}
    details: list[dict] = []
    for r in rows:
        if not r["forecast_day"]:
            continue
        fday = date.fromisoformat(r["forecast_day"])
        pq = parse_question(r["title"], r["detail"], fday)
        if pq is None:
            continue
        rec = {"id": r["id"], "title": r["title"], "forecast_day": r["forecast_day"], "asset": pq.asset,
               "kind": pq.kind, "threshold": pq.threshold, "window": [pq.window_start.isoformat(), pq.window_end.isoformat()]}
        bars = {d: b for d, b in history.get(pq.asset, {}).items() if d < r["forecast_day"]}  # no lookahead
        days = sorted(bars)
        if len(days) < 12:
            rec["error"] = "no price history"
            details.append(rec)
            continue
        spot_date = date.fromisoformat(days[-1])
        assert spot_date < fday
        vol = realized_daily_vol([bars[d]["close"] for d in days], 30)
        win = [bars[d] for d in days if pq.window_start.isoformat() <= d]
        p = price_model_probability(
            pq, bars[days[-1]]["close"], spot_date, vol,
            [b["high"] for b in win if "high" in b], [b["low"] for b in win if "low" in b])
        rec.update(spot=bars[days[-1]]["close"], spot_date=days[-1], daily_vol=vol, raw=p,
                   outcome=r["outcome"], claude=r["claude"])
        probs[r["id"]] = min(max(p, CLIP[0]), CLIP[1])
        details.append(rec)
    return probs, details


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "replay"))
    ap.add_argument("--fetch", action="store_true", help="download price history (otherwise read price_history.json)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = replay.load_questions()
    parsed = []
    for r in rows:
        if r["forecast_day"]:
            pq = parse_question(r["title"], r["detail"], date.fromisoformat(r["forecast_day"]))
            if pq:
                parsed.append((r, pq))
    print(f"parsed {len(parsed)} of {len(rows)} decided questions", file=sys.stderr)
    hist_path = out / "price_history.json"
    if args.fetch:
        first = min(date.fromisoformat(r["forecast_day"]) for r, _ in parsed) - timedelta(days=90)
        last = max(date.fromisoformat(r["forecast_day"]) for r, _ in parsed)  # bars after a question's day are never used
        history = fetch_all({pq.asset for _, pq in parsed}, first, last)
        hist_path.write_text(json.dumps(history, sort_keys=True))
    else:
        history = json.loads(hist_path.read_text())
    probs, details = run(history, rows)
    (out / "price_model_v1.json").write_text(json.dumps(probs, indent=1, sort_keys=True) + "\n")
    (out / "price_model_v1_details.json").write_text(json.dumps(details, indent=1) + "\n")
    print(f"wrote {len(probs)} probabilities to {out / 'price_model_v1.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
