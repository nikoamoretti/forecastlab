"""The owner's track record: every recorded forecast, its outcome, and whether the call was right.

Built from the committed prospective artifacts (``artifacts/prospective_*``): cohorts with
``scores.json``, and ``forecasts.json`` sets of general questions (the GPU set and the
questions the owner asked Claude, whose ids match their ``question_requests`` entries). The API deployment does not ship ``artifacts/``, so
``scripts/build_track_record.py`` writes the result to ``data/track_record.json`` and
``GET /api/track-record`` serves that file.

"Our call" for a question is the median of every forecaster's probability on it (the
``combined_median_v1`` rule, adopted 2026-10-07 in place of "Claude first"; it gives the
same call on all four questions decided by then). Questions first forecast on or after
WEIGHTED_FROM use ``combined_weighted_v1`` instead: a weighted median in which each forecaster
counts by its record on the questions decided so far (see ``_weights``). That call is computed
once, from the record as it stood then, and frozen in the question's directory as ``calls.json``,
so later results never rewrite an earlier call. Questions first forecast on or after CLAUDE_CALL_FROM
use ``claude_call_v1``: Claude's own probability, frozen in ``calls.json`` the same way (owner's plan of
2026-10-31, part 3: drop the judges once Claude alone beats the combined call; on 2026-11-04 Claude alone
scored 0.207 on the 173 decided questions against 0.216 for the combined call). A question with no Claude
forecast falls back to the weighted call. A call is right when it put more than
50% on what happened; an exact 50% is a toss-up and counts in the accuracy score but not
in the right/wrong tally. Each forecaster keeps its own record, so the page shows whether
the combination beats its members.

Macro questions carry ``series`` (the indicator) and ``series_label`` so the page can show
threshold and date siblings of one indicator as a single expandable row.

Claude's own probability also gets a confidence correction (``claude_calibrated_v1``, from
CALIBRATED_FROM): a logistic fit of outcome on logit(p) over Claude's resolved, scored forecasts
on questions that were decided before the forecast day, with a prior pulling (slope, intercept)
toward (1, 0). It is computed once, when the question is first built, and frozen as
``calibrated.json`` next to ``calls.json``. It is scored like a benchmark and never enters the weighted
call. Questions first forecast on or after CORRECTED_CALL_FROM use it as our call
(``claude_corrected_call_v1``): on 2026-11-05, over 168 decided questions, Claude's forecasts averaged
42% yes while 53% resolved yes, and a correction fitted only on results known before each forecast
day would have scored 0.195 against 0.210 for Claude as stated, on the 137 questions it could cover.
That gain did not hold out of sample: a forward-walk test on 2026-11-09 scored Claude as stated 0.2135 and the
corrected forecast 0.2252 on 109 questions (difference +0.0117, 95% interval +0.0009 to +0.0206). Questions first
forecast on or after ANCHORED_FROM (2026-11-10) no longer use it; it is still computed and frozen in
``calibrated.json`` as a shadow forecast, so the two can be compared later. Calls frozen for 2026-11-06 to
2026-11-09 keep ``claude_corrected_call_v1``.

From ANCHORED_FROM (2026-11-10) our call starts from the market (``market_anchored_call_v1``, owner's decision of
2026-11-09). On the 191 questions both priced, Claude alone scored a Brier of 0.2024 against the market's 0.1548, and
capping Claude's distance from the market at 0.10 would have scored 0.1712. So when a question has a market price
(``market_price_v1``, else ``manifold_price_v1``, captured before Claude forecast) the call is the price moved toward
Claude's forecast by at most MAX_MOVE, kept within CALL_BOUNDS. The call moves only on checked evidence: if the
question's ``research.json`` lists it under ``failed``, has no findings for it, or carries a warning on its findings (fewer than
two primary sources with quotes, or no source with a quote), the call is the market price unchanged. A question with
no market price keeps ``claude_call_v1`` (Claude's own forecast); with a price but no Claude forecast the call falls back
as ``claude_call_v1`` does. The anchoring happens only here, from the price already stored in ``forecasts.json``: the
forecasting step never sees it. Claude's independent forecast stays tracked and scored beside the call (its own row in
``forecasters``, ``summary.vs_market.claude`` and ``claude`` in ``summary.progress``), and every anchored call
records both numbers (``claude`` and ``market``). Because the call can differ from the market by at most 10 points,
"ours against the market" mostly measures those 10 points; "Claude alone against the market" is the test of
independent skill.

The headline score is the Brier score of our call over every decided, scored question
(``summary.brier``); ``summary.vs_market`` is the fair comparison with the market on the
questions it also priced, using the same call.

A question marked ``unscored`` (for example, a forecast that may have seen the market's
odds) stays on the page with its integrity note but counts in no score: not the headline
tally and not any forecaster's row. The mark is set before the outcome is known.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta
from itertools import accumulate
from pathlib import Path
from statistics import median
from typing import Any

from forecastlab.fast_questions import bond_market_holidays
from forecastlab.macro import SERIES, MacroSpec
from forecastlab.plain_questions import TOPICS, format_value, question_text, question_title
from forecastlab.timeutil import utcnow

TRACK_RECORD_VERSION = "track_record_v2"
COMBINED = "combined_median_v1"
WEIGHTED = "combined_weighted_v1"
WEIGHTED_FROM = date(2026, 10, 24)
CLAUDE_CALL = "claude_call_v1"
CLAUDE_CALL_FROM = date(2026, 11, 5)
CORRECTED_CALL = "claude_corrected_call_v1"
CORRECTED_CALL_FROM = date(2026, 11, 6)
# The call starts from the market price: the forward-walk test of 2026-11-09 found the correction worse out of sample,
# and Claude alone trailed the market (0.2024 against 0.1548 on 191 questions).
ANCHORED_CALL = "market_anchored_call_v1"
ANCHORED_FROM = date(2026, 11, 10)
# Largest distance the call may move from the market price toward Claude's forecast, and the range it stays within.
MAX_MOVE = 0.10
CALL_BOUNDS = (0.02, 0.98)
CALLS_FILE = "calls.json"
CALIBRATED = "claude_calibrated_v1"
CALIBRATED_FROM = date(2026, 10, 31)
# The research upgrade (5 research passes) applies to forecasts made from this day.
DEEPER_RESEARCH_FROM = date(2026, 11, 9)
# Milestones drawn on the "Are we improving?" chart, in plain words.
CHANGES = (
    {"date": WEIGHTED_FROM, "label": "Weighted by track record"},
    {"date": CORRECTED_CALL_FROM, "label": "Confidence correction"},
    {"date": DEEPER_RESEARCH_FROM, "label": "Deeper research"},
    {"date": ANCHORED_FROM, "label": "Call anchored to market"},
)
# "Before / after" splits forecasts made on or after this day against earlier ones.
PROGRESS_SPLIT = CORRECTED_CALL_FROM
CALIBRATED_FILE = "calibrated.json"
CLAUDE = "claude_code_forecaster_v1"
# No corrected forecast until this many decided Claude forecasts were known before the forecast day.
CALIBRATION_MIN_RESULTS = 30
# The fit is pulled toward "Claude is right as stated" (slope 1, intercept 0) as if by this many
# extra observations that all agree with Claude's stated probabilities.
CALIBRATION_PRIOR_RESULTS = 20
CALIBRATION_BOUNDS = (0.02, 0.98)
# A forecaster's Brier score is pulled toward a coin flip's as if it had this many extra
# coin-flip-quality results, so a short lucky or unlucky streak moves its weight only a little.
PRIOR_RESULTS = 10
SUPPLEMENT_SUFFIX = " (post-freeze supplement)"
COIN_FLIP_BRIER = 0.25
FORECASTERS: dict[str, dict[str, str]] = {
    "claude_code_forecaster_v1": {
        "label": "Claude alone", "about": "Claude researches each question on the web and states a probability, "
        "without seeing any market price. Tracked on its own beside our call; the test of independent skill."},
    "judge_nemotron_ultra_v1": {
        "label": "Nemotron Ultra (judge)", "about": "Free open-weights model (NVIDIA Nemotron 3 Ultra) that forecasts from Claude's research notes."},
    "judge_nemotron_super_v1": {
        "label": "Nemotron Super (judge)", "about": "Free open-weights model (NVIDIA Nemotron 3 Super) that forecasts from Claude's research notes."},
    "judge_deepseek_v4_pro_v1": {
        "label": "DeepSeek V4 Pro (judge)", "about": "Open-weights model (DeepSeek V4 Pro) that forecasts from Claude's research notes."},
    "judge_kimi_k26_v1": {
        "label": "Kimi K2.6 (judge)", "about": "Open-weights model (Moonshot AI Kimi K2.6) that forecasts from Claude's research notes."},
    "judge_qwen38_27b_v1": {
        "label": "Qwen 3.8 27B (judge)", "about": "Open-weights model (Alibaba Qwen3.8 27B) that forecasts from Claude's research notes."},
    "judge_minimax_m3_v1": {
        "label": "MiniMax M3 (judge)", "about": "Open-weights model (MiniMax M3) that forecasts from Claude's research notes."},
    "judge_claude_sonnet55_v1": {
        "label": "Claude Sonnet 5.5 (judge)", "about": "Anthropic's Claude Sonnet 5.5 that forecasts from the research notes. It sits out questions it researched itself."},
    "judge_gemma_v1": {
        "label": "Gemma (judge)", "about": "Free open-weights model (Google Gemma 4 31B) that forecasts from Claude's research notes."},
    "root_event_ensemble_v1": {
        "label": "Research pipeline", "about": "Three AI estimates built from one shared, checked evidence packet."},
    "single_model_forecaster_v1": {"label": "Single AI model", "about": "One AI model with web search."},
    "three_track_strict_forecaster_v1": {"label": "Three-track AI", "about": "Three separate AI analyses, combined."},
    "three_track_forecaster": {"label": "Three-track AI (early)", "about": "Earlier version of the three-track AI."},
    "statistical_baseline_v1": {
        "label": "Statistical baseline", "about": "A fixed rule based on past data, with no AI. The bar to beat."},
    CALIBRATED: {
        "label": "Claude, confidence-corrected",
        "about": "Claude's probability adjusted by how its past calls at each confidence level turned out. "
                 "Our call for questions first forecast 2026-11-06 to 2026-11-09; a tracked shadow since."},
    "market_price_v1": {
        "label": "Prediction market (benchmark)",
        "about": "Polymarket's price when we forecast. From 2026-11-10 our call starts from it and moves at most 10 points; "
                 "the toughest bar to beat."},
    "manifold_price_v1": {
        "label": "Manifold (benchmark)",
        "about": "Manifold's play-money price when we forecast (used when Polymarket has none). From 2026-11-10 our call "
                 "starts from it and moves at most 10 points."},
}
SERIES_LABELS = {"unemployment": "U.S. unemployment rate", "payrolls": "U.S. job growth",
                 "cpi": "U.S. inflation (CPI)", "jobless_claims": "Weekly U.S. jobless claims",
                 "treasury_10y": "10-year U.S. Treasury yield"}
# Benchmarks are scored like forecasters but never enter our call.
BENCHMARKS = ("market_price_v1", "manifold_price_v1")
# Scored like forecasters but never part of our call or of any forecaster's weight.
EXCLUDED = {*BENCHMARKS, CALIBRATED}
PRIMARY_ORDER = list(FORECASTERS)


def _forecaster(method: str) -> str:
    return method.removesuffix(SUPPLEMENT_SUFFIX)


COMBINED_INFO = {"label": "Combined forecast",
                 "about": "Our call: the middle of the forecasters' probabilities; from Oct 24 forecasters with "
                          "better records counted for more. Since Nov 5 it is Claude's own forecast, which "
                          "had beaten the combination. Since Nov 10 it starts from the market price and moves "
                          "at most 10 points toward Claude's forecast."}


def _call(forecasts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Our call: the median probability across the forecasters that answered (benchmarks excluded)."""
    members = [f["probability"] for f in forecasts if f["method"] not in EXCLUDED]
    if not members:
        return None
    return {"method": COMBINED, "probability": median(members), "members": len(members)}


