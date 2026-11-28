"""Binary-question forecasting bot for the Metaculus FutureEval tournament.

API facts come from the official ``forecasting-tools`` SDK (``helpers/metaculus_client.py`` v0.3.7):

* base ``https://www.metaculus.com/api``, header ``Authorization: Token <token>``
* ``GET /posts/?tournaments=<id|slug>&statuses=open&forecast_type=binary&limit&offset&order_by``
* ``POST /questions/forecast/`` with ``[{"question": <question id>, "source": "api", "probability_yes": p}]``
* ``POST /comments/create/`` with ``{"on_post": <post id>, "text", "is_private": true, "included_forecast": true}``

The forecaster only ever sees an allowlisted view of the question (:func:`build_question_view`) plus a research
digest built from searches that exclude forecasting and prediction-market sites. No aggregate, community
prediction, forecaster count or price reaches a model.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

from forecastlab.hashing import redact_secrets
from forecastlab.providers.openai_compatible import DEFAULT_OPENROUTER_BASE

METACULUS_BASE = "https://www.metaculus.com/api"
TAVILY_URL = "https://api.tavily.com/search"
FALL_2026_TOURNAMENT = "33121"  # slug fall-futureeval-2026
MINIBENCH_TOURNAMENT = "minibench"
TESTING_TOURNAMENT = "bot-testing-area"
DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"  # OpenRouter slug; override with --model / METACULUS_BOT_MODEL
MIN_PROBABILITY = 0.01
MAX_PROBABILITY = 0.99
PAGE_SIZE = 100
MAX_PAGES = 5
# Used only when OpenRouter reports no cost: USD per million tokens (input, output).
FALLBACK_RATE_PER_MTOK = (3.0, 15.0)

# Forecasting and prediction-market sites: searches never return them, so their odds cannot leak into research.
EXCLUDED_DOMAINS = [
    "metaculus.com",
    "polymarket.com",
    "manifold.markets",
    "kalshi.com",
    "predictit.org",
    "goodjudgment.com",
    "gjopen.com",
    "insightprediction.com",
    "betfair.com",
    "smarkets.com",
    "augur.net",
    "hypermind.com",
    "infer-pub.com",
    "forecastex.com",
    "limitless.exchange",
    "myriad.markets",
    "futuur.com",
    "oddschecker.com",
]

# The only question fields a model may see (Metaculus question json key -> view key).
QUESTION_VIEW_FIELDS = {
    "title": "title",
    "description": "description",
    "resolution_criteria": "resolution_criteria",
    "fine_print": "fine_print",
    "open_time": "open_time",
    "scheduled_close_time": "close_time",
    "scheduled_resolve_time": "scheduled_resolve_time",
}

QUERY_PROMPT = """You help a forecaster research a yes/no question. Today is {today}.
Write 3 to 4 web search queries that would surface the facts needed to forecast it: the current state of affairs,
recent news, scheduled events and dates, and historical base rates. Search for facts and data, never for forecasts,
odds, or prediction markets. Return JSON only: {{"queries": ["...", "..."]}}

{question}
"""

DIGEST_PROMPT = """You write the research digest for a forecaster. Today is {today}.
Use only the search results below. Write at most 450 words covering: key current facts, relevant dates and
deadlines, base rates and historical analogues, and what could change the outcome. Cite sources by URL.
Drop any probability, odds, price or forecast that comes from a prediction market, betting site or forecasting
platform or aggregator (Metaculus, Polymarket, Manifold, Kalshi, PredictIt, Good Judgment and the like); mention
nothing of them. Say plainly when the results are thin or conflicting.

{question}

SEARCH RESULTS
{results}
"""

FORECAST_PROMPT = """You are a careful superforecaster answering a yes/no question. Today is {today}.
You see only the question and a research digest. Reason about the status quo and how slowly the world changes,
base rates for similar events, the time left until the question closes, and the exact wording of the resolution
criteria. Weigh the evidence in the digest by its reliability, state what is unknown, and avoid both
overconfidence and reflexive 50% answers; extreme values are right only with strong evidence.

{question}

RESEARCH DIGEST
{digest}

