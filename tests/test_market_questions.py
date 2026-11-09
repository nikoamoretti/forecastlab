from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from forecastlab.live_counts import NO_TALLY
from forecastlab.market_questions import (
    asset_of,
    candidates,
    manifold_candidates,
    manifold_resolution,
    resolution,
    select,
    settles_late,
)
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
        {"id": "e2", "slug": "thin", "markets": [_market("m3", "Thinly traded?", 0.5, 2_000)]},  # under the volume floor
        {"id": "e3", "slug": "sure-thing", "markets": [_market("m4", "Near certain?", 0.97, 90_000)]},  # above 95%
    ],
    "politics": [
        {"id": "e4", "slug": "israel-pm", "markets": [_market("m5", "Will the Knesset pass the 2027 budget?", 0.36, 6_000_000)]},
        {"id": "e8", "slug": "next-pm", "markets": [_market("m9", "Will Ana Brnabić be the next Prime Minister of Serbia?", 0.4, 900_000)]},  # settles at swearing-in
        {"id": "e9", "slug": "bulgaria", "markets": [{**_market("m10", "Will Iliana Iotova win the Bulgarian presidential election?", 0.6, 700_000),
            "description": "Includes any second round. If the result isn't known by December 31, 2027, 11:59 PM ET, resolves Other."}]},
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


def test_rules_with_a_deadline_long_after_the_end_date_settle_late() -> None:
    closes = datetime(2026, 10, 25, 23, 59, tzinfo=UTC)
    assert settles_late("by June 30, 2028, 11:59 PM ET", closes)
    assert not settles_late("Resolves Yes if announced by October 31, 2026, 11:59 PM ET.", closes)
    assert not settles_late("No dates here.", closes)


def test_selection_rule_keeps_liquid_open_yes_no_markets_one_per_event() -> None:
    pool = {c["market_id"]: c for c in candidates(NOW, fake_fetch)}
    assert set(pool) == {"m1", "m5", "m8"}
    assert pool["m1"]["topic"] == "Economy" and pool["m1"]["market_url"] == "https://polymarket.com/event/boc-october"
    assert pool["m5"]["market_price"] == 0.36

    chosen = select(list(pool.values()), seen={"polymarket-m8"}, limit=2)
    assert [c["market_id"] for c in chosen] == ["m1", "m5"]  # topics in turn, Tech already tracked

    crowded = [{"id": f"c{i}", "topic": "Crypto", "volume": 100 - i} for i in range(6)]
    assert [c["id"] for c in select(crowded, seen=set())] == ["c0", "c1", "c2", "c3"]  # at most four per topic a day


@pytest.mark.parametrize(("market", "expected"), [
    ({"closed": True, "outcomePrices": '["1", "0"]', "umaResolutionStatuses": '["proposed", "resolved"]'},
     {"status": "resolved", "outcome": 1}),
    ({"closed": True, "outcomePrices": '["0", "1"]', "umaResolutionStatuses": '["resolved"]'}, {"status": "resolved", "outcome": 0}),
    ({"closed": True, "outcomePrices": '["0", "1"]', "umaResolutionStatuses": '["proposed"]'},
     {"status": "pending", "closed": True, "uma": "proposed", "settled_prices": True}),
    ({"closed": False, "outcomePrices": '["0.4", "0.6"]', "umaResolutionStatuses": "[]"},
     {"status": "pending", "closed": False, "uma": "", "settled_prices": False}),
    # Automatically settled markets (daily crypto prices) have no UMA statuses at all.
    ({"closed": True, "outcomePrices": '["0", "1"]', "umaResolutionStatuses": "[]"}, {"status": "resolved", "outcome": 0}),
    ({"closed": True, "outcomePrices": '["1", "0"]'}, {"status": "resolved", "outcome": 1}),
    ({"closed": True, "outcomePrices": '["0.97", "0.03"]', "umaResolutionStatuses": "[]"},
     {"status": "pending", "closed": True, "uma": "", "settled_prices": False}),
    # Gamma often leaves an undisputed proposal at "proposed"; six hours after the end it is final.
    ({"closed": True, "outcomePrices": '["0", "1"]', "umaResolutionStatuses": '["proposed"]', "endDate": "2026-10-10T16:00:00Z"},
     {"status": "resolved", "outcome": 0}),
    ({"closed": True, "outcomePrices": '["0", "1"]', "umaResolutionStatuses": '["proposed"]', "endDate": "2026-10-22T16:00:00Z"},
     {"status": "resolved", "outcome": 0}),
    ({"closed": True, "outcomePrices": '["0", "1"]', "umaResolutionStatuses": '["proposed"]', "endDate": "2026-10-23T10:00:00Z"},
     {"status": "pending", "closed": True, "uma": "proposed", "settled_prices": True}),
    ({"closed": True, "outcomePrices": '["0", "1"]', "umaResolutionStatuses": '["proposed", "disputed", "proposed"]',
      "endDate": "2026-10-10T16:00:00Z"}, {"status": "pending", "closed": True, "uma": "proposed", "settled_prices": True}),
    ({"closed": True, "outcomePrices": '["0.5", "0.5"]', "umaResolutionStatuses": '["resolved"]'}, {"status": "cancelled"}),
])
def test_only_final_polymarket_results_count(market: dict, expected: dict) -> None:
    assert resolution("m1", lambda _url: market, now=datetime(2026, 10, 23, 14, tzinfo=UTC)) == expected


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
    assert by_id["polymarket-m5"]["scored"] is False and hold["scored"] is True  # a disclosed leak counts in no score
    assert by_id["polymarket-m8"]["status"] == "cancelled"
    market = next(f for f in record["forecasters"] if f["method"] == "market_price_v1")
    assert (market["resolved"], market["right"]) == (1, 1)  # 0.78 on yes; m5 is unscored
    assert (record["summary"]["right"], record["summary"]["wrong"], record["summary"]["unscored"]) == (1, 0, 1)


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
        "volume_floor": 1_000, "market_price": 0.23, "market_url": "https://manifold.markets/u/f1", "method": "manifold_price_v1"}


