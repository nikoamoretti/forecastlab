"""Baseline models for the country-month panel.

* Climatology: each country's historical frequency of meeting each threshold, smoothed
  with a Jeffreys prior, ``(hits + 0.5) / (months + 1)``; its count distribution is the
  country's empirical distribution of ``log1p(fatalities)``.
* Persistence: the origin month's state, as a 0/1 probability and a point count.
* Logistic baseline: one L2-penalised logistic regression per threshold and horizon on
  standardised features, made monotone across thresholds with a running minimum. Its
  count forecast is a mixture over the threshold bins: zero with probability
  ``1 - P(>=1)``, and inside each bin a ridge-regression location plus the training
  residual quantiles, clipped to the bin. The mixture's bin probabilities therefore
  equal the threshold probabilities exactly.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LOGIT_CLIP = 35.0


def sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -LOGIT_CLIP, LOGIT_CLIP)))


def _penalised_loglik(design: np.ndarray, target: np.ndarray, beta: np.ndarray, penalty: np.ndarray) -> float:
    eta = np.clip(design @ beta, -LOGIT_CLIP, LOGIT_CLIP)
    loglik = float(np.sum(target * eta - np.logaddexp(0.0, eta)))
    return loglik - 0.5 * float(np.sum(penalty * beta * beta))


def fit_logistic(
    design: np.ndarray,
    target: np.ndarray,
    *,
    l2: float = 1.0,
    beta0: np.ndarray | None = None,
    max_iter: int = 100,
    tol: float = 1e-8,
) -> np.ndarray:
    """Newton-Raphson fit of an L2-penalised logistic regression.

    ``design`` must include an intercept in column 0, which is not penalised.
    """
    n_features = design.shape[1]
    penalty = np.full(n_features, float(l2))
    penalty[0] = 0.0
    beta = np.zeros(n_features) if beta0 is None else np.array(beta0, dtype=np.float64)
    if beta0 is None:
        rate = float(np.clip(target.mean(), 1e-6, 1 - 1e-6)) if target.size else 0.5
        beta[0] = np.log(rate / (1 - rate))
    objective = _penalised_loglik(design, target, beta, penalty)
    for _ in range(max_iter):
        prob = sigmoid(design @ beta)
        weight = prob * (1.0 - prob)
        gradient = design.T @ (target - prob) - penalty * beta
        hessian = (design * weight[:, None]).T @ design + np.diag(penalty) + 1e-9 * np.eye(n_features)
        step = np.linalg.solve(hessian, gradient)
        scale = 1.0
        for _halving in range(30):
            candidate = beta + scale * step
            candidate_objective = _penalised_loglik(design, target, candidate, penalty)
            if candidate_objective >= objective - 1e-12:
                break
            scale *= 0.5
        beta = candidate
        objective = candidate_objective
        if np.max(np.abs(scale * step)) < tol:
            break
    return beta


def fit_ridge(design: np.ndarray, target: np.ndarray, *, l2: float = 1.0) -> np.ndarray:
    penalty = np.full(design.shape[1], float(l2))
    penalty[0] = 0.0
    return np.linalg.solve(design.T @ design + np.diag(penalty) + 1e-9 * np.eye(design.shape[1]), design.T @ target)


def enforce_monotone(probabilities: np.ndarray) -> np.ndarray:
    """Make ``P(>=k)`` non-increasing in the threshold (columns ordered by threshold)."""
    return np.minimum.accumulate(np.clip(probabilities, 0.0, 1.0), axis=1)


def mid_quantile_levels(count: int) -> np.ndarray:
    return (np.arange(count) + 0.5) / count


def log1p_bin_edges(thresholds: tuple[int, ...]) -> list[tuple[float, float]]:
    """``[log1p(k_b), log1p(k_{b+1} - 1)]`` for each threshold bin; the last bin is open."""
    edges = []
    for b, low in enumerate(thresholds):
        high = np.log1p(thresholds[b + 1] - 1) if b + 1 < len(thresholds) else np.inf
        edges.append((float(np.log1p(low)), float(high)))
    return edges


@dataclass
class Standardizer:
    mean: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, features: np.ndarray) -> Standardizer:
        mean = features.mean(axis=0)
        scale = features.std(axis=0)
        scale = np.where(scale > 1e-9, scale, 1.0)
        return cls(mean=mean, scale=scale)

    def design(self, features: np.ndarray) -> np.ndarray:
        standardised = (features - self.mean) / self.scale
        return np.column_stack([np.ones(len(features)), standardised])


@dataclass
class HorizonModel:
    """Logistic threshold models and the binned count distribution for one horizon."""

    horizon: int
    thresholds: tuple[int, ...]
    standardizer: Standardizer
    logit_coefs: np.ndarray
    bin_coefs: np.ndarray
    bin_residual_quantiles: np.ndarray
    bin_edges: list[tuple[float, float]]
    n_train: int
    train_positives: tuple[int, ...]

    def raw_probabilities(self, features: np.ndarray) -> np.ndarray:
        design = self.standardizer.design(features)
        return sigmoid(design @ self.logit_coefs.T)

    def probabilities(self, features: np.ndarray) -> np.ndarray:
        return enforce_monotone(self.raw_probabilities(features))

    def count_distribution(self, features: np.ndarray, probabilities: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Atoms and weights of the predictive distribution of ``log1p(fatalities)``."""
        design = self.standardizer.design(features)
        n_rows = len(features)
        n_atoms = self.bin_residual_quantiles.shape[1]
        upper = np.column_stack([probabilities[:, 1:], np.zeros(n_rows)])
        bin_mass = np.clip(probabilities - upper, 0.0, 1.0)
        atoms = [np.zeros((n_rows, 1))]
        weights = [np.clip(1.0 - probabilities[:, :1], 0.0, 1.0)]
        for b, (low, high) in enumerate(self.bin_edges):
            location = design @ self.bin_coefs[b]
            values = np.clip(location[:, None] + self.bin_residual_quantiles[b][None, :], low, high)
            atoms.append(values)
            weights.append(np.repeat(bin_mass[:, b : b + 1] / n_atoms, n_atoms, axis=1))
        return np.concatenate(atoms, axis=1), np.concatenate(weights, axis=1)


