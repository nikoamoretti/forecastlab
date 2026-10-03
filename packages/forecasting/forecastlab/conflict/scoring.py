"""Proper scoring rules and calibration summaries for panel forecasts."""

from __future__ import annotations

from typing import Any

import numpy as np

# Log loss uses probabilities clipped to [eps, 1 - eps] so that 0/1 forecasts (persistence)
# get a large but finite penalty.
LOG_LOSS_EPS = 1e-4


def _as_arrays(probabilities: np.ndarray, outcomes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    p = np.asarray(probabilities, dtype=np.float64)
    y = np.asarray(outcomes, dtype=np.float64)
    if p.shape != y.shape:
        raise ValueError("probabilities and outcomes differ in shape")
    if p.size and (np.any(p < 0) or np.any(p > 1)):
        raise ValueError("probabilities must lie in [0, 1]")
    if y.size and not np.all((y == 0) | (y == 1)):
        raise ValueError("outcomes must be 0 or 1")
    return p, y


def brier_score(probabilities: np.ndarray, outcomes: np.ndarray) -> float:
    p, y = _as_arrays(probabilities, outcomes)
    return float(np.mean((p - y) ** 2)) if p.size else float("nan")


def log_loss(probabilities: np.ndarray, outcomes: np.ndarray, *, eps: float = LOG_LOSS_EPS) -> float:
    p, y = _as_arrays(probabilities, outcomes)
    if not p.size:
        return float("nan")
    clipped = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y * np.log(clipped) + (1 - y) * np.log(1 - clipped)))


def skill_score(score: float, reference: float) -> float | None:
    """``1 - score / reference`` (e.g. the Brier skill score); None when undefined."""
    if not np.isfinite(score) or not np.isfinite(reference) or reference <= 0:
        return None
    return float(1.0 - score / reference)


def roc_auc(probabilities: np.ndarray, outcomes: np.ndarray) -> float | None:
    """Area under the ROC curve (Mann-Whitney, ties counted as one half); None with one class."""
    p, y = _as_arrays(probabilities, outcomes)
    positives = int(y.sum())
    negatives = int(y.size - positives)
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(p, kind="mergesort")
    _, inverse, counts = np.unique(p[order], return_inverse=True, return_counts=True)
    ends = np.cumsum(counts)
    average_rank = (ends - counts + 1 + ends) / 2.0
    ranks = np.empty_like(p)
    ranks[order] = average_rank[inverse]
    return float((ranks[y == 1].sum() - positives * (positives + 1) / 2.0) / (positives * negatives))


def calibration_table(probabilities: np.ndarray, outcomes: np.ndarray, *, n_bins: int = 10) -> list[dict[str, Any]]:
    """Reliability table over equal-width probability bins [0, 0.1), ..., [0.9, 1.0]."""
    p, y = _as_arrays(probabilities, outcomes)
    index = np.minimum((p * n_bins).astype(np.int64), n_bins - 1)
    table = []
    for b in range(n_bins):
        members = index == b
        count = int(members.sum())
        table.append(
            {
                "bin": f"{b / n_bins:.1f}-{(b + 1) / n_bins:.1f}",
                "n": count,
                "mean_forecast": float(p[members].mean()) if count else None,
                "observed_rate": float(y[members].mean()) if count else None,
                "events": int(y[members].sum()),
            }
        )
    return table


def murphy_decomposition(probabilities: np.ndarray, outcomes: np.ndarray, *, n_bins: int = 10) -> dict[str, float]:
    """Binned Brier decomposition: reliability (lower is better), resolution, uncertainty."""
    p, y = _as_arrays(probabilities, outcomes)
    if not p.size:
        return {"reliability": float("nan"), "resolution": float("nan"), "uncertainty": float("nan")}
    base_rate = float(y.mean())
    reliability = resolution = 0.0
    for row in calibration_table(p, y, n_bins=n_bins):
        if row["n"]:
            weight = row["n"] / p.size
            reliability += weight * (row["mean_forecast"] - row["observed_rate"]) ** 2
            resolution += weight * (row["observed_rate"] - base_rate) ** 2
    return {"reliability": reliability, "resolution": resolution, "uncertainty": base_rate * (1 - base_rate)}


def crps_weighted_atoms(atoms: np.ndarray, weights: np.ndarray, observed: np.ndarray) -> np.ndarray:
    """Exact CRPS of row-wise discrete distributions: ``E|X - y| - E|X - X'| / 2``."""
    x = np.asarray(atoms, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    y = np.asarray(observed, dtype=np.float64)
    w = w / np.maximum(w.sum(axis=1, keepdims=True), 1e-12)
    expected_error = np.sum(w * np.abs(x - y[:, None]), axis=1)
    order = np.argsort(x, axis=1, kind="stable")
    xs = np.take_along_axis(x, order, axis=1)
    ws = np.take_along_axis(w, order, axis=1)
    cumulative = np.cumsum(ws, axis=1)
    half_spread = np.sum(ws * xs * (cumulative + (cumulative - ws) - 1.0), axis=1)
    return expected_error - half_spread


def binary_metrics(probabilities: np.ndarray, outcomes: np.ndarray) -> dict[str, Any]:
    p, y = _as_arrays(probabilities, outcomes)
    return {
        "n": int(p.size),
        "events": int(y.sum()),
        "observed_rate": float(y.mean()) if p.size else None,
        "mean_forecast": float(p.mean()) if p.size else None,
        "brier": brier_score(p, y),
        "log_loss": log_loss(p, y),
        "auc": roc_auc(p, y),
    }


def cluster_bootstrap_skill(
    score_terms: np.ndarray,
    reference_terms: np.ndarray,
    clusters: np.ndarray,
    *,
    replicates: int = 1000,
    seed: int = 20261002,
    level: float = 0.95,
) -> tuple[float, float] | None:
    """Percentile interval for ``1 - sum(score) / sum(reference)``, resampling whole clusters.

    Resampling countries keeps each country's correlated months together.
    """
    labels, inverse = np.unique(clusters, return_inverse=True)
    if labels.size < 2:
        return None
    score_by_cluster = np.bincount(inverse, weights=score_terms, minlength=labels.size)
    reference_by_cluster = np.bincount(inverse, weights=reference_terms, minlength=labels.size)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, labels.size, size=(replicates, labels.size))
    counts = np.apply_along_axis(np.bincount, 1, draws, minlength=labels.size)
    score_total = counts @ score_by_cluster
    reference_total = counts @ reference_by_cluster
    ok = reference_total > 0
    if not np.any(ok):
        return None
    skills = 1.0 - score_total[ok] / reference_total[ok]
    tail = (1.0 - level) / 2.0
    return float(np.quantile(skills, tail)), float(np.quantile(skills, 1.0 - tail))