def _weights(resolved: list[dict[str, Any]]) -> dict[str, float]:
    """Each forecaster's weight from its record so far: 1 / (shrunk Brier score) squared.

    The Brier score is shrunk toward a coin flip's 0.25 by PRIOR_RESULTS, so a forecaster with no
    record weighs 16, one with a long record at 0.15 about 40, and one no better than guessing 16 or less.
    """
    totals: dict[str, list[float]] = {}
    for question in resolved:
        for forecast in question["forecasts"]:
            if forecast["method"] in EXCLUDED:
                continue
            total = totals.setdefault(forecast["method"], [0, 0.0])
            total[0] += 1
            total[1] += _brier(forecast["probability"], question["outcome"])
    return {method: 1 / ((brier + PRIOR_RESULTS * COIN_FLIP_BRIER) / (count + PRIOR_RESULTS)) ** 2
            for method, (count, brier) in totals.items()}


def _weighted_call(forecasts: list[dict[str, Any]], weights: dict[str, float]) -> dict[str, Any] | None:
    """The weighted median: the probability where half the members' total weight lies on each side."""
    default = 1 / COIN_FLIP_BRIER ** 2
    members = sorted((f["probability"], round(weights.get(f["method"], default), 2), f["method"])
                     for f in forecasts if f["method"] not in EXCLUDED)
    if not members:
        return None
    half = sum(w for _, w, _ in members) / 2
    probability = next(p for (p, _, _), total in zip(members, accumulate(w for _, w, _ in members), strict=True) if total >= half)
    return {"method": WEIGHTED, "probability": probability, "members": len(members),
            "weights": {method: weight for _, weight, method in members}}


