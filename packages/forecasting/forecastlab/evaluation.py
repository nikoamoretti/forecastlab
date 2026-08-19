from __future__ import annotations

import math

from forecastlab.aggregation import clip

LOG_LOSS_CLIP = 1e-15
RELIABILITY_MIN_N = 20


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
    """Return a reliability diagram payload, or an insufficiency notice."""
    n = len(pairs)
    if n < min_n:
        return {
            "available": False,
            "sample_count": n,
            "minimum_required": min_n,
            "message": (
                f"Calibration cannot yet be estimated reliably. Need at least {min_n} resolved predictions; have {n}."
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
    return {"available": True, "sample_count": n, "bins": bins}
