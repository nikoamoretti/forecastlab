from __future__ import annotations

import math
import random

from forecastlab.aggregation import clip

LOG_LOSS_CLIP = 1e-15
RELIABILITY_MIN_N = 20
BOOTSTRAP_SEED = 20260818
BOOTSTRAP_SAMPLES = 2000
PAIRED_CI_MIN_N = 2
SIGNIFICANCE_MIN_N = 20


def brier_score(probability: float, outcome: int) -> float:
    if outcome not in (0, 1):
        raise ValueError("outcome must be 0 or 1")
    p = clip(float(probability), 0.0, 1.0)
    return (p - outcome) ** 2


def log_loss(probability: float, outcome: int) -> float:
    if outcome not in (0, 1):
        raise ValueError("outcome must be 0 or 1")
    p = clip(float(probability), LOG_LOSS_CLIP, 1.0 - LOG_LOSS_CLIP)
    if outcome == 1:
        return -math.log(p)
    return -math.log(1.0 - p)


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def reliability_bins(
    pairs: list[tuple[float, int]],
    *,
    bin_count: int = 10,
    min_n: int = RELIABILITY_MIN_N,
) -> dict[str, object]:
    """Return a reliability diagram payload, or an insufficiency notice.

    The 20-row threshold is a display gate only. It is not enough for a
    calibration claim.
    """
    n = len(pairs)
    if n < min_n:
        return {
            "available": False,
            "sample_count": n,
            "minimum_required": min_n,
            "message": (
                f"Reliability display needs at least {min_n} resolved predictions; have {n}. "
                "Twenty observations are not enough for a calibration claim."
            ),
        }
    bins: list[dict[str, float | int]] = []
    for i in range(bin_count):
        lo = i / bin_count
        hi = (i + 1) / bin_count
        members = [
            pair for pair in pairs if (pair[0] >= lo and pair[0] < hi) or (i == bin_count - 1 and pair[0] == 1.0)
        ]
        if not members:
            continue
        avg_p = sum(p for p, _ in members) / len(members)
        avg_y = sum(y for _, y in members) / len(members)
        bins.append(
            {
                "bin_start": lo,
                "bin_end": hi,
                "count": len(members),
                "mean_probability": avg_p,
                "empirical_frequency": avg_y,
            }
        )
    return {
        "available": True,
        "sample_count": n,
        "bins": bins,
        "message": "Display only. Twenty or more rows are still not a calibration claim.",
    }


def _percentile(ordered: list[float], q: float) -> float:
    if not ordered:
        raise ValueError("empty sample")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def bootstrap_mean_ci(
    values: list[float],
    *,
    seed: int = BOOTSTRAP_SEED,
    samples: int = BOOTSTRAP_SAMPLES,
) -> dict[str, object]:
    n = len(values)
    if n < PAIRED_CI_MIN_N:
        return {
            "available": False,
            "sample_count": n,
            "seed": seed,
            "samples": samples,
            "mean": mean(values),
            "low": None,
            "high": None,
            "message": "Too few paired observations for a bootstrap interval.",
        }
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(samples):
        draw = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(draw) / n)
    means.sort()
    low = _percentile(means, 0.025)
    high = _percentile(means, 0.975)
    estimate = mean(values)
    excludes_zero = low is not None and high is not None and (high < 0 or low > 0)
    return {
        "available": True,
        "sample_count": n,
        "seed": seed,
        "samples": samples,
        "mean": estimate,
        "low": low,
        "high": high,
        "excludes_zero": excludes_zero,
        "labeled_significant": bool(excludes_zero and n >= SIGNIFICANCE_MIN_N),
        "rule": "95% percentile interval on paired differences; labeled significant only if n>=20 and the interval excludes 0.",
    }


def paired_profile_comparison(
    left: dict[str, dict[str, float]],
    right: dict[str, dict[str, float]],
    *,
    left_id: str,
    right_id: str,
) -> dict[str, object]:
    common = sorted(set(left) & set(right))
    brier_left = [left[key]["brier"] for key in common]
    brier_right = [right[key]["brier"] for key in common]
    log_left = [left[key]["log_loss"] for key in common]
    log_right = [right[key]["log_loss"] for key in common]
    cost_left = [left[key]["cost_usd"] for key in common]
    cost_right = [right[key]["cost_usd"] for key in common]
    lat_left = [left[key]["latency_ms"] for key in common]
    lat_right = [right[key]["latency_ms"] for key in common]
    brier_diffs = [a - b for a, b in zip(brier_left, brier_right, strict=True)]
    wins = sum(1 for diff in brier_diffs if diff < 0)
    losses = sum(1 for diff in brier_diffs if diff > 0)
    ties = len(brier_diffs) - wins - losses
    interval = bootstrap_mean_ci(brier_diffs)
    return {
        "left_profile_id": left_id,
        "right_profile_id": right_id,
        "n": len(common),
        "mean_brier_left": mean(brier_left),
        "mean_brier_right": mean(brier_right),
        "mean_paired_brier_difference": mean(brier_diffs),
        "wins_left": wins,
        "ties": ties,
        "losses_left": losses,
        "mean_log_loss_difference": mean([a - b for a, b in zip(log_left, log_right, strict=True)]),
        "mean_cost_difference": mean([a - b for a, b in zip(cost_left, cost_right, strict=True)]),
        "mean_latency_difference": mean([a - b for a, b in zip(lat_left, lat_right, strict=True)]),
        "paired_brier_bootstrap": interval,
    }