def _claude_call(forecasts: list[dict[str, Any]], weights: dict[str, float]) -> dict[str, Any] | None:
    """Claude's own probability as our call; the weighted call when Claude has no forecast on the question."""
    claude = next((f for f in forecasts if f["method"] == CLAUDE), None)
    if claude is None:
        return _weighted_call(forecasts, weights)
    return {"method": CLAUDE_CALL, "probability": claude["probability"], "members": 1}


def _market_price(forecasts: list[dict[str, Any]]) -> float | None:
    """The market's price (Polymarket's, else Manifold's) captured before Claude forecast, if the question has one."""
    return next((f["probability"] for method in BENCHMARKS for f in forecasts if f["method"] == method), None)


def _weak_research(research: dict[str, Any] | None, question_id: str) -> bool:
    """True when the question's research.json shows no checked evidence: failed, no findings, or a warning.

    A directory with no research.json has no findings at all, so its questions count as weak. The file is committed
    with the directory before the finish step runs (the download step writes it).
    """
    if research is None:
        return True
    findings = research.get("findings", {}).get(question_id)
    return question_id in research.get("failed", {}) or not findings or bool(findings.get("warnings"))


def _anchored_call(forecasts: list[dict[str, Any]], weights: dict[str, float],
                   research: dict[str, Any] | None = None, question_id: str = "") -> dict[str, Any] | None:
    """The market price moved toward Claude's forecast by at most MAX_MOVE; held at the price when the research is weak.

    No market price: Claude's own forecast. A price but no Claude forecast: whatever ``_claude_call`` does.
    """
    price = _market_price(forecasts)
    claude = next((f for f in forecasts if f["method"] == CLAUDE), None)
    if price is None or claude is None:
        return _claude_call(forecasts, weights)
    held = _weak_research(research, question_id)
    target = price if held else min(max(claude["probability"], price - MAX_MOVE), price + MAX_MOVE)
    low, high = CALL_BOUNDS
    call = {"method": ANCHORED_CALL, "probability": round(min(max(target, low), high), 6), "members": 1,
            "claude": claude["probability"], "market": price}
    return call | {"held": "weak research"} if held else call


