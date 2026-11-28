from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from forecastlab.metaculus_bot import (
    EXCLUDED_DOMAINS,
    MetaculusBotError,
    MetaculusClient,
    RunConfig,
    build_question_view,
    parse_forecast_json,
    run,
)

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def post(post_id: int, *, qtype: str = "binary", forecasts: list[float] | None = None, status: str = "open") -> dict:
    return {
        "id": post_id,
        "nr_forecasters": 123,
        "question": {
            "id": post_id + 1000,
            "type": qtype,
            "status": status,
            "title": f"Will thing {post_id} happen?",
            "description": "Background text",
            "resolution_criteria": "Resolves YES if it happens",
            "fine_print": "Fine print",
            "open_time": "2026-11-01T00:00:00Z",
            "scheduled_close_time": "2026-12-31T00:00:00Z",
            "scheduled_resolve_time": "2027-01-10T00:00:00Z",
            "aggregations": {"recency_weighted": {"latest": {"centers": [0.77]}}},
            "my_forecasts": {"history": [{"start_time": t} for t in (forecasts or [])], "latest": None},
        },
    }


class Fake:
    """Mock transport for Metaculus, OpenRouter and Tavily."""

    def __init__(self, posts: list[dict], forecast_reply: str | None = None, cost: float = 0.01) -> None:
        self.posts = posts
        self.calls: list[tuple[str, str, Any]] = []
        self.forecast_reply = forecast_reply or '{"probability": 0.3, "reasoning": "base rates say so"}'
        self.cost = cost

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, url, body))
        if "metaculus.com/api/posts/" in url:
            return httpx.Response(200, json={"results": self.posts})
        if url.endswith("/questions/forecast/") or url.endswith("/comments/create/"):
            return httpx.Response(201, json={})
        if "api.tavily.com" in url:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "News", "url": "https://example.org/a", "content": "facts"},
                        {"title": "Mkt", "url": "https://polymarket.com/x", "content": "odds 60%"},
                    ]
                },
            )
        if "openrouter.ai" in url:
            prompt = body["messages"][0]["content"]
            if "search queries" in prompt:
                text = '{"queries": ["q one", "q two"]}'
            elif "research digest for a forecaster" in prompt:
                text = "Digest: facts https://example.org/a"
            else:
                text = self.forecast_reply
            usage = {"prompt_tokens": 100, "completion_tokens": 50, "cost": self.cost}
            return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "usage": usage})
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def of(self, method: str, suffix: str) -> list[Any]:
        return [b for m, u, b in self.calls if m == method and suffix in u]


def go(fake: Fake, config: RunConfig | None = None, token: str = "tok") -> dict:
    return run(
        config or RunConfig(),
        metaculus_token=token,
        openrouter_key="or",
        tavily_key="tv",
        client=fake.client(),
        now=NOW,
    )


def test_no_token_skips_without_network(tmp_path: Path) -> None:
    fake = Fake([post(1)])
    log = go(fake, token="")
    assert log["skipped"] == "METACULUS_TOKEN not set"
    assert fake.calls == []
    env = {"PATH": "/usr/bin:/bin", "METACULUS_TOKEN": ""}
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "metaculus_bot.py"), "--dry-run", "--out-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert out.stdout.strip() == "METACULUS_TOKEN not set; skipping"


def test_already_forecast_non_binary_and_unknown_are_skipped() -> None:
    odd = post(5)
    del odd["question"]["my_forecasts"]
    posts = [
        post(1, forecasts=[NOW.timestamp() - 10 * 86400]),
        post(2, qtype="numeric"),
        post(3, status="closed"),
        odd,
        post(4),
    ]
    fake = Fake(posts)
    log = go(fake)
    assert [r["post_id"] for r in log["results"]] == [4]
    assert log["already_forecasted"] == 1


def test_allowlist_strips_aggregations() -> None:
    payload = post(9)["question"]
    payload["aggregations"] = {"x": 1}
    payload["nr_forecasters"] = 99
    view = build_question_view(payload, NOW)
    assert set(view) == {
        "today",
        "title",
        "description",
        "resolution_criteria",
        "fine_print",
        "open_time",
        "close_time",
        "scheduled_resolve_time",
    }
    assert "0.77" not in json.dumps(view) and "aggregations" not in json.dumps(view)
    question = MetaculusClient.parse_post(post(9), NOW)
    assert question is not None and "0.77" not in json.dumps(question.view)


