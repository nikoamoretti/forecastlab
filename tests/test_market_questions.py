from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from forecastlab.market_questions import candidates, manifold_candidates, manifold_resolution, resolution, select
from forecastlab_api.track_record import build_track_record

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import market_questions as script  # noqa: E402

NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


def _market(market_id: str, question: str, price: float, volume: float, ends: str = "2026-10-15T00:00:00Z",
            outcomes: tuple[str, str] = ("Yes", "No")) -> dict:
    return {"id": market_id, "question": question, "description": f"Rules for {question}", "endDate": ends,
            "outcomes": json.dumps(list(outcomes)), "outcomePrices": json.dumps([str(price), str(1 - price)]),
            "volumeNum": volume, "closed": False}


EVENTS = {
    "economy": [
        {"id": "e1", "slug": "boc-october", "markets": [
            _market("m1", "Will the Bank of Canada hold rates?", 0.78, 54_000),
            _market("m2", "Will the Bank of Canada hike 25 bps?", 0.21, 43_000)]},  # same event: only the larger
        {"id": "e2", "slug": "thin", "markets": [_market("m3", "Thinly traded?", 0.5, 2_000)]},  # under $10k
        {"id": "e3", "slug": "sure-thing", "markets": [_market("m4", "Near certain?", 0.97, 90_000)]},  # above 95%
    ],
    "politics": [
        {"id": "e4", "slug": "israel-pm", "markets": [_market("m5", "Will the Knesset pass the 2027 budget?", 0.36, 6_000_000)]},
        {"id": "e8", "slug": "next-pm", "markets": [_market("m9", "Will Ana Brnabić be the next Prime Minister of Serbia?", 0.4, 900_000)]},  # settles at swearing-in
        {"id": "e5", "slug": "far-future", "markets": [_market("m6", "Too far out?", 0.5, 80_000, ends="2026-12-31T00:00:00Z")]},
        {"id": "e6", "slug": "multi", "markets": [_market("m7", "Team A or B?", 0.5, 80_000, outcomes=("A", "B"))]},
    ],
    "world": [{"id": "e4", "slug": "israel-pm", "markets": [_market("m5", "Will the Knesset pass the 2027 budget?", 0.36, 6_000_000)]}],
    "tech": [{"id": "e7", "slug": "openai-training", "markets": [_market("m8", "Will OpenAI resume training?", 0.62, 15_000)]}],
}


def fake_fetch(url: str):
    if "/events?" in url:
        tag = url.split("tag_slug=")[1].split("&")[0]
        return EVENTS.get(tag, [])
    raise AssertionError(url)


def test_selection_rule_keeps_liquid_open_yes_no_markets_one_per_event() -> None:
    pool = {c["market_id"]: c for c in candidates(NOW, fake_fetch)}
    assert set(pool) == {"m1", "m5", "m8"}
    assert pool["m1"]["topic"] == "Economy" and pool["m1"]["market_url"] == "https://polymarket.com/event/boc-october"
    assert pool["m5"]["market_price"] == 0.36

    chosen = select(list(pool.values()), seen={"polymarket-m8"}, limit=2)
    assert [c["market_id"] for c in chosen] == ["m1", "m5"]  # topics in turn, Tech already tracked

    crowded = [{"id": f"c{i}", "topic": "Crypto", "volume": 100 - i} for i in range(5)]
    assert [c["id"] for c in select(crowded, seen=set())] == ["c0", "c1"]  # at most two per topic a day


@pytest.mark.parametrize(("market", "expected"), [
    ({"closed": True, "outcomePrices": '["1", "0"]', "umaResolutionStatuses": '["proposed", "resolved"]'},
     {"status": "resolved", "outcome": 1}),
    ({"closed": True, "outcomePrices": '["0", "1"]', "umaResolutionStatuses": '["resolved"]'}, {"status": "resolved", "outcome": 0}),
    ({"closed": True, "outcomePrices": '["0", "1"]', "umaResolutionStatuses": '["proposed"]'}, {"status": "pending"}),
    ({"closed": False, "outcomePrices": '["0.4", "0.6"]', "umaResolutionStatuses": "[]"}, {"status": "pending"}),
    ({"closed": True, "outcomePrices": '["0.5", "0.5"]', "umaResolutionStatuses": '["resolved"]'}, {"status": "cancelled"}),
])
def test_only_final_polymarket_results_count(market: dict, expected: dict) -> None:
    assert resolution("m1", lambda _url: market) == expected