def _corrected_call(forecasts: list[dict[str, Any]], weights: dict[str, float]) -> dict[str, Any] | None:
    """Claude's confidence-corrected probability as our call; Claude's own when no correction exists yet."""
    corrected = next((f for f in forecasts if f["method"] == CALIBRATED), None)
    if corrected is None:
        return _claude_call(forecasts, weights)
    return {"method": CORRECTED_CALL, "probability": corrected["probability"], "members": 1}


def _research(artifacts: Path, source: str, loaded: dict[str, dict[str, Any] | None]) -> dict[str, Any] | None:
    """A directory's research.json, read once; None when it has none."""
    if source not in loaded:
        path = artifacts / source / "research.json"
        loaded[source] = json.loads(path.read_text()) if path.exists() else None
    return loaded[source]


def _logit(p: float) -> float:
    p = min(max(p, CALIBRATION_BOUNDS[0]), CALIBRATION_BOUNDS[1])
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z))


def fit_confidence_correction(rows: list[tuple[float, int]]) -> tuple[float, float]:
    """(slope, intercept) of the logistic regression of outcome on logit(probability).

    The fit is pulled toward (1, 0), "the stated probabilities are right", by CALIBRATION_PRIOR_RESULTS
    pseudo-observations spread evenly from 5% to 95% whose outcome rate equals the stated probability.
    With no rows the answer is exactly (1, 0); with many it approaches the plain maximum-likelihood fit.
    """
    n = CALIBRATION_PRIOR_RESULTS
    data = [(_logit(p), float(o)) for p, o in rows]
    data += [(_logit(q), q) for q in (0.05 + 0.9 * k / (n - 1) for k in range(n))]

    def log_likelihood(a: float, b: float) -> float:
        total = 0.0
        for x, y in data:
            z = a + b * x
            total += y * z - math.log1p(math.exp(z)) if z < 30 else y * z - z
        return total

    a, b = 0.0, 1.0
    for _ in range(100):  # Newton's method on a concave function, with step halving as a safeguard
        ga = gb = haa = hab = hbb = 0.0
        for x, y in data:
            p = _sigmoid(a + b * x)
            w = p * (1 - p)
            ga += y - p
            gb += (y - p) * x
            haa += w
            hab += w * x
            hbb += w * x * x
        det = haa * hbb - hab * hab
        da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
        step, base = 1.0, log_likelihood(a, b)
        while step > 1e-6 and log_likelihood(a + step * da, b + step * db) < base:
            step /= 2
        a, b = a + step * da, b + step * db
        if abs(da) + abs(db) < 1e-10:
            break
    return b, a