def test_models_never_see_community_prediction() -> None:
    fake = Fake([post(1)])
    go(fake)
    prompts = json.dumps(fake.of("POST", "openrouter.ai"))
    assert "0.77" not in prompts and "123" not in prompts and "aggregations" not in prompts


def test_probability_parsing_and_clipping() -> None:
    assert parse_forecast_json('{"probability": 0.42, "reasoning": "r"}') == (0.42, "r")
    assert parse_forecast_json('Here:\n```json\n{"probability": "35%", "reasoning": "r"}\n```')[0] == 0.35
    assert parse_forecast_json('{"probability": 0.0, "reasoning": ""}')[0] == 0.01
    assert parse_forecast_json('{"probability": 1, "reasoning": ""}')[0] == 0.99
    assert parse_forecast_json('{"probability": 72, "reasoning": ""}')[0] == 0.72
    with pytest.raises(MetaculusBotError):
        parse_forecast_json("no idea")


def test_dry_run_makes_no_post() -> None:
    fake = Fake([post(1)])
    log = go(fake, RunConfig(dry_run=True))
    assert log["results"][0]["probability"] == 0.3 and log["results"][0]["posted"] is False
    assert fake.of("POST", "/questions/forecast/") == []
    assert fake.of("POST", "/comments/create/") == []


def test_forecast_and_private_comment_payloads() -> None:
    fake = Fake([post(1)], forecast_reply='{"probability": 0.999, "reasoning": "sure"}')
    go(fake)
    assert fake.of("POST", "/questions/forecast/") == [
        [{"question": 1001, "source": "api", "probability_yes": 0.99}]
    ]
    comment = fake.of("POST", "/comments/create/")[0]
    assert comment["on_post"] == 1 and comment["is_private"] is True and comment["included_forecast"] is True
    assert "sure" in comment["text"] and "Digest" in comment["text"]
    auth = [r for r in fake.calls if "/questions/forecast/" in r[1]]
    assert auth  # request made; header format checked below


def test_auth_header_format() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={})

    client = MetaculusClient("abc", httpx.Client(transport=httpx.MockTransport(handler)))
    client.post_forecast(1, 0.5)
    assert seen[0].headers["Authorization"] == "Token abc"


def test_per_run_question_cap() -> None:
    fake = Fake([post(i) for i in range(1, 9)])
    log = go(fake, RunConfig(max_questions=3))
    assert len(log["results"]) == 3


def test_rolling_24h_cap_uses_own_forecast_times() -> None:
    recent = NOW.timestamp() - 3600
    posts = [post(i, forecasts=[recent]) for i in range(1, 4)] + [post(i) for i in range(10, 15)]
    fake = Fake(posts)
    log = go(fake, RunConfig(max_questions=5, max_per_24h=5))
    assert log["forecasts_last_24h"] == 3
    assert len(log["results"]) == 2


def test_cost_ceiling_stops_processing() -> None:
    fake = Fake([post(i) for i in range(1, 6)], cost=0.4)  # 3 calls x $0.4 = $1.2 per question
    log = go(fake, RunConfig(cost_ceiling_usd=1.0))
    assert len(log["results"]) == 1
    assert log["stopped"] == "cost ceiling reached"
    assert log["usage"]["cost_usd"] == pytest.approx(1.2)


def test_tavily_excludes_forecasting_sites_and_drops_their_hits() -> None:
    fake = Fake([post(1)])
    go(fake)
    searches = fake.of("POST", "api.tavily.com")
    assert searches and all(s["exclude_domains"] == EXCLUDED_DOMAINS for s in searches)
    for domain in ("metaculus.com", "polymarket.com", "manifold.markets", "kalshi.com", "predictit.org"):
        assert domain in searches[0]["exclude_domains"]
    digest_prompt = [b for b in fake.of("POST", "openrouter.ai") if "research digest for a forecaster" in json.dumps(b)]
    assert "polymarket" not in json.dumps(digest_prompt)


def test_error_in_one_question_does_not_stop_run() -> None:
    fake = Fake([post(1), post(2)], forecast_reply="garbage")
    log = go(fake)
    assert len(log["results"]) == 2 and all(r["error"] for r in log["results"])
    assert fake.of("POST", "/questions/forecast/") == []


def test_listing_failure_is_logged_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    log = run(
        RunConfig(),
        metaculus_token="bad",
        openrouter_key="or",
        tavily_key="tv",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=NOW,
    )
    assert log["fatal"] is True and "listing questions failed" in log["error"]
