from __future__ import annotations

import math
from dataclasses import dataclass

MIN_TRACK_P = 0.01
MAX_TRACK_P = 0.99
MIN_AGG_P = 0.02
MAX_AGG_P = 0.98
DEFAULT_SHRINKAGE = 0.10
DEFAULT_ANCHOR = 0.50


def clip(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def clip_track_probability(probability: float) -> float:
    return clip(float(probability), MIN_TRACK_P, MAX_TRACK_P)


def clip_aggregated_probability(probability: float) -> float:
    return clip(float(probability), MIN_AGG_P, MAX_AGG_P)


def logit(probability: float) -> float:
    p = clip(float(probability), 1e-12, 1 - 1e-12)
    return math.log(p / (1.0 - p))


def inv_logit(value: float) -> float:
    if value >= 40:
        return 1.0
    if value <= -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


@dataclass(frozen=True)
class TrackContribution:
    track_type: str
    raw_probability: float
    clipped_probability: float
    logit: float
    included: bool
    failure_reason: str | None = None


@dataclass(frozen=True)
class AggregationBreakdown:
    method: str
    shrinkage: float
    anchor_probability: float
    anchor_logit: float
    included_track_types: list[str]
    missing_track_types: list[str]
    contributions: list[TrackContribution]
    pooled_logit: float | None
    shrunk_logit: float | None
    ensemble_probability: float | None
    track_spread: float | None
    formula: str = (
        "clip each successful track to [0.02, 0.98]; convert to logit; "
        "equal-weight mean; shrink in logit space toward the base-rate track "
        "(or 0.50 if that track failed); invert logit."
    )


def _spread(probabilities: list[float]) -> float | None:
    if len(probabilities) < 2:
        return 0.0 if probabilities else None
    return round(max(probabilities) - min(probabilities), 6)


def aggregate_track_probabilities(
    track_probabilities: dict[str, float | None],
    *,
    shrinkage: float = DEFAULT_SHRINKAGE,
    anchor_track: str = "base_rate",
    failed_tracks: dict[str, str] | None = None,
) -> AggregationBreakdown:
    """Deterministic equal-weight logit mean with optional shrinkage.

    Failed tracks are omitted. The LLM never chooses the final number.
    """
    failed_tracks = failed_tracks or {}
    contributions: list[TrackContribution] = []
    included: list[TrackContribution] = []

    for track_type, raw in track_probabilities.items():
        if raw is None or track_type in failed_tracks:
            contributions.append(
                TrackContribution(
                    track_type=track_type,
                    raw_probability=float("nan") if raw is None else float(raw),
                    clipped_probability=float("nan"),
                    logit=float("nan"),
                    included=False,
                    failure_reason=failed_tracks.get(track_type, "track_failed"),
                )
            )
            continue
        clipped = clip_aggregated_probability(clip_track_probability(raw))
        item = TrackContribution(
            track_type=track_type,
            raw_probability=float(raw),
            clipped_probability=clipped,
            logit=logit(clipped),
            included=True,
        )
        contributions.append(item)
        included.append(item)

    missing = [c.track_type for c in contributions if not c.included]
    included_types = [c.track_type for c in included]

    if not included:
        return AggregationBreakdown(
            method="equal_weight_logit_shrinkage",
            shrinkage=shrinkage,
            anchor_probability=DEFAULT_ANCHOR,
            anchor_logit=logit(DEFAULT_ANCHOR),
            included_track_types=[],
            missing_track_types=missing,
            contributions=contributions,
            pooled_logit=None,
            shrunk_logit=None,
            ensemble_probability=None,
            track_spread=None,
        )

    pooled = sum(c.logit for c in included) / len(included)
    anchor_raw = track_probabilities.get(anchor_track)
    if anchor_track in included_types and anchor_raw is not None:
        anchor_p = clip_aggregated_probability(clip_track_probability(anchor_raw))
    else:
        anchor_p = DEFAULT_ANCHOR
    anchor_l = logit(anchor_p)
    shrink = clip(float(shrinkage), 0.0, 1.0)
    shrunk = (1.0 - shrink) * pooled + shrink * anchor_l
    ensemble = clip_aggregated_probability(inv_logit(shrunk))
    return AggregationBreakdown(
        method="equal_weight_logit_shrinkage",
        shrinkage=shrink,
        anchor_probability=anchor_p,
        anchor_logit=anchor_l,
        included_track_types=included_types,
        missing_track_types=missing,
        contributions=contributions,
        pooled_logit=pooled,
        shrunk_logit=shrunk,
        ensemble_probability=ensemble,
        track_spread=_spread([c.clipped_probability for c in included]),
    )