def correct_confidence(probability: float, slope: float, intercept: float) -> float:
    low, high = CALIBRATION_BOUNDS
    return min(max(_sigmoid(intercept + slope * _logit(probability)), low), high)


def _claude_results_before(questions: list[dict[str, Any]], day: date) -> list[tuple[float, int]]:
    """Claude's scored forecasts on questions decided before ``day``: what was known when it forecast."""
    return [(f["probability"], q["outcome"]) for q in questions
            if q["status"] == "resolved" and q["scored"] and q["resolves_on"] < day.isoformat()
            for f in q["forecasts"] if f["method"] == CLAUDE]


def _correction(questions: list[dict[str, Any]], day: date) -> dict[str, Any] | None:
    """The fit as of ``day``, or None until CALIBRATION_MIN_RESULTS results were known."""
    rows = _claude_results_before(questions, day)
    if len(rows) < CALIBRATION_MIN_RESULTS:
        return None
    slope, intercept = fit_confidence_correction(rows)
    return {"slope": round(slope, 6), "intercept": round(intercept, 6), "results": len(rows), "as_of": day.isoformat()}


def _forecast_day(question: dict[str, Any]) -> date | None:
    """The day a question was first forecast, from its directory name (prospective_<kind>_YYYYMMDD)."""
    found = re.search(r"_(\d{8})$", question["source"])
    return datetime.strptime(found.group(1), "%Y%m%d").date() if found else None


def verdict(probability: float, outcome: int | None) -> str | None:
    """'right', 'wrong', 'toss_up', or None while the question is open."""
    if outcome is None:
        return None
    if probability == 0.5:
        return "toss_up"
    return "right" if (probability > 0.5) == bool(outcome) else "wrong"


def _reasoning(cell: dict[str, Any]) -> dict[str, Any]:
    """The forecaster's own explanation, when it recorded one."""
    keep = {"rationale": cell.get("rationale"), "sources": cell.get("sources"),
            "made_at": cell.get("computed_at") or cell.get("created_at")}
    return {key: value for key, value in keep.items() if value}


