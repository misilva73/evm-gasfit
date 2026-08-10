"""NNLS regression with bootstrap inference."""

from __future__ import annotations

import contextlib

import numpy as np
import pandas as pd
from scipy.optimize import nnls

from .results import NNLSResults


def fit_nnls(
    feature_df: pd.DataFrame,
    features: list[str],
    target: str,
    n_bootstrap: int = 1000,
    random_seed: int = 42,
) -> NNLSResults:
    """Fit a non-negative least squares model with bootstrap inference.

    The intercept is fitted alongside every other coefficient under the same
    non-negativity constraint: a column of ones is prepended to the design
    matrix and passed through ``scipy.optimize.nnls``. Bootstrap iterations that
    raise leave a row of NaNs in the coefficient matrix; ``NNLSResults`` filters
    those rows out before computing std errors, confidence intervals, and
    p-values.

    Args:
        feature_df: Frame containing the regressors and the target column.
        features: Regressor column names; ``"const"`` is added internally.
        target: Name of the response column in ``feature_df``.
        n_bootstrap: Number of bootstrap resamples used for inference.
        random_seed: Seed threaded into ``numpy.random.default_rng`` so the
            bootstrap is reproducible across runs and platforms.

    Returns:
        Results object exposing ``params``, ``pvalues``, ``conf_int``,
        ``rsquared``, ``rsquared_adj``, ``nobs``, ``fittedvalues``, ``resid``,
        and ``summary``.

    Raises:
        ValueError: If ``feature_df`` is empty, or the target / feature columns
            are missing.
    """
    if feature_df.empty:
        raise ValueError("feature_df cannot be empty")
    if target not in feature_df.columns:
        raise ValueError(f"feature_df must contain target column {target!r}")
    missing = [f for f in features if f not in feature_df.columns]
    if missing:
        raise ValueError(f"features not found in feature_df: {missing}")

    X = feature_df[features].to_numpy(dtype=float)
    y = feature_df[target].to_numpy(dtype=float)
    X_with_const = np.column_stack([np.ones(len(X)), X])
    feature_names = ["const"] + list(features)

    coefficients, residual_norm = nnls(X_with_const, y)

    rng = np.random.default_rng(random_seed)
    n = len(y)
    n_features = X_with_const.shape[1]
    # NaN-initialized so failed iterations are distinguishable from a legitimate
    # bootstrap draw of zero (which is the NNLS boundary). Downstream inference
    # in NNLSResults filters NaN rows out of std-error / percentile / p-value.
    bootstrap_coefs = np.full((n_bootstrap, n_features), np.nan)
    # Pre-draw all resample indices so seeding stays deterministic regardless
    # of how many iterations fail mid-fit.
    all_indices = rng.integers(0, n, size=(n_bootstrap, n))
    for i in range(n_bootstrap):
        idx = all_indices[i]
        with contextlib.suppress(Exception):
            coef_boot, _ = nnls(X_with_const[idx], y[idx])
            bootstrap_coefs[i] = coef_boot

    return NNLSResults(
        X=X_with_const,
        y=y,
        y_name=target,
        coefficients=coefficients,
        bootstrap_coefs=bootstrap_coefs,
        feature_names=feature_names,
        residual_norm=residual_norm,
    )
