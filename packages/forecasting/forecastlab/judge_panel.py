"""A panel of cheap open-weights models that forecast from Claude's evidence brief.

Claude does the expensive part, the web research, and writes a neutral evidence brief per
question: dated facts, base rates and sources, with no probability and no lean. Each panel
member then reads the question, its resolution rule and the brief, and answers with a
probability. Members do not see each other, Claude's forecast or any market price, so
each one is a separate forecaster in the median call.

Spending is capped per UTC day (``daily_budget_usd`` in ``configs/judge_panel.json``).
Before every call the worst-case cost of that call, from OpenRouter's published prices,
must fit under the cap; afterwards the amount OpenRouter reports is what counts.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

OPENROUTER = "https://openrouter.ai/api/v1"
# Characters per token used to bound a prompt's size before it is sent; a low ratio overestimates.
CHARS_PER_TOKEN = 3

Post = Callable[[dict[str, Any]], dict[str, Any]]


class Member(BaseModel):
    method: str  # its label and description live in forecastlab_api.track_record.FORECASTERS
    model: str  # OpenRouter model id


class PanelConfig(BaseModel):
    version: str
    prompt_version: str
    daily_budget_usd: float = Field(gt=0, le=5)
    max_tokens: int = Field(gt=0, le=20_000)
    members: list[Member]


class JudgeAnswer(BaseModel):
    probability: float = Field(ge=0, le=1)
    reasoning: str = Field(min_length=1)


@dataclass(frozen=True)
class PanelQuestion:
    id: str
    question: str
    criteria: str
    resolves_on: str
    cutoff: datetime
    brief: str


@dataclass(frozen=True)
class Price:
    prompt: float  # USD per token
    completion: float


def load_config(path: Path) -> PanelConfig:
    return PanelConfig.model_validate_json(path.read_text())


SYSTEM = (
    "You are a careful, well-calibrated forecaster. You estimate the probability that a question "
    "resolves Yes, using only the evidence brief you are given and general knowledge. Start from "
    "the base rate, adjust for the specific evidence, and avoid overconfidence: use extreme "
    "probabilities only when the evidence is decisive. Reply with JSON only."
)


def prompt(question: PanelQuestion, now: datetime) -> str:
    return (
        f"Today is {now.strftime('%Y-%m-%d %H:%M UTC')}.\n\n"
        f"Question: {question.question}\n\n"
        f"How it resolves: {question.criteria}\n"
        f"Resolution date: {question.resolves_on}\n\n"
        f"Evidence brief (written by a researcher today; facts and sources only):\n{question.brief}\n\n"
        'Answer with a JSON object: {"probability": <number from 0 to 1 that the answer is Yes>, '
        '"reasoning": "<two to four sentences: base rate, the evidence that moved you, what could change it>"}'
    )


def parse_answer(text: str) -> JudgeAnswer:
    """The JSON object in a reply, tolerating code fences or text around it."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in the reply")
    return JudgeAnswer.model_validate(json.loads(match.group(0)))


def prices(models: list[dict[str, Any]]) -> dict[str, Price]:
    """Per-token prices from OpenRouter's public model list."""
    found = {}
    for model in models:
        pricing = model.get("pricing") or {}
        try:
            found[model["id"]] = Price(float(pricing["prompt"]), float(pricing["completion"]))
        except (KeyError, TypeError, ValueError):
            continue
    return found


def worst_case_cost(prompt_chars: int, max_tokens: int, price: Price) -> float:
    return (prompt_chars / CHARS_PER_TOKEN) * price.prompt + max_tokens * price.completion


class Budget:
    """A daily cap: a call may start only if its worst case fits in what is left."""

    def __init__(self, cap_usd: float, spent_usd: float) -> None:
        self.cap, self.spent = cap_usd, spent_usd

    def allows(self, worst_case_usd: float) -> bool:
        return self.spent + worst_case_usd <= self.cap

    def charge(self, cost_usd: float) -> None:
        self.spent += cost_usd


def ask(member: Member, question: PanelQuestion, *, now: datetime, post: Post, max_tokens: int,
        price: Price) -> dict[str, Any]:
    """One judge call. Returns an attempt record with the forecast or the error, and its cost."""
    user = prompt(question, now)
    body = {"model": member.model, "max_tokens": max_tokens, "temperature": 0,
            "response_format": {"type": "json_object"}, "usage": {"include": True},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}
    attempt: dict[str, Any] = {"id": question.id, "method": member.method, "model": member.model,
                               "at": now.isoformat(), "ok": False, "cost_usd": 0.0}
    try:
        response = post(body)
    except Exception as exc:  # noqa: BLE001 - the error is recorded and the run moves on
        return attempt | {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    usage = response.get("usage") or {}
    reported = usage.get("cost")
    cost = (float(reported) if isinstance(reported, int | float) and not isinstance(reported, bool) else
            int(usage.get("prompt_tokens") or 0) * price.prompt + int(usage.get("completion_tokens") or 0) * price.completion)
    attempt |= {"cost_usd": round(cost, 6), "served_model": response.get("model"),
                "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")}
    try:
        content = response["choices"][0]["message"]["content"] or ""
        answer = parse_answer(content)
    except Exception as exc:  # noqa: BLE001 - an unusable reply is recorded, not retried
        return attempt | {"error": f"unusable reply: {str(exc)[:200]}"}
    return attempt | {"ok": True, "probability": answer.probability, "reasoning": answer.reasoning.strip()}


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])[:160]
    except (ValueError, KeyError, TypeError):
        return response.text[:160]


def openrouter_post(api_key: str, *, timeout: float = 180.0) -> Post:
    """POST to OpenRouter's chat completions, retrying once on a rate limit or server error."""

    def post(body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {api_key}", "HTTP-Referer": "https://github.com/nikoamoretti/forecastlab",
                   "X-Title": "ForecastLab judge panel"}
        for attempt in range(2):
            response = httpx.post(f"{OPENROUTER}/chat/completions", json=body, headers=headers, timeout=timeout)
            if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                time.sleep(20)
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"OpenRouter returned HTTP {response.status_code}: {_error_message(response)}")
            data: dict[str, Any] = response.json()
            if "error" in data:
                raise RuntimeError(f"OpenRouter error: {str(data['error'].get('message', ''))[:160]}")
            return data
        raise RuntimeError("unreachable")

    return post


def fetch_models() -> list[dict[str, Any]]:
    response = httpx.get(f"{OPENROUTER}/models", timeout=60)
    response.raise_for_status()
    data: list[dict[str, Any]] = response.json()["data"]
    return data
