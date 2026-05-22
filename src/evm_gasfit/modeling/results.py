"""NNLS regression results wrapper."""

from __future__ import annotations

import numpy as np
import pandas as pd

_EPS = 1e-12


class NNLSResults:
    """Wrap an NNLS fit with a statsmodels-style read-only surface."""

    def __init__(
        self,
        X: np.ndarray,
        y: np.ndarray,
        y_name: str,
        coefficients: np.ndarray,
        bootstrap_coefs: np.ndarray,
        feature_names: list[str],
        residual_norm: float,
    ) -> None:
        self._X = X
        self._y = y
        self._dep_var = y_name
        self._coefficients = coefficients
        self._bootstrap_coefs = bootstrap_coefs
        self._feature_names = list(feature_names)
        self._residual_norm = residual_norm

        self._fittedvalues = X @ coefficients
        self._resid = y - self._fittedvalues

        ss_res = float(np.sum(self._resid**2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        self._rsquared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

        n = len(y)
        k = len(coefficients) - 1  # exclude intercept
        if n > k + 1:
            self._rsquared_adj = 1.0 - (1.0 - self._rsquared) * (n - 1) / (n - k - 1)
        else:
            self._rsquared_adj = self._rsquared

        self._rmse = float(np.sqrt(np.mean(self._resid**2)))
        self._mae = float(np.mean(np.abs(self._resid)))

        # Lazy caches.
        self._params_series: pd.Series | None = None
        self._pvalues_series: pd.Series | None = None
        self._std_errors: np.ndarray | None = None

    @property
    def params(self) -> pd.Series:
        if self._params_series is None:
            self._params_series = pd.Series(
                self._coefficients, index=self._feature_names
            )
        return self._params_series

    @property
    def pvalues(self) -> pd.Series:
        if self._pvalues_series is None:
            self._pvalues_series = pd.Series(
                self._bootstrap_pvalues(), index=self._feature_names
            )
        return self._pvalues_series

    @property
    def rsquared(self) -> float:
        return self._rsquared

    @property
    def rsquared_adj(self) -> float:
        return self._rsquared_adj

    @property
    def nobs(self) -> int:
        return len(self._y)

    @property
    def fittedvalues(self) -> np.ndarray:
        return self._fittedvalues

    @property
    def resid(self) -> np.ndarray:
        return self._resid

    def _bootstrap_pvalues(self) -> np.ndarray:
        # Cache std errs while we're walking the bootstrap matrix.
        self._std_errors = np.std(self._bootstrap_coefs, axis=0)
        coefs = np.asarray(self._coefficients)
        # Vectorized percentile-style p-value:
        #   constrained-to-zero coefficients → 1.0
        #   else → mean(bootstrap_coef <= eps)
        p_below = (self._bootstrap_coefs <= _EPS).mean(axis=0)
        zero_mask = coefs == 0
        return np.where(zero_mask, 1.0, p_below)

    def conf_int(self, alpha: float = 0.05) -> pd.DataFrame:
        lower = np.percentile(self._bootstrap_coefs, 100 * (alpha / 2), axis=0)
        upper = np.percentile(self._bootstrap_coefs, 100 * (1 - alpha / 2), axis=0)
        return pd.DataFrame({0: lower, 1: upper}, index=self._feature_names)

    def summary(self) -> str:
        # Touch pvalues so std errors are populated.
        pvals = self.pvalues
        ci = self.conf_int()
        width = 78
        lines: list[str] = []
        lines.append("=" * width)
        lines.append(f"{'NNLS Regression Results':^{width}}")
        lines.append("=" * width)
        lines.append(
            f"Dep. Variable:          {self._dep_var}"
            f"{'R-squared:':>{width - 54}}{self.rsquared:>15.3f}"
        )
        lines.append(
            f"Model:                  NNLS"
            f"{'Adj. R-squared:':>{width - 43}}{self.rsquared_adj:>15.3f}"
        )
        lines.append(
            f"No. Observations:       {self.nobs:<7}"
            f"{'RMSE:':>{width - 46}}{self._rmse:>15.2f}"
        )
        lines.append(
            f"Df Residuals:           {self.nobs - len(self._feature_names):<7}"
            f"{'MAE:':>{width - 46}}{self._mae:>15.2f}"
        )
        lines.append(f"Df Model:               {len(self._feature_names) - 1:<7}")
        lines.append("=" * width)
        lines.append(
            f"{'':>14}{'coef':>12}{'std err':>12}{'P-value':>12}"
            f"{'[0.025':>12}{'0.975]':>12}"
        )
        lines.append("-" * width)
        std_errs = self._std_errors
        assert std_errs is not None  # populated by pvalues access above
        for i, name in enumerate(self._feature_names):
            coef = self.params[name]
            pval = pvals[name]
            ci_low = ci.loc[name, 0]
            ci_high = ci.loc[name, 1]
            se = std_errs[i]
            lines.append(
                f"{name:>14}{coef:>12.4f}{se:>12.4f}{pval:>12.3f}"
                f"{ci_low:>12.4f}{ci_high:>12.4f}"
            )
        lines.append("=" * width)
        lines.append(
            "Notes: Non-negative least squares with bootstrap inference "
            f"({len(self._bootstrap_coefs)} iterations)"
        )
        lines.append("=" * width)
        return "\n".join(lines)
