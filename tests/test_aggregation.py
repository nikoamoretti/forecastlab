from forecastlab.aggregation import (
    aggregate_track_probabilities,
    clip,
    clip_aggregated_probability,
    clip_track_probability,
    inv_logit,
    logit,
)


def test_clip_and_logit_roundtrip() -> None:
    assert clip(1.5, 0.0, 1.0) == 1.0
    assert clip_track_probability(0.0) == 0.01
    assert clip_aggregated_probability(0.999) == 0.98
    p = 0.31
    assert abs(inv_logit(logit(p)) - p) < 1e-12


def test_equal_weight_logit_mean_with_shrinkage() -> None:
    result = aggregate_track_probabilities(
        {"base_rate": 0.42, "current_evidence": 0.31, "skeptic": 0.38},
        shrinkage=0.10,
        anchor_track="base_rate",
    )
    assert result.ensemble_probability is not None
    assert 0.02 <= result.ensemble_probability <= 0.98
    assert result.included_track_types == ["base_rate", "current_evidence", "skeptic"]
    assert result.method == "equal_weight_logit_shrinkage"
    # Hand-checked: pooled logit mean then 10% shrink toward 0.42.
    assert abs(result.ensemble_probability - 0.368) < 0.02


def test_failed_track_is_omitted() -> None:
    result = aggregate_track_probabilities(
        {"base_rate": 0.40, "current_evidence": None, "skeptic": 0.60},
        failed_tracks={"current_evidence": "json_invalid"},
    )
    assert result.missing_track_types == ["current_evidence"]
    assert result.ensemble_probability is not None
    assert result.ensemble_probability > 0.45
