"""A small model that learns from the decided questions how far to trust the market's price and Claude's forecast.

It starts from "the market is right as stated" and moves away from that only as far as results support:

    logit(p) = intercept + slope * logit(market) + weight[topic] * (logit(claude) - logit(market))

``intercept`` learns whether yes happens more or less often than priced, ``slope`` whether prices are too timid
(above 1) or too bold (below 1), and ``weight[topic]`` how much of Claude's disagreement with the market to follow on
that topic: 0 ignores Claude, 1 is Claude's own forecast, below 0 leans away from Claude. Every knob is pulled toward
the market (intercept 0, slope 1, weights 0) by a ridge penalty of SHRINKAGE, so a handful of results changes little,
a topic with no results keeps weight 0, and with no results at all the model is the market's price.

The form and the penalty were chosen on 2026-11-10 on the 195 decided questions that had both a Claude forecast and a
market price. Fitted only on other result days (ten folds of the result days, repeated 20 times), it scored 0.148
against the market's 0.153 (difference -0.005, 90% interval by result day -0.013 to +0.002: promising, not proven).
A model with 13 knobs scored 0.128 on its own data and 0.158 on results it had not seen, so more knobs wait for more
results. The track record tests this model live, as a call rule, on questions forecast after it was registered.
"""

from __future__ import annotations

import math
from typing import TypedDict

# Probabilities are kept inside this range, on the way in (before the logit) and on the way out.
BOUNDS = (0.02, 0.98)
# Ridge penalty on each knob's squared distance from "the market is right as stated".
SHRINKAGE = 3.0
# (market price, Claude's probability, topic, outcome) of one decided question.
Row = tuple[float, float, str, int]


class LearnedFit(TypedDict):
    intercept: float
    slope: float
    weights: dict[str, float]


def _logit(p: float) -> float:
    p = min(max(p, BOUNDS[0]), BOUNDS[1])
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z))


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """The solution of ``matrix @ x = vector`` by Gaussian elimination with partial pivoting."""
    size = len(vector)
    rows = [row[:] + [value] for row, value in zip(matrix, vector, strict=True)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(rows[r][column]))
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for r in range(column + 1, size):
            factor = rows[r][column] / rows[column][column]
            for c in range(column, size + 1):
                rows[r][c] -= factor * rows[column][c]
    solution = [0.0] * size
    for r in reversed(range(size)):
        solution[r] = (rows[r][size] - sum(rows[r][c] * solution[c] for c in range(r + 1, size))) / rows[r][r]
    return solution


def fit_learned_model(rows: list[Row]) -> LearnedFit:
    """The penalised logistic fit over ``rows``; exactly the market (0, 1, no weights) when there are none.

    Topics are taken in sorted order and nothing is random, so the same rows always give the same fit.
    """
    topics = sorted({topic for _, _, topic, _ in rows})
    prior = [0.0, 1.0] + [0.0] * len(topics)
    data: list[tuple[list[float], float]] = []
    for market, claude, topic, outcome in rows:
        features = [1.0, _logit(market)] + [0.0] * len(topics)
        features[2 + topics.index(topic)] = _logit(claude) - _logit(market)
        data.append((features, float(outcome)))
    size = len(prior)

    def objective(knobs: list[float]) -> float:
        total = -0.5 * SHRINKAGE * sum((knob - start) ** 2 for knob, start in zip(knobs, prior, strict=True))
        for features, outcome in data:
            z = sum(knob * feature for knob, feature in zip(knobs, features, strict=True))
            total += outcome * z - (math.log1p(math.exp(z)) if z < 30 else z)
        return total

    knobs = prior[:]
    for _ in range(100):  # Newton's method on a concave function, with step halving as a safeguard
        gradient = [-SHRINKAGE * (knob - start) for knob, start in zip(knobs, prior, strict=True)]
        hessian = [[SHRINKAGE if i == j else 0.0 for j in range(size)] for i in range(size)]
        for features, outcome in data:
            p = _sigmoid(sum(knob * feature for knob, feature in zip(knobs, features, strict=True)))
            spread = p * (1 - p)
            used = [i for i, feature in enumerate(features) if feature]
            for i in used:
                gradient[i] += (outcome - p) * features[i]
                for j in used:
                    hessian[i][j] += spread * features[i] * features[j]
        step = _solve(hessian, gradient)
        length, base = 1.0, objective(knobs)
        while length > 1e-6 and objective([knob + length * move for knob, move in zip(knobs, step, strict=True)]) < base:
            length /= 2
        knobs = [knob + length * move for knob, move in zip(knobs, step, strict=True)]
        if sum(abs(move) for move in step) < 1e-10:
            break
    return {"intercept": knobs[0], "slope": knobs[1], "weights": dict(zip(topics, knobs[2:], strict=True))}


def learned_probability(market: float, claude: float, topic: str, fit: LearnedFit) -> float:
    """The model's probability for one question; a topic the fit has no weight for follows the price alone."""
    z = (fit["intercept"] + fit["slope"] * _logit(market)
         + fit["weights"].get(topic, 0.0) * (_logit(claude) - _logit(market)))
    return min(max(_sigmoid(z), BOUNDS[0]), BOUNDS[1])