def test_prepare_record_resolve_and_score_without_the_market_in_our_call(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    directory = script.prepare(NOW, artifacts, fake_fetch)
    assert directory is not None
    questions = json.loads((directory / "questions.json").read_text())["questions"]
    assert "market_price" not in json.dumps(questions)  # forecasters never see the price
    assert script.prepare(NOW, artifacts, fake_fetch) is None  # same day: every market is already known

    answers = tmp_path / "claude.json"
    answers.write_text(json.dumps({"forecast_made_at": "2026-10-07T13:00:00Z", "forecasts": [
        {"id": q["id"], "probability": 0.7, "rationale": "Because.", "sources": ["https://example.org"],
         **({"integrity_note": "Odds showed up in a search summary."} if q["id"] == "polymarket-m5" else {})}
        for q in questions]}))
    script.record(directory, answers)

    late = tmp_path / "late.json"
    late.write_text(json.dumps({"forecast_made_at": "2026-10-20T00:00:00Z", "forecasts": [
        {"id": q["id"], "probability": 0.7} for q in questions]}))
    with pytest.raises(SystemExit, match="before the market closes"):
        script.record(directory, late)

    final = {"m1": ["1", "0"], "m5": ["0", "1"], "m8": ["0.5", "0.5"]}

    def settled(url: str):
        market_id = url.rsplit("/", 1)[1]
        return {"closed": True, "outcomePrices": json.dumps(final[market_id]), "umaResolutionStatuses": '["resolved"]'}

    assert script.resolve(artifacts, settled, today=datetime(2026, 10, 16, tzinfo=UTC)) == 3

    record = build_track_record(artifacts, today=datetime(2026, 10, 16).date())
    by_id = {q["id"]: q for q in record["questions"]}
    hold = by_id["polymarket-m1"]
    assert hold["call"] == {"method": "combined_median_v1", "probability": 0.7, "members": 1}  # market excluded
    assert (hold["status"], hold["verdict"], hold["actual"], hold["topic"]) == ("resolved", "right", "Polymarket: Yes", "Economy")
    assert by_id["polymarket-m5"]["verdict"] == "wrong"
    assert by_id["polymarket-m5"]["note"] == "Odds showed up in a search summary." and hold["note"] is None
    assert by_id["polymarket-m8"]["status"] == "cancelled"
    market = next(f for f in record["forecasters"] if f["method"] == "market_price_v1")
    assert (market["resolved"], market["right"]) == (2, 2)  # 0.78 on yes, 0.36 on no
    assert record["summary"]["right"] == 1 and record["summary"]["wrong"] == 1


def _manifold(market_id: str, question: str, price: float, *, bettors: int = 40, volume: float = 5_000,
              closes: str = "2026-10-15T00:00:00+00:00") -> dict:
    return {"id": market_id, "question": question, "probability": price, "uniqueBettorCount": bettors,
            "volume": volume, "closeTime": int(datetime.fromisoformat(closes).timestamp() * 1000),
            "outcomeType": "BINARY", "isResolved": False, "url": f"https://manifold.markets/u/{market_id}"}


MANIFOLD_SEARCH = {
    "economics-default": [
        _manifold("f1", "Will the Fed raise rates in October 2026?", 0.23),
        _manifold("f2", "Will WTI close above $99.50 on Oct 8?", 0.4, closes="2026-10-08T00:00:00+00:00"),  # too soon
        _manifold("f3", "Will silver outperform gold?", 0.55, bettors=6),  # too few traders
        _manifold("f4", "Will I get the OpenAI Pro subscription this week?", 0.5),  # personal
    ],
    "politics-default": [_manifold("f5", "Will the shutdown end by Oct 20?", 0.97)],  # above 95%
    "technology-default": [_manifold("f6", "Will Gemini 4 be released before Oct 15?", 0.31, volume=200)],  # thin
    "world-default": [_manifold("f7", "Will there be mass protests in the USA on Oct 14?", 0.6)],
}


def manifold_fetch(url: str):
    if "manifold.markets/v0/search-markets" in url:
        return MANIFOLD_SEARCH[url.split("topicSlug=")[1].split("&")[0]]
    if "manifold.markets/v0/market/" in url:
        market_id = url.rsplit("/", 1)[1]
        return {"id": market_id, "textDescription": f"Rules for {market_id}.", "isResolved": market_id == "f1",
                "resolution": "YES" if market_id == "f1" else None}
    return fake_fetch(url)


def test_manifold_rule_keeps_traded_impersonal_markets_in_the_window() -> None:
    pool = {c["market_id"]: c for c in manifold_candidates(NOW, manifold_fetch)}
    assert set(pool) == {"f1", "f7"}
    assert pool["f1"] | {"volume": 0} == {
        "id": "manifold-f1", "market_id": "f1", "topic": "Economy", "tag": "economics-default",
        "question": "Will the Fed raise rates in October 2026?", "closes_at": "2026-10-15T00:00:00Z", "volume": 0,
        "market_price": 0.23, "market_url": "https://manifold.markets/u/f1", "method": "manifold_price_v1"}


@pytest.mark.parametrize(("market", "expected"), [
    ({"isResolved": True, "resolution": "YES"}, {"status": "resolved", "outcome": 1}),
    ({"isResolved": True, "resolution": "NO"}, {"status": "resolved", "outcome": 0}),
    ({"isResolved": True, "resolution": "MKT"}, {"status": "cancelled"}),
    ({"isResolved": True, "resolution": "CANCEL"}, {"status": "cancelled"}),
    ({"isResolved": False}, {"status": "pending"}),
])
def test_manifold_results(market: dict, expected: dict) -> None:
    assert manifold_resolution("f1", lambda _url: market) == expected


def test_manifold_questions_join_the_daily_set_with_their_own_benchmark(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    directory = script.prepare(NOW, artifacts, manifold_fetch)
    assert directory is not None
    selected = json.loads((directory / "questions.json").read_text())
    by_id = {q["id"]: q for q in selected["questions"]}
    assert selected["selection"] == "polymarket_selection_v2+manifold_selection_v1"
    assert {"polymarket-m1", "manifold-f1", "manifold-f7"} <= set(by_id)
    assert by_id["manifold-f1"]["resolution_criteria"] == "Rules for f1."
    assert "market_price" not in json.dumps(selected)

    answers = tmp_path / "claude.json"
    answers.write_text(json.dumps({"forecast_made_at": "2026-10-07T13:00:00Z", "forecasts": [
        {"id": qid, "probability": 0.3} for qid in by_id]}))
    script.record(directory, answers)
    recorded = {q["id"]: q for q in json.loads((directory / "forecasts.json").read_text())["questions"]}
    assert recorded["manifold-f1"]["forecasts"][0] == {
        "method": "manifold_price_v1", "probability": 0.23, "created_at": NOW.isoformat()}
    assert recorded["polymarket-m1"]["forecasts"][0]["method"] == "market_price_v1"

    def settled(url: str):
        if "manifold" in url:
            return manifold_fetch(url)
        return {"closed": False, "outcomePrices": "[]"}

    script.resolve(artifacts, settled, today=datetime(2026, 10, 16, tzinfo=UTC))
    record = build_track_record(artifacts, today=datetime(2026, 10, 16).date())
    fed = next(q for q in record["questions"] if q["id"] == "manifold-f1")
    assert (fed["status"], fed["actual"], fed["verdict"]) == ("resolved", "Manifold: Yes", "wrong")
    assert fed["call"] == {"method": "combined_median_v1", "probability": 0.3, "members": 1}  # benchmark excluded