def _macro_questions(directory: Path) -> list[dict[str, Any]]:
    scores = json.loads((directory / "scores.json").read_text())
    manifest = json.loads((directory / "frozen_manifest.json").read_text())["manifest"]
    explained: dict[tuple[str, str], dict[str, Any]] = {}
    for supplement_path in sorted(directory.glob("*_supplement.json")):
        supplement = json.loads(supplement_path.read_text())
        for cell in supplement["cells"]:
            explained[(cell["entry_id"], supplement["method"])] = _reasoning(cell)
    in_database = manifest.get("schema_version") == "autopilot_export_v1"
    questions = []
    for entry in manifest["entries"]:
        spec = MacroSpec.model_validate(entry["macro"])
        forecasts: dict[str, dict[str, Any]] = {}
        for cell in scores["cells"]:
            if cell["entry_id"] == entry["entry_id"] and cell["probability"] is not None:
                method = _forecaster(cell["method"])
                forecasts.setdefault(method, {"method": method, "probability": cell["probability"],
                                              **explained.get((entry["entry_id"], method), {})})
        result = scores["outcomes"].get(entry["entry_id"], {})
        status, outcome, actual = "pending", None, None
        if result.get("status") == "resolved":
            status, outcome, actual = "resolved", result["outcome"], format_value(spec.indicator, result["value"])
        elif spec.indicator == "treasury_10y":
            day = date.fromisoformat(spec.observation_period)
            if day in bond_market_holidays(day.year):
                status = "cancelled"
        questions.append({
            "id": entry["entry_id"], "title": question_title(spec), "detail": question_text(spec),
            "topic": TOPICS[spec.indicator], "series": spec.indicator, "series_label": SERIES_LABELS[spec.indicator],
            "resolves_on": spec.release_at.date().isoformat(),
            "status": status, "outcome": outcome, "actual": actual,
            "scored": True, "source_url": f"https://fred.stlouisfed.org/series/{SERIES[spec.indicator]['fred']}",
            "report_url": f"/forecasts/{entry['entry_id']}" if in_database else None,
            "forecasts": sorted(forecasts.values(), key=lambda f: PRIMARY_ORDER.index(f["method"])
                                if f["method"] in PRIMARY_ORDER else len(PRIMARY_ORDER)),
            "source": directory.name})
    return questions


def _general_questions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    questions = []
    for question in data["questions"]:
        forecasts = [{"method": f["method"], "probability": f["probability"], **_reasoning(f)}
                     for f in question["forecasts"] if f.get("probability") is not None]
        url = re.search(r"https?://\S+", question.get("resolution_source", ""))
        outcome = question.get("outcome")
        questions.append({
            "id": question["id"], "title": question["question"], "detail": question["resolution_criteria"],
            # The GPU set predates per-question topics.
            "topic": question.get("topic") or data.get("topic") or ("Tech" if "gpu" in path.parent.name else "Other"),
            "resolves_on": question["resolution_date"],
            "status": "cancelled" if question.get("cancelled") else "pending" if outcome is None else "resolved",
            "outcome": None if outcome is None else int(outcome), "actual": question.get("outcome_note"),
            "note": question.get("integrity_note"), "scored": not question.get("unscored", False),
            "source_url": url.group(0).rstrip(" ;,)") if url else None, "report_url": None,
            "forecasts": sorted(forecasts, key=lambda f: PRIMARY_ORDER.index(f["method"])
                                if f["method"] in PRIMARY_ORDER else len(PRIMARY_ORDER)),
            "source": path.parent.name})
    return questions


def _brier(probability: float, outcome: int) -> float:
    return (probability - outcome) ** 2


def _tally(rows: list[tuple[float, int]]) -> dict[str, Any]:
    marks = [verdict(p, o) for p, o in rows]
    briers = [_brier(p, o) for p, o in rows]
    return {"resolved": len(rows), "right": marks.count("right"), "wrong": marks.count("wrong"),
            "toss_ups": marks.count("toss_up"),
            "brier": sum(briers) / len(briers) if briers else None, "coin_flip_brier": COIN_FLIP_BRIER}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _claude_probability(question: dict[str, Any]) -> float | None:
    return next((f["probability"] for f in question["forecasts"] if f["method"] == CLAUDE), None)