def fit_horizon_model(
    features: np.ndarray,
    outcome: np.ndarray,
    *,
    horizon: int,
    thresholds: tuple[int, ...],
    l2: float = 1.0,
    bin_atoms: int = 20,
    warm_start: HorizonModel | None = None,
) -> HorizonModel:
    """Fit all threshold models and bin regressions on training rows for one horizon."""
    if thresholds[0] != 1 or list(thresholds) != sorted(set(thresholds)):
        raise ValueError("thresholds must be increasing and start at 1")
    if len(features) == 0:
        raise ValueError("no training rows")
    standardizer = Standardizer.fit(features)
    design = standardizer.design(features)
    n_coef = design.shape[1]
    logit = np.zeros((len(thresholds), n_coef))
    positives = []
    warm = warm_start.logit_coefs if warm_start is not None and warm_start.logit_coefs.shape == logit.shape else None
    for k, threshold in enumerate(thresholds):
        target = (outcome >= threshold).astype(np.float64)
        positives.append(int(target.sum()))
        logit[k] = fit_logistic(design, target, l2=l2, beta0=None if warm is None else warm[k])

    edges = log1p_bin_edges(thresholds)
    levels = mid_quantile_levels(bin_atoms)
    log_outcome = np.log1p(outcome)
    bin_coefs = np.zeros((len(thresholds), n_coef))
    residual_quantiles = np.zeros((len(thresholds), bin_atoms))
    for b, threshold in enumerate(thresholds):
        upper = thresholds[b + 1] if b + 1 < len(thresholds) else np.inf
        in_bin = (outcome >= threshold) & (outcome < upper)
        n_bin = int(in_bin.sum())
        if n_bin == 0:
            bin_coefs[b, 0] = edges[b][0]
            continue
        if n_bin >= 2 * n_coef:
            coef = fit_ridge(design[in_bin], log_outcome[in_bin], l2=l2)
        else:
            coef = np.zeros(n_coef)
            coef[0] = float(log_outcome[in_bin].mean())
        bin_coefs[b] = coef
        residuals = log_outcome[in_bin] - design[in_bin] @ coef
        residual_quantiles[b] = np.quantile(residuals, levels)
    return HorizonModel(
        horizon=horizon,
        thresholds=tuple(thresholds),
        standardizer=standardizer,
        logit_coefs=logit,
        bin_coefs=bin_coefs,
        bin_residual_quantiles=residual_quantiles,
        bin_edges=edges,
        n_train=len(features),
        train_positives=tuple(positives),
    )


def climatology_probabilities(
    series: np.ndarray, valid: np.ndarray, origin_index: int, thresholds: tuple[int, ...], *, prior: float = 0.5
) -> np.ndarray:
    """``(hits + prior) / (months + 2 * prior)`` over each country's months up to the origin."""
    window = valid[:, : origin_index + 1]
    values = series[:, : origin_index + 1]
    months = window.sum(axis=1).astype(np.float64)
    columns = [((values >= threshold) & window).sum(axis=1) for threshold in thresholds]
    hits = np.stack(columns, axis=1).astype(np.float64)
    return (hits + prior) / (months[:, None] + 2 * prior)


def climatology_count_atoms(series: np.ndarray, valid: np.ndarray, origin_index: int, n_atoms: int) -> np.ndarray:
    """Equal-weight quantile atoms of each country's empirical ``log1p(fatalities)`` history."""
    levels = mid_quantile_levels(n_atoms)
    atoms = np.zeros((series.shape[0], n_atoms))
    for c in range(series.shape[0]):
        history = np.log1p(series[c, : origin_index + 1][valid[c, : origin_index + 1]])
        if history.size:
            atoms[c] = np.quantile(history, levels, method="inverted_cdf")
    return atoms


def persistence_probabilities(series: np.ndarray, origin_index: int, thresholds: tuple[int, ...]) -> np.ndarray:
    current = series[:, origin_index]
    return np.stack([(current >= threshold).astype(np.float64) for threshold in thresholds], axis=1)


def weighted_quantiles(atoms: np.ndarray, weights: np.ndarray, levels: tuple[float, ...] | np.ndarray) -> np.ndarray:
    """Quantiles of row-wise discrete distributions; returns shape ``(n_rows, n_levels)``."""
    order = np.argsort(atoms, axis=1, kind="stable")
    sorted_atoms = np.take_along_axis(atoms, order, axis=1)
    cumulative = np.cumsum(np.take_along_axis(weights, order, axis=1), axis=1)
    cumulative /= np.maximum(cumulative[:, -1:], 1e-12)
    out = np.empty((atoms.shape[0], len(levels)))
    for j, level in enumerate(levels):
        index = np.sum(cumulative < level - 1e-12, axis=1)
        index = np.minimum(index, atoms.shape[1] - 1)
        out[:, j] = sorted_atoms[np.arange(atoms.shape[0]), index]
    return out


def weighted_mean(atoms: np.ndarray, weights: np.ndarray) -> np.ndarray:
    return np.sum(atoms * weights, axis=1) / np.maximum(np.sum(weights, axis=1), 1e-12)