Return JSON only, with this shape:
{{"probability": <number between 0.01 and 0.99, the chance the question resolves YES>,
"reasoning": "<concise reasoning, under 300 words>"}}
"""


class MetaculusBotError(Exception):
    pass


@dataclass
class Question:
    post_id: int
    question_id: int
    url: str
    view: dict[str, str]
    already_forecasted: bool
    forecast_times: list[float] = field(default_factory=list)


@dataclass
class CostTracker:
    ceiling_usd: float
    spent_usd: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    @property
    def exceeded(self) -> bool:
        return self.spent_usd >= self.ceiling_usd


@dataclass
class QuestionResult:
    post_id: int
    question_id: int
    title: str
    probability: float | None = None
    posted: bool = False
    dry_run: bool = False
    error: str | None = None
    queries: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


@dataclass
class RunConfig:
    tournaments: list[str] = field(default_factory=lambda: [FALL_2026_TOURNAMENT])
    max_questions: int = 5
    max_per_24h: int = 30
    cost_ceiling_usd: float = 1.5
    dry_run: bool = False
    model: str = DEFAULT_MODEL
    queries_max_tokens: int = 400
    digest_max_tokens: int = 1500
    forecast_max_tokens: int = 1800
    search_results_per_query: int = 5


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def build_question_view(question_json: dict[str, Any], today: datetime | None = None) -> dict[str, str]:
    """Allowlisted fields only. Anything else in the API payload (aggregations, counts, scores) is dropped."""
    today = today or datetime.now(UTC)
    view: dict[str, str] = {"today": today.date().isoformat()}
    for source_key, view_key in QUESTION_VIEW_FIELDS.items():
        value = question_json.get(source_key)
        if isinstance(value, str) and value.strip():
            view[view_key] = value.strip()
    return view


def render_question_view(view: dict[str, str]) -> str:
    labels = [
        ("title", "QUESTION"),
        ("description", "BACKGROUND"),
        ("resolution_criteria", "RESOLUTION CRITERIA"),
        ("fine_print", "FINE PRINT"),
        ("open_time", "OPENED"),
        ("close_time", "CLOSES"),
        ("scheduled_resolve_time", "SCHEDULED TO RESOLVE"),
    ]
    return "\n\n".join(f"{label}:\n{view[key]}" for key, label in labels if key in view)


def parse_forecast_json(text: str) -> tuple[float, str]:
    """Pull {probability, reasoning} out of a model reply; clip the probability to [0.01, 0.99]."""
    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or "probability" not in data:
            continue
        probability = _coerce_probability(data["probability"])
        if probability is None:
            continue
        return probability, str(data.get("reasoning") or "").strip()
    match = re.search(r'"?probability"?\s*[:=]\s*"?([0-9.]+%?)', text)
    if match:
        probability = _coerce_probability(match.group(1))
        if probability is not None:
            return probability, text.strip()
    raise MetaculusBotError("could not parse a probability from the model reply")


def _coerce_probability(value: Any) -> float | None:
    try:
        if isinstance(value, str):
            value = value.strip()
            number = float(value[:-1]) / 100 if value.endswith("%") else float(value)
        else:
            number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    if number > 1.0:  # a percentage such as 35
        number /= 100
    return min(MAX_PROBABILITY, max(MIN_PROBABILITY, number))


def parse_queries(text: str, limit: int = 4) -> list[str]:
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1])
        raw = data.get("queries", []) if isinstance(data, dict) else []
    except json.JSONDecodeError:
        raw = []
    queries = [q.strip() for q in raw if isinstance(q, str) and q.strip()]
    return queries[:limit]


class MetaculusClient:
    def __init__(self, token: str, client: httpx.Client, base_url: str = METACULUS_BASE) -> None:
        self.token = token
        self.client = client
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept-Language": "en"}
        if self.token:
            headers["Authorization"] = f"Token {self.token}"
        return headers

    def list_open_binary(self, tournament: str, today: datetime | None = None) -> list[Question]:
        questions: list[Question] = []
        for page in range(MAX_PAGES):
            response = self.client.get(
                f"{self.base_url}/posts/",
                params={
                    "tournaments": tournament,
                    "statuses": "open",
                    "forecast_type": "binary",
                    "limit": PAGE_SIZE,
                    "offset": page * PAGE_SIZE,
                    "order_by": "-published_time",
                    "with_cp": "true",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            results = response.json().get("results") or []
            for post in results:
                question = self.parse_post(post, today)
                if question is not None:
                    questions.append(question)
            if len(results) < PAGE_SIZE:
                break
        return questions

    @staticmethod
    def parse_post(post: dict[str, Any], today: datetime | None = None) -> Question | None:
        """Plain open binary questions only. Groups, conditionals, notebooks and anything unclear are skipped."""
        question_json = post.get("question")
        if not isinstance(question_json, dict) or "notebook" in post:
            return None
        if question_json.get("type") != "binary" or question_json.get("status") != "open":
            return None
        post_id, question_id = post.get("id"), question_json.get("id")
        if not isinstance(post_id, int) or not isinstance(question_id, int):
            return None
        mine = question_json.get("my_forecasts")
        if not isinstance(mine, dict):
            # Cannot tell whether this account already forecast it; fail closed rather than forecast twice.
            return None
        history = mine.get("history") or []
        times = [float(item["start_time"]) for item in history if isinstance(item, dict) and item.get("start_time")]
        return Question(
            post_id=post_id,
            question_id=question_id,
            url=f"https://www.metaculus.com/questions/{post_id}",
            view=build_question_view(question_json, today),
            already_forecasted=len(history) > 0,
            forecast_times=times,
        )

    def post_forecast(self, question_id: int, probability: float) -> None:
        response = self.client.post(
            f"{self.base_url}/questions/forecast/",
            json=[{"question": question_id, "source": "api", "probability_yes": probability}],
            headers=self._headers(),
        )
        response.raise_for_status()

    def post_private_comment(self, post_id: int, text: str) -> None:
        response = self.client.post(
            f"{self.base_url}/comments/create/",
            json={"on_post": post_id, "text": text, "is_private": True, "included_forecast": True},
            headers=self._headers(),
        )
        response.raise_for_status()


class OpenRouterChat:
    def __init__(self, api_key: str, client: httpx.Client, model: str, costs: CostTracker) -> None:
        self.api_key = api_key
        self.client = client
        self.model = model
        self.costs = costs

    def complete(self, prompt: str, max_tokens: int) -> str:
        response = self.client.post(
            f"{DEFAULT_OPENROUTER_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.3,
                "usage": {"include": True},
            },
        )
        if response.status_code >= 400:
            raise MetaculusBotError(f"OpenRouter HTTP {response.status_code}: {redact_secrets(response.text[:200])}")
        data = response.json()
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        cost = usage.get("cost")
        if not isinstance(cost, int | float):
            rate_in, rate_out = FALLBACK_RATE_PER_MTOK
            cost = (prompt_tokens * rate_in + completion_tokens * rate_out) / 1_000_000
        self.costs.spent_usd += float(cost)
        self.costs.prompt_tokens += prompt_tokens
        self.costs.completion_tokens += completion_tokens
        self.costs.calls += 1
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError) as exc:
            raise MetaculusBotError("OpenRouter reply had no message content") from exc


def _is_excluded(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in EXCLUDED_DOMAINS)


def tavily_search(client: httpx.Client, api_key: str, query: str, max_results: int = 5) -> list[dict[str, str]]:
    response = client.post(
        TAVILY_URL,
        json={
            "api_key": api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
            "exclude_domains": EXCLUDED_DOMAINS,
        },
    )
    if response.status_code >= 400:
        raise MetaculusBotError(f"Tavily HTTP {response.status_code}: {redact_secrets(response.text[:200])}")
    hits = []
    for item in response.json().get("results") or []:
        url = str(item.get("url") or "")
        if url and not _is_excluded(url):
            hits.append({"title": str(item.get("title") or url), "url": url, "content": str(item.get("content") or "")})
    return hits


def format_search_results(results: list[dict[str, str]], per_hit_chars: int = 900) -> str:
    seen: set[str] = set()
    lines = []
    for hit in results:
        if hit["url"] in seen:
            continue
        seen.add(hit["url"])
        lines.append(f"- {hit['title']} ({hit['url']})\n  {hit['content'][:per_hit_chars]}")
    return "\n".join(lines) or "(no results)"


def build_comment(probability: float, reasoning: str, digest: str, model: str) -> str:
    return (
        f"Forecast: {probability:.1%} YES\n\n"
        f"## Reasoning\n{reasoning or '(none)'}\n\n"
        f"## Research highlights\n{digest.strip() or '(none)'}\n\n"
        f"Automated forecast by an AI bot ({model}). The forecaster saw only the question text and web research "
        f"that excluded forecasting and prediction-market sites."
    )[:20000]


def forecast_question(
    question: Question,
    chat: OpenRouterChat,
    http: httpx.Client,
    tavily_key: str,
    config: RunConfig,
    result: QuestionResult,
) -> tuple[float, str, str]:
    rendered = render_question_view(question.view)
    today = question.view["today"]
    queries = parse_queries(
        chat.complete(QUERY_PROMPT.format(today=today, question=rendered), config.queries_max_tokens)
    ) or [question.view.get("title", "")]
    result.queries = queries
    hits: list[dict[str, str]] = []
    for query in queries:
        try:
            hits.extend(tavily_search(http, tavily_key, query, config.search_results_per_query))
        except (MetaculusBotError, httpx.HTTPError) as exc:
            result.error = f"search failed: {type(exc).__name__}"
    digest = chat.complete(
        DIGEST_PROMPT.format(today=today, question=rendered, results=format_search_results(hits)),
        config.digest_max_tokens,
    ).strip()
    # Blind forecast: separate call, allowlisted question view and the digest only.
    reply = chat.complete(
        FORECAST_PROMPT.format(today=today, question=rendered, digest=digest), config.forecast_max_tokens
    )
    probability, reasoning = parse_forecast_json(reply)
    return probability, reasoning, digest


def run(
    config: RunConfig,
    *,
    metaculus_token: str,
    openrouter_key: str,
    tavily_key: str,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One bot run. Returns the run log. Skips when no token is set (and, for a dry run, lists nothing)."""
    now = now or datetime.now(UTC)
    log: dict[str, Any] = {
        "started_at": now.isoformat(),
        "dry_run": config.dry_run,
        "tournaments": config.tournaments,
        "model": config.model,
        "max_questions": config.max_questions,
        "max_per_24h": config.max_per_24h,
        "cost_ceiling_usd": config.cost_ceiling_usd,
        "results": [],
    }
    if not metaculus_token:
        log["skipped"] = "METACULUS_TOKEN not set"
        return log
    if not (openrouter_key and tavily_key) and not config.dry_run:
        log["skipped"] = "model or search key missing"
        return log
    costs = CostTracker(ceiling_usd=config.cost_ceiling_usd)
    owns_client = client is None
    http = client or httpx.Client(timeout=60.0)
    try:
        metaculus = MetaculusClient(metaculus_token, http)
        candidates: dict[int, Question] = {}
        try:
            for tournament in config.tournaments:
                for question in metaculus.list_open_binary(tournament, now):
                    candidates.setdefault(question.post_id, question)
        except httpx.HTTPError as exc:
            log["error"] = f"listing questions failed: {type(exc).__name__}"
            # A rejected token (401/403) is fatal and fails the run; network trouble and 5xx just end it.
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else 0
            log["fatal"] = status in (401, 403)
            return log
        cutoff = (now - timedelta(hours=24)).timestamp()
        recent = sum(1 for q in candidates.values() for t in q.forecast_times if t >= cutoff)
        todo = [q for q in candidates.values() if not q.already_forecasted]
        allowance = max(0, min(config.max_questions, config.max_per_24h - recent))
        log.update(
            open_binary_questions=len(candidates),
            already_forecasted=len(candidates) - len(todo),
            forecasts_last_24h=recent,
            allowance=allowance,
        )
        chat = OpenRouterChat(openrouter_key, http, config.model, costs)
        listing_only = config.dry_run and not (openrouter_key and tavily_key)
        if listing_only:
            log["note"] = "dry run without model or search keys: listed questions only"
        for question in [] if listing_only else todo[:allowance]:
            if costs.exceeded:
                log["stopped"] = "cost ceiling reached"
                break
            result = QuestionResult(
                post_id=question.post_id,
                question_id=question.question_id,
                title=question.view.get("title", ""),
                dry_run=config.dry_run,
            )
            spent_before = costs.spent_usd
            try:
                probability, reasoning, digest = forecast_question(question, chat, http, tavily_key, config, result)
                result.probability = probability
                if not config.dry_run:
                    metaculus.post_forecast(question.question_id, probability)
                    result.posted = True
                    metaculus.post_private_comment(
                        question.post_id, build_comment(probability, reasoning, digest, config.model)
                    )
            except (MetaculusBotError, httpx.HTTPError) as exc:
                result.error = redact_secrets(f"{type(exc).__name__}: {exc}")[:300]
            result.cost_usd = round(costs.spent_usd - spent_before, 6)
            log["results"].append(result.__dict__)
    finally:
        if owns_client:
            http.close()
    log["usage"] = {
        "calls": costs.calls,
        "prompt_tokens": costs.prompt_tokens,
        "completion_tokens": costs.completion_tokens,
        "cost_usd": round(costs.spent_usd, 6),
    }
    log["finished_at"] = datetime.now(UTC).isoformat()
    return log


def write_run_log(log: dict[str, Any], out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = os.path.join(out_dir, f"metaculus-bot-{stamp}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(log, handle, indent=2, default=str)
    return path