def _versus_market(resolved: list[dict[str, Any]]) -> dict[str, Any]:
    """Our call against the market's price (Polymarket's, else Manifold's) on the questions it priced.

    ``claude`` is Claude's own forecast on the same questions (``claude_questions`` of them have one), and
    ``since_anchored`` repeats all three on the questions first forecast from ANCHORED_FROM that have both a market
    price and a Claude forecast: there the call can differ from the market by at most MAX_MOVE, so ``ours`` against
    ``market`` mostly measures that cap, and ``claude`` against ``market`` is the test of independent skill.
    """
    ours: list[float] = []
    market: list[float] = []
    claude: list[float] = []
    anchored: list[tuple[float, float, float]] = []
    for q in resolved:
        price = _market_price(q["forecasts"])
        if price is None:
            continue
        ours.append(_brier(q["call"]["probability"], q["outcome"]))
        market.append(_brier(price, q["outcome"]))
        raw = _claude_probability(q)
        if raw is not None:
            claude.append(_brier(raw, q["outcome"]))
            if (day := _forecast_day(q)) and day >= ANCHORED_FROM:
                anchored.append((ours[-1], claude[-1], market[-1]))
    return {"questions": len(ours), "ours": _mean(ours), "market": _mean(market),
            "claude": _mean(claude), "claude_questions": len(claude),
            "since_anchored": {"date": ANCHORED_FROM.isoformat(), "questions": len(anchored),
                               "ours": _mean([r[0] for r in anchored]), "claude": _mean([r[1] for r in anchored]),
                               "market": _mean([r[2] for r in anchored])}}


def _progress(resolved: list[dict[str, Any]]) -> dict[str, Any]:
    """Our call against the market by the week (Monday, UTC) each question was first forecast.

    Only decided questions the market priced count. ``since_last_change`` splits them at PROGRESS_SPLIT.
    """
    weeks: dict[date, list[tuple[float, float, str | None, float | None]]] = {}
    split: dict[str, list[tuple[float, float, float | None]]] = {"after": [], "before": []}
    for q in resolved:
        day = _forecast_day(q)
        price = _market_price(q["forecasts"])
        if day is None or price is None:
            continue
        ours, theirs = _brier(q["call"]["probability"], q["outcome"]), _brier(price, q["outcome"])
        raw = _claude_probability(q)
        alone = None if raw is None else _brier(raw, q["outcome"])
        weeks.setdefault(day - timedelta(days=day.weekday()), []).append((ours, theirs, q["verdict"], alone))
        split["after" if day >= PROGRESS_SPLIT else "before"].append((ours, theirs, alone))
    return {
        "weeks": [{"start": start.isoformat(), "questions": len(rows),
                   "ours": _mean([r[0] for r in rows]), "market": _mean([r[1] for r in rows]),
                   "claude": _mean([r[3] for r in rows if r[3] is not None]),
                   "right": sum(r[2] == "right" for r in rows), "wrong": sum(r[2] == "wrong" for r in rows)}
                  for start, rows in sorted(weeks.items())],
        "changes": [{"date": c["date"].isoformat(), "label": c["label"]} for c in CHANGES],
        "since_last_change": {
            "date": PROGRESS_SPLIT.isoformat(),
            **{key: {"questions": len(rows), "ours": _mean([r[0] for r in rows]), "market": _mean([r[1] for r in rows]),
                     "claude": _mean([r[2] for r in rows if r[2] is not None])}
               for key, rows in split.items()}},
    }