def test_manifold_funnel_says_which_side_of_the_window_markets_miss() -> None:
    stats: dict[str, int] = {}
    assert [c["market_id"] for c in manifold_candidates(NOW, manifold_fetch, stats)] == ["f1", "f7"]
    assert stats["drop:window_closes_too_soon"] == 1 and "drop:window_closes_too_late" not in stats
    late = {**MANIFOLD_SEARCH, "world-default": [_manifold("f8", "Will it happen?", 0.5, closes="2026-12-01T00:00:00+00:00")]}
    stats = {}
    manifold_candidates(NOW, lambda url: late[url.split("topicSlug=")[1].split("&")[0]] if "search-markets" in url else None, stats)
    assert stats["drop:window_closes_too_late"] == 1


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
    assert selected["selection"] == "polymarket_selection_v7+manifold_selection_v3"
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


def test_record_skips_questions_without_research(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    directory = script.prepare(NOW, artifacts, fake_fetch)
    assert directory is not None
    ids = [q["id"] for q in json.loads((directory / "questions.json").read_text())["questions"]]
    answers = tmp_path / "claude.json"
    answers.write_text(json.dumps({"forecast_made_at": "2026-10-07T13:00:00Z", "forecasts": [
        {"id": i, "probability": 0.6, "rationale": "Because.", "sources": []} for i in ids[2:]]}))
    with pytest.raises(SystemExit, match="missing"):
        script.record(directory, answers)
    (directory / "research.json").write_text(json.dumps({"failed": [ids[0]]}))
    with pytest.raises(SystemExit, match="missing"):
        script.record(directory, answers, skip_failed=True)  # ids[1] is still uncovered
    script.record(directory, answers, skip={ids[1]}, skip_failed=True)
    recorded = json.loads((directory / "forecasts.json").read_text())
    assert [q["id"] for q in recorded["questions"]] == ids[2:]
    assert recorded["skipped"] == [{"id": ids[0], "reason": "research failed or timed out"}, {"id": ids[1], "reason": "no research"}]
    assert f"skipped {ids[1]} (no research)" in capsys.readouterr().out
    (directory / "research.json").write_text(json.dumps({"failed": {ids[0]: NO_TALLY}}))
    script.record(directory, answers, skip={ids[1]}, skip_failed=True)
    assert json.loads((directory / "forecasts.json").read_text())["skipped"][0] == {
        "id": ids[0], "reason": "no live tally for a count/total question"}
    with pytest.raises(SystemExit, match="unknown"):
        script.record(directory, answers, skip={"nope"})
    with pytest.raises(SystemExit, match="both skipped and forecast"):
        script.record(directory, answers, skip={ids[0], ids[2]})


def _cand(i: str, day: str, topic: str = "Politics", question: str | None = None, volume: float = 1000) -> dict:
    return {"id": i, "topic": topic, "question": question or f"Race {i}?", "closes_at": f"{day}T12:00:00Z", "volume": volume}


def _pend(i: str, day: str, topic: str = "Politics", question: str | None = None) -> dict:
    return {"id": i, "topic": topic, "question": question or f"Race {i}?", "resolution_date": day}


def test_cluster_cap_counts_pending_questions_within_a_day_in_the_same_topic() -> None:
    pending = [_pend(f"p{i}", "2026-11-03") for i in range(4)] + [_pend("far", "2026-11-09"), _pend("w", "2026-11-03", "World")]
    pool = [_cand("a", "2026-11-04"), _cand("b", "2026-11-04"), _cand("c", "2026-11-05"), _cand("w2", "2026-11-04", "World")]
    chosen = [c["id"] for c in select(pool, set(), pending=pending)]
    # one slot left in Politics (4 near + a = 5), so b is skipped; c is 2 days from the pending ones, so only a is near it
    assert chosen == ["a", "w2", "c"]
    # without pending questions nothing is capped below the topic limit
    assert len(select(pool, set())) == 4  # nothing pending: only the daily topic limit applies


def test_cluster_cap_ignores_resolved_far_and_other_topic_questions() -> None:
    pending = [_pend(f"p{i}", "2026-11-03") for i in range(5)]
    assert [c["id"] for c in select([_cand("far", "2026-11-05"), _cand("tech", "2026-11-03", "Tech")], set(), pending=pending)] == ["far", "tech"]
    assert select([_cand("near", "2026-11-04")], set(), pending=pending) == []


def test_cluster_cap_counts_questions_chosen_earlier_in_the_run() -> None:
    # TOPIC_LIMIT is 4 a day, so lower the room with 2 pending: 2 + 3 chosen = 5, the 4th is skipped
    pending = [_pend("p0", "2026-11-03"), _pend("p1", "2026-11-03")]
    pool = [_cand(f"c{i}", "2026-11-03", volume=100 - i) for i in range(4)] + [_cand("later", "2026-11-12", volume=1)]
    assert [c["id"] for c in select(pool, set(), pending=pending)] == ["c0", "c1", "c2", "later"]


def test_an_event_with_an_open_question_from_an_earlier_day_is_not_selected_again() -> None:
    def cand(i: str, event: str, volume: float) -> dict:
        return {**_cand(i, "2026-11-12", volume=volume), "market_url": f"https://polymarket.com/event/{event}"}

    pool = [cand("a2", "fed-dec", 900), cand("b", "cpi", 800), cand("c", "gdp", 700)]
    pending = [{**_pend("a1", "2026-11-12"), "market_url": "https://polymarket.com/event/fed-dec"},  # open: unrecorded day
               {**_pend("old", "2026-11-12"), "resolution_source": "https://polymarket.com/event/gdp"}]  # recorded question
    stats: dict[str, int] = {}
    assert [c["id"] for c in select(pool, set(), pending=pending, stats=stats)] == ["b"]
    assert stats["select:event_already_open:Politics"] == 2 and stats["select:chosen:Politics"] == 1
    assert [c["id"] for c in select(pool, set())] == ["a2", "b", "c"]  # nothing open: all three, as before
    assert [c["id"] for c in select(pool, set(), pending=[_pend("x", "2026-11-12")])] == ["a2", "b", "c"]  # no event on it


def test_pending_questions_carry_their_event_and_resolved_ones_free_it(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    one, two = artifacts / "prospective_markets_20261101", artifacts / "prospective_markets_20261102"
    one.mkdir(parents=True), two.mkdir()
    done = {"id": "d", "topic": "Economy", "question": "Q?", "resolution_date": "2026-11-05", "outcome": 1,
            "resolution_source": "https://polymarket.com/event/done"}
    held = {**done, "id": "h", "outcome": None, "resolution_source": "https://polymarket.com/event/held"}
    (one / "forecasts.json").write_text(json.dumps({"questions": [done, held]}))
    (two / "questions.json").write_text(json.dumps({"questions": [
        {"id": "u", "topic": "Tech", "question": "U?", "closes_at": "2026-11-09T00:00:00Z", "market_url": "https://polymarket.com/event/u"}]}))
    pending = script.pending_questions(artifacts)
    assert {q["id"] for q in pending} == {"h", "u"}
    pool = [{**_cand(i, "2026-11-12"), "market_url": f"https://polymarket.com/event/{i}"} for i in ("done", "held", "u", "fresh")]
    assert [c["id"] for c in select(pool, set(), pending=pending)] == ["done", "fresh"]  # a resolved event may come back


def test_asset_of_names_the_asset_in_the_title() -> None:
    assert asset_of("Will Bitcoin reach $120,000 on Nov 5?") == "bitcoin"
    assert asset_of("Will ETH be above $4k?") == "ethereum"
    assert asset_of("Will WTI crude close above $80?") == "oil"
    assert asset_of("Will the S&P 500 close higher?") == "s&p 500"
    assert asset_of("Will Nvidia (NVDA) close above $200?") == "nvda"
    assert asset_of("Will the Senate flip?") is None


def test_asset_cap_allows_two_pending_questions_per_asset_within_a_day() -> None:
    pending = [_pend("p0", "2026-11-05", "Crypto", "Will Bitcoin be above $100k on Nov 5?")]
    pool = [_cand("b1", "2026-11-05", "Crypto", "Will BTC be above $110k on Nov 5?", 900),
            _cand("b2", "2026-11-06", "Crypto", "Will Bitcoin dip to $90k by Nov 6?", 800),
            _cand("b3", "2026-11-12", "Crypto", "Will Bitcoin be above $100k on Nov 12?", 700),
            _cand("e1", "2026-11-05", "Crypto", "Will Ethereum be above $4k on Nov 5?", 600)]
    assert [c["id"] for c in select(pool, set(), pending=pending)] == ["b1", "b3", "e1"]


def test_prepare_passes_pending_questions_to_selection(tmp_path: Path) -> None:
    for day, outcome in (("20261101", None), ("20261102", 1)):
        directory = tmp_path / f"prospective_markets_{day}"
        directory.mkdir()
        (directory / "forecasts.json").write_text(json.dumps({"questions": [
            {"id": f"x{day}{i}", "topic": "Politics", "question": "Race?", "resolution_date": "2026-11-04", "outcome": outcome}
            for i in range(5)]}))
    open_ids = {q["id"] for q in script.pending_questions(tmp_path)}
    assert open_ids == {f"x20261101{i}" for i in range(5)}


def test_pool_counts_each_rule_and_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    stats: dict[str, int] = {}
    found = candidates(NOW, fake_fetch, stats)
    assert [c["id"] for c in found] == [c["id"] for c in candidates(NOW, fake_fetch)]  # same result with or without counting
    assert stats["events:economy"] == 3 and stats["markets:economy"] == 4
    assert stats["drop:sibling_in_same_event"] == 1 and stats["drop:volume"] == 1 and stats["drop:price_band"] == 1
    assert stats["drop:next_leader"] == 1 and stats["drop:settles_late"] == 1 and stats["drop:window"] == 1
    assert stats["drop:not_yes_no"] == 1 and stats["drop:event_already_taken_from_other_tag"] == 1

    chosen_stats: dict[str, int] = {}
    chosen = select(found, {"polymarket-m8"}, pending=[], stats=chosen_stats)
    assert len(chosen) == 2 and chosen_stats["select:pool"] == 3 and chosen_stats["select:already_seen"] == 1
    assert chosen_stats["select:chosen:Economy"] == 1 and chosen_stats["select:chosen:Politics"] == 1

    crowded = [{"id": f"c{i}", "topic": "Crypto", "volume": 100 - i, "question": "Will it rain?", "closes_at": "2026-10-15T00:00:00Z"}
               for i in range(7)]
    crowded_stats: dict[str, int] = {}
    select(crowded, set(), pending=crowded[:0], stats=crowded_stats)
    assert crowded_stats["select:chosen:Crypto"] == 4 and crowded_stats["select:left_at_topic_limit:Crypto"] == 3

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    result = script.pool(NOW, artifacts, manifold_fetch)
    assert result["polymarket"]["select:chosen:Economy"] >= 1 and result["manifold"]["candidates:economics-default"] >= 1
    assert "POLYMARKET funnel" in capsys.readouterr().out
    assert list(artifacts.iterdir()) == []  # writes nothing


def test_volume_floor_is_two_thousand_five_hundred_and_kept_per_question(tmp_path: Path) -> None:
    from forecastlab.market_questions import MIN_VOLUME, SELECTION_VERSION

    assert (MIN_VOLUME, SELECTION_VERSION) == (2_500, "polymarket_selection_v7")
    events = {"economy": [
        {"id": "a", "slug": "a", "markets": [_market("ma", "Will A happen?", 0.5, 2_600)]},
        {"id": "b", "slug": "b", "markets": [_market("mb", "Will B happen?", 0.5, 2_400)]}]}
    fetch = lambda url: events.get(url.split("tag_slug=")[1].split("&")[0], [])  # noqa: E731
    assert [c["market_id"] for c in candidates(NOW, fetch)] == ["ma"]
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    directory = script.prepare(NOW, artifacts, fetch)
    assert directory is not None
    snapshot = json.loads((directory / "market_snapshot.json").read_text())
    assert [m["volume_floor"] for m in snapshot["markets"]] == [2_500]
    answers = tmp_path / "claude.json"
    answers.write_text(json.dumps({"forecast_made_at": "2026-10-07T13:00:00Z", "forecasts": [
        {"id": "polymarket-ma", "probability": 0.4}]}))
    script.record(directory, answers)
    recorded = json.loads((directory / "forecasts.json").read_text())["questions"]
    assert recorded[0]["volume_floor"] == 2_500


def test_funnel_counts_volume_bands_and_fetch_window_position() -> None:
    events = {"crypto": [
        {"id": "a", "slug": "a", "markets": [_market("a1", "Q a1?", 0.5, 500), _market("a2", "Q a2?", 0.5, 1_500)]},
        {"id": "b", "slug": "b", "markets": [_market("b1", "Q b1?", 0.5, 3_000), _market("b2", "Q b2?", 0.99, 3_000)]},  # b2: price band
        {"id": "c", "slug": "c", "markets": [_market("c1", "Q c1?", 0.5, 7_000)]},
        {"id": "d", "slug": "d", "markets": [_market("d1", "Q d1?", 0.5, 60_000, ends="2026-12-31T00:00:00Z")]},  # far-dated
        {"id": "e", "slug": "e", "markets": [_market("e1", "Q e1?", 0.5, 60_000, ends="2026-10-08T00:00:00Z")]}]}  # too soon
    stats: dict[str, int] = {}
    candidates(NOW, lambda url: events.get(url.split("tag_slug=")[1].split("&")[0], []), stats)
    assert {k: v for k, v in stats.items() if k.startswith("volume_band:")} == {
        "volume_band:markets:under_1k": 1, "volume_band:markets:1k_2.5k": 1, "volume_band:markets:2.5k_5k": 1,
        "volume_band:markets:5k_10k": 1, "volume_band:events:under_1k": 1, "volume_band:events:1k_2.5k": 1,
        "volume_band:events:2.5k_5k": 1, "volume_band:events:5k_10k": 1}
    assert (stats["fetched:ends_in_window"], stats["fetched:ends_too_late"], stats["fetched:ends_too_soon"]) == (5, 1, 1)


def test_manifold_funnel_shows_whether_the_fetch_reaches_the_window() -> None:
    day = 86_400_000
    now_ms = int(NOW.timestamp() * 1000)
    rows = [_manifold(f"r{i}", "Will it happen?", 0.5, closes="2026-12-01T00:00:00+00:00") | {"closeTime": now_ms + d * day}
            for i, d in enumerate([-1, 1, 5, 30, 40])]
    stats: dict[str, int] = {}
    manifold_candidates(NOW, lambda url: rows if "search-markets" in url else None, stats)
    topic = "economics-default"
    assert [stats[f"reach:{topic}:{k}"] for k in ("already_past", "0_to_2_days", "in_window", "after_window", "sorted_ascending")] == [1, 1, 1, 2, 1]
    rows.reverse()
    stats = {}
    manifold_candidates(NOW, lambda url: rows if "search-markets" in url else None, stats)
    assert stats.get(f"reach:{topic}:sorted_ascending", 0) == 0