def build_track_record(artifacts: Path, *, today: date | None = None, freeze: bool = False) -> dict[str, Any]:
    """The track record; with ``freeze``, new weighted calls are saved so they never change later."""
    questions: list[dict[str, Any]] = []
    for directory in sorted(artifacts.glob("prospective_*")):
        if (directory / "scores.json").exists() and (directory / "frozen_manifest.json").exists():
            questions += _macro_questions(directory)
        elif (directory / "forecasts.json").exists():
            questions += _general_questions(directory / "forecasts.json")
    frozen: dict[str, dict[str, dict[str, Any]]] = {}
    for question in questions:
        path = artifacts / question["source"] / CALLS_FILE
        if question["source"] not in frozen:
            frozen[question["source"]] = json.loads(path.read_text()) if path.exists() else {}
    research_files: dict[str, dict[str, Any] | None] = {}
    record_so_far = [q for q in questions if q["status"] == "resolved" and q["scored"]]
    weights = _weights(record_so_far)
    # Claude's confidence-corrected forecast: frozen the first time it can be made, never for earlier days.
    corrected: dict[str, dict[str, dict[str, Any]]] = {}
    fits: dict[date, dict[str, Any] | None] = {}
    for question in list(questions):
        day = _forecast_day(question)
        claude = next((f for f in question["forecasts"] if f["method"] == CLAUDE), None)
        if day is None or day < CALIBRATED_FROM or claude is None:
            continue
        path = artifacts / question["source"] / CALIBRATED_FILE
        saved = corrected.setdefault(question["source"], json.loads(path.read_text()) if path.exists() else {})
        if question["id"] not in saved:
            if day not in fits:
                fits[day] = _correction(questions, day)
            if fit := fits[day]:
                saved[question["id"]] = {
                    "probability": round(correct_confidence(claude["probability"], fit["slope"], fit["intercept"]), 6),
                    "raw": claude["probability"], **fit}
        if question["id"] in saved:
            question["forecasts"].append({"method": CALIBRATED, **saved[question["id"]]})
            question["forecasts"].sort(key=lambda f: PRIMARY_ORDER.index(f["method"])
                                       if f["method"] in PRIMARY_ORDER else len(PRIMARY_ORDER))
    for question in questions:
        day = _forecast_day(question)
        if day is None or day < WEIGHTED_FROM:
            call = _call(question["forecasts"])
        else:
            calls = frozen[question["source"]]
            pick = ((lambda forecasts, weights, q=question: _anchored_call(
                        forecasts, weights, _research(artifacts, q["source"], research_files), q["id"]))
                    if day >= ANCHORED_FROM
                    else _corrected_call if day >= CORRECTED_CALL_FROM
                    else _claude_call if day >= CLAUDE_CALL_FROM else _weighted_call)
            if question["id"] not in calls and (fresh := pick(question["forecasts"], weights)):
                calls[question["id"]] = fresh
            call = calls.get(question["id"])
        question["call"] = call
        question["verdict"] = verdict(call["probability"], question["outcome"]) if call else None
    if freeze:
        for source, calls in frozen.items():
            path = artifacts / source / CALLS_FILE
            if calls and (not path.exists() or json.loads(path.read_text()) != calls):
                path.write_text(json.dumps(calls, indent=2, sort_keys=True) + "\n")
        for source, saved in corrected.items():
            path = artifacts / source / CALIBRATED_FILE
            if saved and (not path.exists() or json.loads(path.read_text()) != saved):
                path.write_text(json.dumps(saved, indent=2, sort_keys=True) + "\n")
    resolved = [q for q in questions if q["status"] == "resolved" and q["call"] and q["scored"]]
    summary = _tally([(q["call"]["probability"], q["outcome"]) for q in resolved])
    summary |= {"vs_market": _versus_market(resolved), "progress": _progress(resolved)}
    summary |= {"pending": sum(q["status"] == "pending" for q in questions),
                "cancelled": sum(q["status"] == "cancelled" for q in questions),
                "unscored": sum(not q["scored"] for q in questions), "total": len(questions)}
    forecasters = [{"method": COMBINED, **COMBINED_INFO, "forecasts": sum(q["call"] is not None for q in questions),
                    **_tally([(q["call"]["probability"], q["outcome"]) for q in resolved])}]
    for method in sorted({f["method"] for q in questions for f in q["forecasts"]},
                         key=lambda m: PRIMARY_ORDER.index(m) if m in PRIMARY_ORDER else len(PRIMARY_ORDER)):
        rows = [(f["probability"], q["outcome"]) for q in resolved for f in q["forecasts"] if f["method"] == method]
        info = FORECASTERS.get(method, {"label": method, "about": ""})
        forecasters.append({"method": method, **info,
                            "forecasts": sum(f["method"] == method for q in questions for f in q["forecasts"]),
                            **_tally(rows)})
    # Latest results first, then open questions by the date they resolve.
    questions = (sorted((q for q in questions if q["status"] == "resolved"), key=lambda q: q["resolves_on"], reverse=True)
                 + sorted((q for q in questions if q["status"] != "resolved"), key=lambda q: q["resolves_on"]))
    built_on = today or utcnow().date()
    return {"version": TRACK_RECORD_VERSION, "generated_on": built_on.isoformat(),
            "summary": summary, "calibration": _correction(questions, built_on),
            "forecasters": forecasters, "questions": questions}
